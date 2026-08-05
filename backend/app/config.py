import json
import os
from pathlib import Path

from pydantic import BaseModel

DATA_DIR = Path(
    os.environ.get("CANON_NAS_DATA") or Path(__file__).resolve().parent.parent / "data"
)
DATA_DIR.mkdir(parents=True, exist_ok=True)
CONFIG_FILE = DATA_DIR / "config.json"

APP_VERSION = "1.0.1"


class Config(BaseModel):
    camera_ip: str = "192.168.5.53"
    camera_port: int = 8080
    nas_path: str = "/Volumes/photos/canon-backup"
    auto_sync: bool = True
    sync_on_event: bool = True  # 事件驱动：相机有新文件时秒级同步
    delete_after_sync: bool = False  # 同步完成后删除卡上已备份文件（谨慎）
    poll_interval: int = 60  # 兜底扫描间隔（秒）：事件驱动下超过该时长无事件也会强制扫描


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
    CONFIG_FILE.write_text(cfg.model_dump_json(indent=2))
