from contextlib import asynccontextmanager
import asyncio
import hashlib
import io
import logging
import os
import tempfile
from pathlib import Path

import httpx
from fastapi import FastAPI, HTTPException, Query
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, Response, StreamingResponse
from fastapi.staticfiles import StaticFiles

from .ccapi import CanonCamera, CameraUnreachable
from .config import Config, DATA_DIR, load_config, save_config
from .log import attach_uvicorn_to_file, setup_logging
from .state import State
from .sync import SyncEngine

setup_logging()
log = logging.getLogger("api")

state = State()
config = load_config()
engine = SyncEngine(config, state)


@asynccontextmanager
async def lifespan(app: FastAPI):
    attach_uvicorn_to_file()  # uvicorn 此时已完成自身日志配置，挂上文件 handler
    log.info(
        "服务启动：相机 %s:%s，备份目录 %s",
        config.camera_ip, config.camera_port, config.nas_path,
    )
    engine.start()
    yield
    engine.stop()
    # 关停前强制落盘：正常退出（docker stop 等）不丢失最近未写入的同步记录/错误状态
    await state.flush(force=True)
    log.info("服务已停止")


app = FastAPI(title="canon-nas", lifespan=lifespan)
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.get("/api/status")
def get_status():
    return engine.status()


@app.get("/api/config")
def get_config():
    return config.model_dump()


@app.post("/api/config")
async def update_config(cfg: Config):
    global config
    old_nas = config.nas_path
    save_config(cfg)
    config = cfg
    engine.config = cfg
    if Path(cfg.nas_path) != Path(old_nas):
        # 更换备份目录：清空已备份记录，相机上的文件下一轮同步会重新下载到新目录
        cleared = state.clear_synced()
        await state.flush(force=True)  # 路由线程外无事件循环调度，立即落盘
        engine.recount_pending()
        log.info("备份目录已更换：%s → %s，已清空 %d 条已备份记录，将重新同步", old_nas, cfg.nas_path, cleared)
    else:
        log.info("配置已更新：%s", cfg.model_dump_json())
    return cfg.model_dump()


@app.post("/api/sync")
async def trigger_sync():
    return await engine.sync_once()


@app.post("/api/sync/stop")
async def stop_sync():
    engine.request_stop()
    return {"ok": True}


@app.post("/api/reconnect")
async def reconnect_camera():
    ok = await engine.reconnect()
    return {"ok": ok, "camera_online": ok}


@app.get("/api/files")
def list_synced(offset: int = 0, limit: int = 100):
    # 快照在锁内复制：同步主循环会并发写 state.synced，直接遍历可能抛
    # RuntimeError（dictionary changed size during iteration）导致接口 500
    items = state.snapshot_synced()
    items.sort(key=lambda kv: kv[1]["synced_at"], reverse=True)
    total = len(items)
    page = items[offset : offset + limit]
    return {"total": total, "items": [{"path": p, **meta} for p, meta in page]}


@app.delete("/api/files")
def delete_synced(path: str = Query(...)):
    """删除一条已备份记录及其 NAS 上的文件。仅允许删除 nas_path 内的文件，防目录穿越。"""
    meta = state.remove_synced(path)
    if meta is None:
        raise HTTPException(404, "记录不存在")
    state.ignore(path)  # 相机上的原文件不再被自动同步传回
    engine.recount_pending()
    dest = meta.get("dest")
    deleted_file = False
    if dest:
        root = Path(config.nas_path).resolve()
        target = Path(dest).resolve()
        if root == target or root in target.parents:
            try:
                target.unlink(missing_ok=True)
                deleted_file = True
            except OSError as e:
                log.warning("删除备份文件失败 %s: %s", dest, e)
    return {"ok": True, "deleted_file": deleted_file}


@app.post("/api/files/restore")
def restore_ignored(path: str = Query(...)):
    """把文件移出忽略名单：待备份列表和自动同步会重新包含它。"""
    restored = state.unignore(path)
    engine.recount_pending()
    return {"ok": restored}


@app.get("/api/pending")
def list_pending():
    items = [
        {"path": p, "ignored": state.is_ignored(p)}
        for p in engine.camera_files if not state.is_synced(p)
    ]
    # 被忽略（手动删除备份）的排在最后，不打扰正常待备份浏览
    items.sort(key=lambda x: x["ignored"])
    return items


# 相机单连接处理能力弱：并发缩略图请求会触发 503，连续请求会逐渐卡死。
# 保护：信号量限制小并发窗口 + 请求失败重试 + 内存缓存（轮询刷新不再重复打相机）
# 再加浏览器缓存头：切页/刷新后命中本地缓存，不再请求后端
_thumb_sem = asyncio.Semaphore(2)
_thumb_cache: dict[str, tuple[bytes, str]] = {}
_CACHE_HEADERS = {"Cache-Control": "public, max-age=600"}

