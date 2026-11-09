import json
import os
import tempfile
from pathlib import Path

from pydantic import BaseModel, Field

DATA_DIR = Path(
    os.environ.get("CANON_NAS_DATA") or Path(__file__).resolve().parent.parent / "data"
)
DATA_DIR.mkdir(parents=True, exist_ok=True)
CONFIG_FILE = DATA_DIR / "config.json"

# 构建时由 fpk/build.sh / build-desktop.yml 按 tag 写入实际版本
APP_VERSION = os.environ.get("APP_VERSION", "1.2.1")


class Config(BaseModel):
    camera_ip: str = "192.168.5.53"
    camera_port: int = Field(8080, ge=1, le=65535)
    nas_path: str = "/Volumes/photos/canon-backup"
    auto_sync: bool = True
    sync_on_event: bool = True  # 事件驱动：相机有新文件时秒级同步
    delete_after_sync: bool = False  # 同步完成后删除卡上已备份文件（谨慎）
    poll_interval: int = Field(60, ge=1)  # 兜底扫描间隔（秒）


def load_config() -> Config:
    env_ip = os.environ.get("CANON_IP")
    env_nas = os.environ.get("NAS_PATH")
    if CONFIG_FILE.exists():
        cfg = Config(**json.loads(CONFIG_FILE.read_text()))
    else:
        cfg = Config()
    if env_ip:
        cfg.camera_ip = env_ip
    if env_nas:
        cfg.nas_path = env_nas
    return cfg


def save_config(cfg: Config) -> None:
    fd, name = tempfile.mkstemp(dir=CONFIG_FILE.parent, suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            handle.write(cfg.model_dump_json(indent=2))
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(name, CONFIG_FILE)
    finally:
        Path(name).unlink(missing_ok=True)
