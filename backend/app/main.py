from contextlib import asynccontextmanager
import asyncio
import hashlib
import io
import logging
import os
import subprocess
import sys
import tempfile
import threading
from pathlib import Path

import httpx
from fastapi import FastAPI, HTTPException, Query
from fastapi.responses import FileResponse, Response, StreamingResponse
from fastapi.staticfiles import StaticFiles

from .access import api_password, protect_requests
from .backup import BackupTarget, media_path, run_io
from .ccapi import CanonCamera, CameraUnreachable
from .config import Config, DATA_DIR, load_config, save_config
from .log import attach_uvicorn_to_file, setup_logging
from .state import State
from .sync import SyncEngine

setup_logging()
log = logging.getLogger("api")

config = load_config()
state = State(config)
engine = SyncEngine(config, state)


@asynccontextmanager
async def lifespan(app: FastAPI):
    attach_uvicorn_to_file()  # uvicorn 此时已完成自身日志配置，挂上文件 handler
    log.info(
        "服务启动：相机 %s:%s，备份目录 %s",
        config.camera_ip, config.camera_port, config.nas_path,
    )
    engine.start()
    try:
        yield
    finally:
        await engine.stop()
        await state.flush(force=True)
        log.info("服务已停止")


app = FastAPI(title="canon-nas", lifespan=lifespan)
app.state.api_password = api_password()
app.middleware("http")(protect_requests)


async def save_state() -> None:
    try:
        await state.flush(force=True)
    except OSError as error:
        log.exception("无法保存同步记录")
        raise HTTPException(503, "Could not save backup state") from error


def requested_id(path: str, target: BackupTarget) -> str:
    if path.startswith("/"):
        try:
            return target.file_id(path)
        except ValueError as error:
            raise HTTPException(400, "Invalid camera file path") from error
    return path


@app.get("/api/status")
def get_status():
    return engine.status()


@app.get("/api/config")
def get_config():
    return config.model_dump()


@app.post("/api/config")
async def update_config(cfg: Config):
    global config
    if engine.syncing:
        raise HTTPException(409, "Wait for the current sync to finish before changing settings")
    save_config(cfg)
    config = cfg
    engine.apply_config(cfg)
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
def list_synced(offset: int = Query(0, ge=0), limit: int = Query(100, ge=1, le=200)):
    # 快照在锁内复制：同步主循环会并发写 state.synced，直接遍历可能抛
    # RuntimeError（dictionary changed size during iteration）导致接口 500
    target = BackupTarget.from_config(config)
    items = state.snapshot_synced(target.root)
    items.sort(key=lambda kv: kv[1]["synced_at"], reverse=True)
    total = len(items)
    page = items[offset : offset + limit]
    # 校验 dest 是否真实存在：用户在备份目录手动删文件后记录仍在，
    # 前端据此标记「已删除」而非误报缩略图加载失败（每页最多 200 条 stat，开销可忽略）
    # NAS 掉线/未挂载时 is_file 全为 False，会把正常记录误标「已删除」；
    # 根目录不可达时跳过存在性校验（exists=None，前端按未知处理不误标）
    nas_ok = target.root.is_dir()
    result = []
    for file_id, meta in page:
        dest = meta.get("dest")
        result.append({
            "id": file_id,
            "path": meta["href"],
            "dest": dest,
            "size": meta["size"],
            "synced_at": meta["synced_at"],
            "exists": (bool(dest) and Path(dest).is_file()) if nas_ok else None,
        })
    return {"total": total, "items": result}


@app.delete("/api/files")
async def delete_synced(path: str = Query(...)):
    """删除一条已备份记录及其 NAS 上的文件。仅允许删除 nas_path 内的文件，防目录穿越。"""
    if engine.syncing:
        raise HTTPException(409, "Wait for the current sync to finish before deleting backups")
    target = BackupTarget.from_config(config)
    file_id = requested_id(path, target)
    meta = state.get_synced(file_id, target.root)
    if meta is None:
        raise HTTPException(404, "记录不存在")
    dest = meta.get("dest")
    deleted_file = False
    if dest:
        def _delete_file():
            if not target.root.is_dir():
                raise HTTPException(503, "备份目录不可访问，请检查 NAS 挂载状态")
            target.checked_path(Path(dest))
            # Remove the directory entry itself even if it is a symlink.
            if not state.destination_claimed(dest, file_id):
                Path(dest).unlink(missing_ok=True)
                return True
            return False

        try:
            deleted_file = await run_io(_delete_file)
        except (OSError, ValueError) as error:
            raise HTTPException(409, "Could not delete the backup file") from error
    state.ignore(file_id, target.root)  # 相机上的原文件不再被自动同步传回
    state.remove_synced(file_id)
    await save_state()
    engine.recount_pending()
    return {"ok": True, "deleted_file": deleted_file}


@app.post("/api/files/restore")
async def restore_ignored(path: str = Query(...)):
    """把文件移出忽略名单：待备份列表和自动同步会重新包含它。"""
    target = BackupTarget.from_config(config)
    file_id = requested_id(path, target)
    restored = file_id in state.snapshot_ignored(target.root) and state.unignore(file_id)
    if restored:
        await save_state()
    engine.recount_pending()
    return {"ok": restored}