@app.get("/api/thumb")
async def thumbnail(path: str = Query(...)):
    cam: CanonCamera = engine.camera()
    cached = _thumb_cache.get(path)
    if cached:
        return StreamingResponse(iter([cached[0]]), media_type=cached[1], headers=_CACHE_HEADERS)
    try:
        async with httpx.AsyncClient(timeout=10) as client:  # 重试复用同一连接
            for attempt in range(2):
                async with _thumb_sem:
                    cached = _thumb_cache.get(path)
                    if cached:
                        return StreamingResponse(iter([cached[0]]), media_type=cached[1], headers=_CACHE_HEADERS)
                    resp = await client.get(cam.thumb_url(path))
                if resp.status_code == 503 and attempt == 0:
                    # 退避在信号量外等待，重试期间不占用并发槽
                    await asyncio.sleep(1)
                    continue
                resp.raise_for_status()
                if len(_thumb_cache) > 200:
                    # 只清最旧的一半，保留近期热图
                    for key in list(_thumb_cache)[: len(_thumb_cache) // 2]:
                        del _thumb_cache[key]
                _thumb_cache[path] = (resp.content, resp.headers.get("content-type", "image/jpeg"))
                return StreamingResponse(iter([resp.content]), media_type=_thumb_cache[path][1], headers=_CACHE_HEADERS)
    except (CameraUnreachable, httpx.HTTPError) as e:
        raise HTTPException(502, f"获取缩略图失败：相机连接异常（{e}）")


THUMB_CACHE_DIR = DATA_DIR / "thumb_cache"
THUMB_CACHE_MAX = 1000  # 磁盘缓存文件数上限，超出时按修改时间淘汰最旧的一半


def _thumb_cache_file(path: str, size: int, mtime: int) -> Path:
    # key 含源文件 mtime：同名文件被覆盖（重新同步）后自动生成新缩略图
    key = hashlib.sha1(f"{path}:{size}:{mtime}".encode()).hexdigest()
    return THUMB_CACHE_DIR / f"{key}.jpg"


def _write_thumb_cache(cache_file: Path, content: bytes) -> None:
    """写磁盘缓存；写失败（磁盘满等）仅跳过，不影响本次响应。写后淘汰超量旧文件。"""
    try:
        THUMB_CACHE_DIR.mkdir(parents=True, exist_ok=True)
        # 临时文件 + rename 原子写入，避免并发请求互相写坏缓存文件
        fd, tmp = tempfile.mkstemp(dir=THUMB_CACHE_DIR, suffix=".tmp")
        try:
            with os.fdopen(fd, "wb") as f:
                f.write(content)
            os.replace(tmp, cache_file)
        except Exception:
            Path(tmp).unlink(missing_ok=True)  # 清理残留临时文件
            raise
        files = list(THUMB_CACHE_DIR.glob("*.jpg"))
        if len(files) > THUMB_CACHE_MAX:
            files.sort(key=lambda f: f.stat().st_mtime)
            for old in files[: len(files) // 2]:
                old.unlink(missing_ok=True)
    except OSError:
        pass


@app.get("/api/preview")
def preview(path: str = Query(...), size: int | None = None):
    """预览已备份到 NAS 的文件：仅允许 nas_path 目录内的文件，防目录穿越。
    带 size 时用 Pillow 生成缩略图（结果磁盘缓存，避免每次请求都解码原图），否则返回原图。
    """
    root = Path(config.nas_path).resolve()
    target = Path(path).resolve()
    if root != target and root not in target.parents:
        raise HTTPException(400, "文件不在备份目录内")
    if not target.is_file():
        raise HTTPException(404, "文件不存在")
    if not size:
        return FileResponse(target, headers=_CACHE_HEADERS)
    cache_file = _thumb_cache_file(path, size, int(target.stat().st_mtime))
    if cache_file.is_file():
        return FileResponse(cache_file, headers=_CACHE_HEADERS)
    try:
        from PIL import Image

        img = Image.open(target)
        img.thumbnail((size, size))
        buf = io.BytesIO()
        if img.mode not in ("RGB", "L"):
            img = img.convert("RGB")
        img.save(buf, "JPEG", quality=80)
    except Exception:
        raise HTTPException(415, "该格式不支持缩略图")
    content = buf.getvalue()
    _write_thumb_cache(cache_file, content)
    return Response(content, media_type="image/jpeg", headers=_CACHE_HEADERS)


STATIC_DIR = Path(__file__).resolve().parent / "static"
if STATIC_DIR.exists():
    app.mount("/", StaticFiles(directory=STATIC_DIR, html=True), name="static")
