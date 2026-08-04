import json
import threading
from datetime import datetime

from .config import DATA_DIR

STATE_FILE = DATA_DIR / "state.json"


class State:
    def __init__(self):
        self._lock = threading.Lock()
        if STATE_FILE.exists():
            self._data = json.loads(STATE_FILE.read_text())
        else:
            self._data = {"synced": {}, "last_sync": None, "last_error": None}

    def _save(self):
        STATE_FILE.write_text(json.dumps(self._data, indent=2, ensure_ascii=False))

    def is_synced(self, path: str) -> bool:
        return path in self._data["synced"]

    def mark_synced(self, path: str, size: int, dest: str):
        with self._lock:
            self._data["synced"][path] = {
                "size": size,
                "dest": dest,
                "synced_at": datetime.now().isoformat(timespec="seconds"),
            }
            self._save()

    def set_last_sync(self):
        with self._lock:
            self._data["last_sync"] = datetime.now().isoformat(timespec="seconds")
            self._save()

    def set_error(self, msg):
        with self._lock:
            self._data["last_error"] = msg
            self._save()

    @property
    def synced(self) -> dict:
        return self._data["synced"]

    @property
    def last_sync(self):
        return self._data["last_sync"]

    @property
    def last_error(self):
        return self._data["last_error"]