@app.post("/api/files/cleanup-missing")
async def cleanup_missing():
    """批量清理已从备份目录删除的同步记录：仅移除 dest 已不存在的记录，
    存在的文件不受影响；与单个删除一致，相机上的原文件加入忽略名单不再自动传回。
    """
    if engine.syncing:
        raise HTTPException(409, "Wait for the current sync to finish before cleaning up backups")
    # NAS 掉线/未挂载时 is_file 全为 False，会误清全部记录并加入忽略名单，先检查根目录可达
    target = BackupTarget.from_config(config)

    def _scan_missing() -> list:
        if not target.root.is_dir():
            raise HTTPException(503, "备份目录不可访问，请检查 NAS 挂载状态")
        missing = []
        for file_id, meta in state.snapshot_synced(target.root):
            dest = meta.get("dest")
            if dest and not Path(dest).is_file():
                missing.append((file_id, meta))
        return missing

    # 网络挂载盘上逐条 stat 是同步网络 IO，放线程池执行避免阻塞事件循环
    removed = 0
    missing = await run_io(_scan_missing)
    if engine.syncing:
        raise HTTPException(409, "Wait for the current sync to finish before cleaning up backups")
    for file_id, meta in missing:
        if state.get_synced(file_id, target.root) is meta:
            state.ignore(file_id, target.root)
            state.remove_synced(file_id)
            removed += 1
    if removed:
        await save_state()
        engine.recount_pending()
        log.info("已批量清理 %d 条已删除的备份记录", removed)
    return {"removed": removed}


@app.post("/api/files/clear-ignored")
async def clear_ignored():
    """批量清除手动删除备份留下的忽略记录（等于批量恢复备份）：
    相机上仍存在的原文件会重新进入待备份列表并自动同步。
    """
    cleared = 0
    for path in state.snapshot_ignored(BackupTarget.from_config(config).root):
        if state.unignore(path):
            cleared += 1
    if cleared:
        await save_state()
        engine.recount_pending()
        log.info("已批量清除 %d 条忽略记录", cleared)
    return {"cleared": cleared}


@app.get("/api/pending")
def list_pending():
    items = [
        {"path": href, "ignored": ignored}
        for href, _, ignored in engine.pending_files(BackupTarget.from_config(config), include_ignored=True)
    ]
    # 被忽略（手动删除备份）的排在最后，不打扰正常待备份浏览
    items.sort(key=lambda x: x["ignored"])
    return items


# 相机单连接处理能力弱：并发缩略图请求会触发 503，连续请求会逐渐卡死。
# 保护：信号量限制小并发窗口 + 请求失败重试 + 内存缓存（轮询刷新不再重复打相机）
# 再加浏览器缓存头：切页/刷新后命中本地缓存，不再请求后端
_thumb_sem = asyncio.Semaphore(2)
_thumb_cache: dict[tuple[str, int, str], tuple[bytes, str]] = {}
_CACHE_HEADERS = {"Cache-Control": "private, max-age=600", "Cross-Origin-Resource-Policy": "same-origin"}

