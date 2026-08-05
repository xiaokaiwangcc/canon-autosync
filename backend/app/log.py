import logging
from logging.handlers import RotatingFileHandler

from .config import DATA_DIR

LOG_FILE = DATA_DIR / "app.log"
_file_handler: RotatingFileHandler | None = None


def setup_logging() -> RotatingFileHandler:
    """初始化日志：终端 + data/app.log（2MB 轮转，保留 3 份）。可重复调用。"""
    global _file_handler
    if _file_handler is None:
        fmt = logging.Formatter("%(asctime)s %(levelname)s %(name)s: %(message)s")
        _file_handler = RotatingFileHandler(
            LOG_FILE, maxBytes=2 * 1024 * 1024, backupCount=3, encoding="utf-8"
        )
        _file_handler.setFormatter(fmt)
        stream = logging.StreamHandler()
        stream.setFormatter(fmt)
        root = logging.getLogger()
        root.setLevel(logging.INFO)
        root.addHandler(_file_handler)
        root.addHandler(stream)
    return _file_handler


def attach_uvicorn_to_file() -> None:
    """把 uvicorn 的访问/错误日志也写入 app.log。

    需在 uvicorn 完成自身日志配置后调用（lifespan 启动时），否则挂上的 handler
    会被 uvicorn 的 dictConfig 清掉。uvicorn / uvicorn.access 默认 propagate=False，
    uvicorn.error 自身无 handler、向上传播到 uvicorn，因此只挂这两个即可覆盖
    全部访问/错误日志且不重复。
    """
    fh = setup_logging()
    for name in ("uvicorn", "uvicorn.access"):
        lg = logging.getLogger(name)
        if fh not in lg.handlers:
            lg.addHandler(fh)
