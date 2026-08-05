#!/usr/bin/env python3
"""canon-autosync 桌面版入口：本地启动 FastAPI 服务 + pywebview 原生窗口。

打包：PyInstaller（desktop/CanonAutoSync.spec），产物 macOS .app / Windows .exe。
"""
import os
import platform
import shutil
import socket
import sys
import threading
from pathlib import Path

# 将 backend 加入导入路径（源码运行与 PyInstaller 打包均可用）
BACKEND_DIR = Path(__file__).resolve().parent.parent / "backend"
if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))

APP_NAME = "canon-autosync"


def default_data_dir() -> Path:
    """用户数据目录：配置与同步记录（config.json / state.json）。"""
    system = platform.system()
    if system == "Darwin":
        base = Path.home() / "Library" / "Application Support"
    elif system == "Windows":
        base = Path(os.environ.get("APPDATA", Path.home() / "AppData" / "Roaming"))
    else:
        base = Path.home() / ".config"
    return base / APP_NAME


def default_nas_path() -> str:
    """首次启动的默认备份目录（此后由前端设置管理）。"""
    return str(Path.home() / "Pictures" / "canon-backup")


def free_port() -> int:
    """动态选择空闲端口，避免与已有实例或其他应用冲突。"""
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def ensure_default_config(data_dir: Path) -> None:
    """首次启动（无 config.json）时生成平台合理的默认配置。"""
    cfg_file = data_dir / "config.json"
    if cfg_file.exists():
        return
    data_dir.mkdir(parents=True, exist_ok=True)
    from app.config import Config, save_config

    save_config(Config(nas_path=default_nas_path()))


def run_server(port: int) -> None:
    import uvicorn
    from app.main import app

    class Server(uvicorn.Server):
        def install_signal_handlers(self):
            pass  # 后台线程中不能安装信号处理器

    Server(uvicorn.Config(app, host="127.0.0.1", port=port, log_level="warning")).run()


def ensure_static() -> None:
    """源码运行时（未打包）把前端构建产物就位到后端 static 目录。

    PyInstaller 打包时由 spec 的 datas 直接打入包内，无需此步骤。
    """
    if not BACKEND_DIR.exists():
        return  # 打包环境：静态资源已内置
    static = BACKEND_DIR / "app" / "static"
    if static.exists():
        return
    dist = BACKEND_DIR.parent / "frontend" / "dist"
    if not dist.exists():
        print("警告：frontend/dist 不存在，请先运行 `cd frontend && npm run build`", file=sys.stderr)
        return
    shutil.copytree(dist, static)


def main() -> None:
    ensure_static()
    data_dir = Path(os.environ.get("CANON_NAS_DATA") or default_data_dir())
    os.environ["CANON_NAS_DATA"] = str(data_dir)
    ensure_default_config(data_dir)

    port = free_port()
    threading.Thread(target=run_server, args=(port,), daemon=True).start()

    import webview

    # 窗口图标由打包产物的图标提供（Windows: exe 图标；macOS: .app 图标）
    webview.create_window(
        "佳能备份",
        f"http://127.0.0.1:{port}",
        width=1280,
        height=800,
        min_size=(960, 640),
        background_color="#f6f7f9",  # 与前端页面背景一致，避免加载闪白
    )
    webview.start()


if __name__ == "__main__":
    main()
