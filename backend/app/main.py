from contextlib import asynccontextmanager
import asyncio
import io
import time
from pathlib import Path

import httpx
from fastapi import FastAPI, HTTPException, Query
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, Response, StreamingResponse
from fastapi.staticfiles import StaticFiles

from .ccapi import CanonCamera, CameraUnreachable
from .config import Config, load_config, save_config
from .state import State
from .sync import SyncEngine

state = State()
config = load_config()
engine = SyncEngine(config, state)


@asynccontextmanager
async def lifespan(app: FastAPI):
    engine.start()
    yield
    engine.stop()


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
def update_config(cfg: Config):
    global config
    save_config(cfg)
    config = cfg
    engine.config = cfg
    return cfg.model_dump()


@app.post("/api/sync")
async def trigger_sync():
    return await engine.sync_once()


@app.post("/api/reconnect")
async def reconnect_camera():
    ok = await engine.reconnect()
    return {"ok": ok, "camera_online": ok}


@app.get("/api/files")
def list_synced(offset: int = 0, limit: int = 100):
    items = [
        {"path": p, **meta}
        for p, meta in sorted(
            state.synced.items(), key=lambda kv: kv[1]["synced_at"], reverse=True
        )
    ]
    return {"total": len(items), "items": items[offset : offset + limit]}


@app.get("/api/pending")
def list_pending():
    return [
        p for p in engine.camera_files if not state.is_synced(p)
    ]


# 相机单连接处理能力弱：并发缩略图请求会触发 503，连续请求会逐渐卡死。
# 三重保护：全局串行锁 + 请求间最小间隔 + 内存缓存（轮询刷新不再重复打相机）
_thumb_lock = asyncio.Lock()
_thumb_cache: dict[str, tuple[bytes, str]] = {}
_thumb_last_req = 0.0

@app.get("/api/thumb")
async def thumbnail(path: str = Query(...)):
    cam: CanonCamera = engine.camera()
    cached = _thumb_cache.get(path)
    if cached:
        return StreamingResponse(iter([cached[0]]), media_type=cached[1])
    async with _thumb_lock:
        cached = _thumb_cache.get(path)
        if cached:
            return StreamingResponse(iter([cached[0]]), media_type=cached[1])
        global _thumb_last_req
        try:
            for attempt in range(2):
                await asyncio.sleep(max(0.0, 0.3 - (time.monotonic() - _thumb_last_req)))
                async with httpx.AsyncClient(timeout=10) as client:
                    resp = await client.get(cam.thumb_url(path))
                    _thumb_last_req = time.monotonic()
                    if resp.status_code == 503 and attempt == 0:
                        await asyncio.sleep(1)
                        continue
                    resp.raise_for_status()
                    if len(_thumb_cache) > 200:
                        _thumb_cache.clear()
                    _thumb_cache[path] = (resp.content, resp.headers.get("content-type", "image/jpeg"))
                    return StreamingResponse(iter([resp.content]), media_type=_thumb_cache[path][1])
        except (CameraUnreachable, httpx.HTTPError) as e:
            raise HTTPException(502, f"获取缩略图失败：相机连接异常（{e}）")


@app.get("/api/preview")
def preview(path: str = Query(...), size: int | None = None):
    """预览已备份到 NAS 的文件：仅允许 nas_path 目录内的文件，防目录穿越。
    带 size 时用 Pillow 生成缩略图（供列表封面），否则返回原图。
    """
    root = Path(config.nas_path).resolve()
    target = Path(path).resolve()
    if root != target and root not in target.parents:
        raise HTTPException(400, "文件不在备份目录内")
    if not target.is_file():
        raise HTTPException(404, "文件不存在")
    if not size:
        return FileResponse(target)
    try:
        from PIL import Image

        img = Image.open(target)
        img.thumbnail((size, size))
        buf = io.BytesIO()
        if img.mode not in ("RGB", "L"):
            img = img.convert("RGB")
        img.save(buf, "JPEG", quality=80)
        return Response(buf.getvalue(), media_type="image/jpeg")
    except Exception:
        raise HTTPException(415, "该格式不支持缩略图")


STATIC_DIR = Path(__file__).resolve().parent / "static"
if STATIC_DIR.exists():
    app.mount("/", StaticFiles(directory=STATIC_DIR, html=True), name="static")