@app.get("/api/thumb")
async def thumbnail(path: str = Query(...)):
    try:
        media_path(path)
    except ValueError as error:
        raise HTTPException(400, "Invalid camera file path") from error
    cam: CanonCamera = engine.camera()
    key = (cam.ip, cam.port, path)
    cached = _thumb_cache.get(key)
    if cached:
        return StreamingResponse(iter([cached[0]]), media_type=cached[1], headers=_CACHE_HEADERS)
    try:
        async with httpx.AsyncClient(timeout=10) as client:  # 重试复用同一连接
            for attempt in range(2):
                async with _thumb_sem:
                    cached = _thumb_cache.get(key)
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
                    for old in list(_thumb_cache)[: len(_thumb_cache) // 2]:
                        del _thumb_cache[old]
                _thumb_cache[key] = (resp.content, resp.headers.get("content-type", "image/jpeg"))
                return StreamingResponse(iter([resp.content]), media_type=_thumb_cache[key][1], headers=_CACHE_HEADERS)
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


# Pillow 无法解码视频，mp4/mov 缩略图改用 ffmpeg 提取首帧
# （打包环境优先用 CI 裁剪编译的最小化 ffmpeg，其余情况用 imageio-ffmpeg 自带完整静态二进制）
_VIDEO_EXTS = {".mp4", ".mov"}
# 相机 RAW 文件 Pillow 同样无法解码，但文件内嵌相机生成的 JPEG 预览图，用 rawpy（libraw）提取
_RAW_EXTS = {".cr3", ".cr2", ".raw", ".nef", ".arw", ".dng"}
# ffmpeg 解码首帧是 CPU 密集操作：限制并发，避免一次加载多个视频时打满 CPU
_video_thumb_sem = threading.Semaphore(2)


def _ffmpeg_exe() -> str | None:
    """定位 ffmpeg：优先打包附带的最小化二进制（sys._MEIPASS/ffmpeg/），否则回退 imageio-ffmpeg。"""
    base = getattr(sys, "_MEIPASS", None)
    if base:
        for name in ("ffmpeg.exe", "ffmpeg"):
            p = Path(base) / "ffmpeg" / name
            if p.is_file():
                return str(p)
    try:
        import imageio_ffmpeg

        return imageio_ffmpeg.get_ffmpeg_exe()
    except Exception:
        return None


def _raw_jpeg(target: Path) -> bytes | None:
    """提取 RAW 文件内嵌的 JPEG 预览图；rawpy 未安装或提取失败返回 None。"""
    try:
        import rawpy
    except ImportError:
        return None
    try:
        with rawpy.imread(str(target)) as raw:
            thumb = raw.extract_thumb()
            if thumb.format == rawpy.ThumbFormat.JPEG:
                return thumb.data
    except Exception:
        return None
    return None


def _video_thumb(target: Path, size: int) -> bytes:
    """用 ffmpeg 提取视频首帧生成 JPEG 缩略图。"""
    exe = _ffmpeg_exe()
    if not exe:
        raise RuntimeError("未找到 ffmpeg，无法生成视频缩略图")
    # PyInstaller 打包后二进制可能丢失执行权限（macOS 常见），确保可执行
    if not os.access(exe, os.X_OK):
        os.chmod(exe, 0o755)
    # -ss 0.1 跳过片头黑帧；scale 在 size 盒子内等比缩放、只缩小不放大（与 Pillow thumbnail 一致）
    cmd = [
        exe, "-nostdin", "-loglevel", "error",
        "-ss", "0.1", "-i", str(target),
        "-frames:v", "1", "-an",
        "-vf", f"scale='min({size},iw)':'min({size},ih)':force_original_aspect_ratio=decrease",
        "-f", "image2pipe", "-vcodec", "mjpeg", "-q:v", "5", "-",
    ]
    with _video_thumb_sem:
        proc = subprocess.run(cmd, capture_output=True, timeout=30)
    if proc.returncode != 0 or not proc.stdout:
        raise RuntimeError(f"ffmpeg 提取视频帧失败: {proc.stderr.decode(errors='replace')[-200:]}")
    return proc.stdout


@app.get("/api/preview")
def preview(path: str | None = None, size: int | None = None, id: str | None = None):
    """预览已备份到 NAS 的文件：仅允许 nas_path 目录内的文件，防目录穿越。
    带 size 时生成缩略图（图片用 Pillow，视频用 ffmpeg 提取首帧，RAW 提取内嵌 JPEG 预览；
    结果磁盘缓存，避免每次请求都解码原文件），否则返回原文件（RAW 返回内嵌 JPEG）。
    """
    backup = BackupTarget.from_config(config)
    if id is not None:
        meta = state.get_synced(id, backup.root)
    else:
        meta = next((meta for _, meta in state.snapshot_synced(backup.root) if meta["dest"] == path), None)
    if not meta:
        raise HTTPException(404, "备份记录不存在")
    try:
        target = backup.checked_path(Path(meta["dest"]))
    except ValueError as error:
        raise HTTPException(400, "文件不在备份目录内") from error
    if not target.is_file():
        raise HTTPException(404, "文件不存在")
    if not size:
        # RAW 本体浏览器无法渲染，直出内嵌 JPEG；提取失败时退回原文件
        if target.suffix.lower() in _RAW_EXTS:
            raw_jpeg = _raw_jpeg(target)
            if raw_jpeg:
                return Response(raw_jpeg, media_type="image/jpeg", headers=_CACHE_HEADERS)
        return FileResponse(target, headers=_CACHE_HEADERS)
    cache_file = _thumb_cache_file(str(target), size, target.stat().st_mtime_ns)
    if cache_file.is_file():
        return FileResponse(cache_file, headers=_CACHE_HEADERS)
    try:
        if target.suffix.lower() in _VIDEO_EXTS:
            content = _video_thumb(target, size)
        else:
            from PIL import Image

            # RAW 文件先提取内嵌 JPEG 再缩放，其余直接 Pillow 解码
            raw_jpeg = _raw_jpeg(target) if target.suffix.lower() in _RAW_EXTS else None
            img = Image.open(io.BytesIO(raw_jpeg)) if raw_jpeg else Image.open(target)
            img.thumbnail((size, size))
            buf = io.BytesIO()
            if img.mode not in ("RGB", "L"):
                img = img.convert("RGB")
            img.save(buf, "JPEG", quality=80)
            content = buf.getvalue()
    except Exception:
        raise HTTPException(415, "该格式不支持缩略图")
    _write_thumb_cache(cache_file, content)
    return Response(content, media_type="image/jpeg", headers=_CACHE_HEADERS)


STATIC_DIR = Path(__file__).resolve().parent / "static"
if STATIC_DIR.exists():
    app.mount("/", StaticFiles(directory=STATIC_DIR, html=True), name="static")
