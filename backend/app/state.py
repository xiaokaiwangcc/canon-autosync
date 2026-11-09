import asyncio
import json
import logging
import os
import tempfile
import threading
import time
from collections import Counter
from datetime import datetime
from pathlib import Path

from .backup import BackupTarget, _sync_directory, run_io
from .config import DATA_DIR, Config, read_json

log = logging.getLogger("state")

STATE_FILE = DATA_DIR / "state.json"
# 节流：两次全量落盘的最小间隔（秒）。同步每传完一个文件都会触发写盘，
# 若每次都立即全量写会阻塞事件循环（同步中点击其他页面会卡顿），故合并写。
STATE_FLUSH_INTERVAL = 2.0


class State:
    def __init__(self, config: Config):
        self._lock = threading.Lock()
        self._write_lock = asyncio.Lock()  # 串行化写盘，保证最终落盘的是最新快照
        self._dirty = False
        self._last_flush = 0.0
        self._retry_scheduled = False  # 是否已安排节流到期后的补写
        if STATE_FILE.exists():
            self._data = read_json(STATE_FILE)
            self._data.setdefault("ignored", {} if "version" in self._data else [])
        else:
            self._data = {"version": 2, "synced": {}, "last_sync": None, "last_error": None, "ignored": {}}
        if "version" not in self._data:
            self._migrate_legacy(BackupTarget.from_config(config))
        self._synced_counts = Counter(meta["root"] for meta in self._data["synced"].values())

    def _migrate_legacy(self, target: BackupTarget) -> None:
        """Legacy state belonged to the configured camera and backup directory."""
        synced = {}
        ignored = {}
        for href, meta in self._data["synced"].items():
            try:
                file_id = target.file_id(href)
                dest = target.checked_path(Path(meta["dest"]))
                if dest.is_file() and (meta["size"] <= 0 or dest.stat().st_size != meta["size"]):
                    log.warning("旧备份文件大小异常，下次同步会重新下载：%s", dest)
                    continue
            except (KeyError, ValueError, OSError):
                continue
            previous = synced.get(file_id)
            if previous is None or meta["synced_at"] >= previous["synced_at"]:
                synced[file_id] = {**meta, "dest": str(dest), "href": href, "root": str(target.root)}
        for href in self._data["ignored"]:
            try:
                ignored[target.file_id(href)] = str(target.root)
            except ValueError:
                continue
        self._data.update(version=2, synced=synced, ignored=ignored)
        self._dirty = True

    @staticmethod
    def _write_atomic(content: str) -> None:
        """临时文件 + rename 原子替换，避免写一半崩溃损坏 state.json。"""
        fd, name = tempfile.mkstemp(dir=STATE_FILE.parent, suffix=".tmp")
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as handle:
                handle.write(content)
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(name, STATE_FILE)
            _sync_directory(STATE_FILE.parent)
        finally:
            Path(name).unlink(missing_ok=True)

    def _schedule_flush(self):
        """标记脏数据并调度节流落盘（调用方需持有 _lock）。"""
        self._dirty = True
        try:
            asyncio.get_running_loop().create_task(self._flush_background())
        except RuntimeError:
            pass  # 无事件循环（启动早期等）：等待后续显式 flush(force=True)

    async def flush(self, force: bool = False) -> None:
        """节流落盘：force=True 立即写最新快照；否则距上次落盘不足间隔则推迟。

        写盘走线程池（to_thread），避免阻塞事件循环。
        """
        async with self._write_lock:
            now = time.monotonic()
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
                snapshot = json.dumps(self._data, indent=2, ensure_ascii=False)
            try:
                await run_io(self._write_atomic, snapshot)
            except (OSError, asyncio.CancelledError):
                with self._lock:
                    self._dirty = True
                raise
            self._last_flush = now

    async def _flush_background(self) -> None:
        try:
            await self.flush()
        except OSError:
            log.exception("state.json 落盘失败")

    async def _flush_retry(self) -> None:
        """节流到期后的补写入口（由 call_later 触发）。"""
        self._retry_scheduled = False
        await self._flush_background()

    def is_synced(self, path: str) -> bool:
        return path in self._data["synced"]

    def ignore(self, path: str, root: Path) -> None:
        """加入忽略名单：手动删除备份后，相机上的原文件不再参与自动同步。"""
        with self._lock:
            if path not in self._data["ignored"]:
                self._data["ignored"][path] = str(root)
                self._schedule_flush()

    def unignore(self, path: str) -> bool:
        """移出忽略名单（恢复备份），返回是否实际移除。"""
        with self._lock:
            if path in self._data["ignored"]:
                del self._data["ignored"][path]
                self._schedule_flush()
                return True
            return False

    def is_ignored(self, path: str) -> bool:
        return path in self._data["ignored"]

    def remove_synced(self, path: str) -> dict | None:
        """删除一条已备份记录，返回被删的 meta；不存在返回 None。"""
        with self._lock:
            meta = self._data["synced"].pop(path, None)
            if meta is not None:
                self._synced_counts[meta["root"]] -= 1
                self._schedule_flush()
            return meta

    def mark_synced(self, file_id: str, size: int, dest: Path, *, href: str, root: Path):
        with self._lock:
            if file_id not in self._data["synced"]:
                self._synced_counts[str(root)] += 1
            self._data["synced"][file_id] = {
                "size": size,
                "dest": str(dest),
                "href": href,
                "root": str(root),
                "synced_at": datetime.now().isoformat(timespec="seconds"),
            }
            self._schedule_flush()

    def set_last_sync(self):
        with self._lock:
            self._data["last_sync"] = datetime.now().isoformat(timespec="seconds")
            self._schedule_flush()

    def set_error(self, msg: str | None, english: str | None = None):
        with self._lock:
            self._data["last_error"] = msg
            self._data["last_error_en"] = english
            self._schedule_flush()

    def error_messages(self) -> tuple[str | None, str | None]:
        with self._lock:
            return self._data["last_error"], self._data.get("last_error_en")

    def snapshot_synced(self, root: Path) -> list:
        """返回已同步记录快照（list of (path, meta)），供线程池中的路由安全遍历。"""
        with self._lock:
            return [(key, meta) for key, meta in self._data["synced"].items() if meta["root"] == str(root)]

    def get_synced(self, file_id: str, root: Path) -> dict | None:
        with self._lock:
            meta = self._data["synced"].get(file_id)
            return meta if meta and meta["root"] == str(root) else None

    def destination_claimed(self, dest: str, file_id: str) -> bool:
        with self._lock:
            return any(
                key != file_id and meta["dest"] == dest for key, meta in self._data["synced"].items()
            )

    def snapshot_ignored(self, root: Path) -> list:
        """返回忽略名单快照，供线程池中的路由安全遍历。"""
        with self._lock:
            return [key for key, value in self._data["ignored"].items() if value == str(root)]

    def synced_count(self, root: Path) -> int:
        with self._lock:
            return self._synced_counts[str(root)]

    @property
    def last_sync(self):
        return self._data["last_sync"]

    @property
    def last_error(self):
        return self._data["last_error"]
