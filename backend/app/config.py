import json
import locale
import os
import tempfile
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, Field

DATA_DIR = Path(
    os.environ.get("CANON_NAS_DATA") or Path(__file__).resolve().parent.parent / "data"
)
DATA_DIR.mkdir(parents=True, exist_ok=True)
CONFIG_FILE = DATA_DIR / "config.json"

# 构建时由 fpk/build.sh / build-desktop.yml 按 tag 写入实际版本
APP_VERSION = os.environ.get("APP_VERSION", "1.2.2")


class Config(BaseModel):
    camera_ip: str = "192.168.5.53"
    camera_port: int = Field(8080, ge=1, le=65535)
    camera_protocol: Literal["http", "https"] = "http"
    camera_fingerprint: str | None = Field(None, pattern=r"^[0-9a-f]{64}$")

    nas_path: str = "/Volumes/photos/canon-backup"
    auto_sync: bool = True
    sync_on_event: bool = True  # 事件驱动：相机有新文件时秒级同步
    delete_after_sync: bool = False  # 同步完成后删除卡上已备份文件（谨慎）
    poll_interval: int = Field(60, ge=1)  # 兜底扫描间隔（秒）

    @property
    def camera_connection(self):
        return (self.camera_ip, self.camera_port, self.camera_protocol, self.camera_fingerprint)


def read_json(path: Path) -> dict:
    try:
        content = path.read_text(encoding="utf-8")
    except UnicodeDecodeError:
        # Older Windows builds wrote JSON using the system code page.
        content = path.read_text(encoding=locale.getpreferredencoding(False))
    return json.loads(content)


def load_config() -> Config:
    saved = read_json(CONFIG_FILE) if CONFIG_FILE.exists() else {}
    environment = {
        field: value
        for field, name in (("camera_ip", "CANON_IP"), ("nas_path", "NAS_PATH"))
        if (value := os.environ.get(name))
    }
    if os.environ.get("CANON_CONFIG_ENV_DEFAULTS") == "1":
        return Config(**(environment | saved))
    return Config(**(saved | environment))


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
