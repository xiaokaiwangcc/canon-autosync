import asyncio
import json
import logging
import os
import threading
import time
from datetime import datetime

from .config import DATA_DIR

log = logging.getLogger("state")

STATE_FILE = DATA_DIR / "state.json"
# 节流：两次全量落盘的最小间隔（秒）。同步每传完一个文件都会触发写盘，
# 若每次都立即全量写会阻塞事件循环（同步中点击其他页面会卡顿），故合并写。
STATE_FLUSH_INTERVAL = 2.0


class State:
    def __init__(self):
        self._lock = threading.Lock()
        self._write_lock = asyncio.Lock()  # 串行化写盘，保证最终落盘的是最新快照
        self._dirty = False
        self._last_flush = 0.0
        self._retry_scheduled = False  # 是否已安排节流到期后的补写
        if STATE_FILE.exists():
            self._data = json.loads(STATE_FILE.read_text())
        else:
            self._data = {"synced": {}, "last_sync": None, "last_error": None}

    @staticmethod
    def _write_atomic(content: str) -> None:
        """临时文件 + rename 原子替换，避免写一半崩溃损坏 state.json。"""
        tmp = STATE_FILE.with_name(STATE_FILE.name + ".tmp")
        tmp.write_text(content)
        os.replace(tmp, STATE_FILE)

    def _schedule_flush(self):
        """标记脏数据并调度节流落盘（调用方需持有 _lock）。"""
        self._dirty = True
        try:
            asyncio.get_running_loop().create_task(self.flush())
        except RuntimeError:
            pass  # 无事件循环（启动早期等）：等待后续显式 flush(force=True)

    async def flush(self, force: bool = False) -> None:
        """节流落盘：force=True 立即写最新快照；否则距上次落盘不足间隔则推迟。

        写盘走线程池（to_thread），避免阻塞事件循环。
        """
        now = time.monotonic()
        async with self._write_lock:
            with self._lock:
                if not self._dirty:
                    return
                if not force and now - self._last_flush < STATE_FLUSH_INTERVAL:
                    # 间隔不足：安排到期后补写一次，否则一次突发的最后一次写入
                    # 被跳过后若再无状态变更，脏数据会一直不落盘
                    if not self._retry_scheduled:
                        self._retry_scheduled = True
                        delay = STATE_FLUSH_INTERVAL - (now - self._last_flush)
                        asyncio.get_running_loop().call_later(
                            delay, lambda: asyncio.create_task(self._flush_retry())
                        )
                    return
                self._dirty = False
                self._last_flush = now
                snapshot = json.dumps(self._data, indent=2, ensure_ascii=False)
            try:
                await asyncio.to_thread(self._write_atomic, snapshot)
            except OSError as e:
                log.error("state.json 落盘失败：%s", e)

    async def _flush_retry(self) -> None:
        """节流到期后的补写入口（由 call_later 触发）。"""
        self._retry_scheduled = False
        await self.flush()

    def is_synced(self, path: str) -> bool:
        return path in self._data["synced"]

    def clear_synced(self) -> int:
        """清空已备份记录（更换备份目录时调用），返回清掉的条数。"""
        with self._lock:
            n = len(self._data["synced"])
            if n:
                self._data["synced"] = {}
                self._schedule_flush()
            return n

    def mark_synced(self, path: str, size: int, dest: str):
        with self._lock:
            self._data["synced"][path] = {
                "size": size,
                "dest": dest,
                "synced_at": datetime.now().isoformat(timespec="seconds"),
            }
            self._schedule_flush()

    def set_last_sync(self):
        with self._lock:
            self._data["last_sync"] = datetime.now().isoformat(timespec="seconds")
            self._schedule_flush()

    def set_error(self, msg):
        with self._lock:
            self._data["last_error"] = msg
            self._schedule_flush()

    def snapshot_synced(self) -> list:
        """返回已同步记录快照（list of (path, meta)），供线程池中的路由安全遍历。"""
        with self._lock:
            return list(self._data["synced"].items())

    @property
    def synced(self) -> dict:
        return self._data["synced"]

    @property
    def last_sync(self):
        return self._data["last_sync"]

    @property
    def last_error(self):
        return self._data["last_error"]
