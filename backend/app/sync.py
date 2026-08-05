import asyncio
import logging
import time
from pathlib import Path

import httpx

from .ccapi import CanonCamera, CameraUnreachable, EventPollUnsupported, SyncStopped
from .config import Config
from .state import State

log = logging.getLogger("sync")

IMAGE_EXTS = {".jpg", ".jpeg", ".cr3", ".cr2", ".heif", ".hif", ".mp4", ".mov"}
INFO_REFRESH_INTERVAL = 60  # 电量/温度/卡状态刷新间隔（秒）


class SyncEngine:
    def __init__(self, config: Config, state: State):
        self.config = config
        self.state = state
        self.syncing = False
        self.camera_online = False
        self.camera_files: list[str] = []
        self.progress = {"current": None, "done": 0, "total": 0}
        self.camera_info = {
            "device": None,
            "battery": None,
            "temperature": None,
            "storage": None,
        }
        self.camera_warning: str | None = None
        self.sync_mode = "poll"  # "event" 事件驱动 | "poll" 定时扫描
        self.event_listening = False
        self.pending_count = 0  # 待备份数量缓存，status() 直接读取，避免每次轮询全量遍历
        self._task: asyncio.Task | None = None
        self._info_refreshed_at = 0.0
        self._device_fetched = False
        self._stop_requested = False

    def camera(self) -> CanonCamera:
        return CanonCamera(self.config.camera_ip, self.config.camera_port)

    def dest_for(self, cam_path: str) -> Path:
        rel = cam_path.split("/contents/", 1)[-1]
        return Path(self.config.nas_path) / rel

    async def check_camera(self) -> bool:
        was_online = self.camera_online
        try:
            await self.camera().ping()
            self.camera_online = True
            if not was_online:
                log.info("相机已连接：%s:%s", self.config.camera_ip, self.config.camera_port)
            if self.state.last_error:
                self.state.set_error(None)  # 恢复连接后清除错误提示
        except CameraUnreachable as e:
            self.camera_online = False
            msg = self._unreachable_msg(e)
            if self.state.last_error != msg:
                # 仅在提示变化时写入/记日志，避免离线段每 5 秒刷屏
                log.warning("相机连接失败：%s", e)
                self.state.set_error(msg)
            self._clear_info()
        except Exception as e:
            self.camera_online = False
            log.exception("相机状态检查出错：%s", e)
            self.state.set_error(f"相机状态检查出错：{e}")
            self._clear_info()
        return self.camera_online

    def _unreachable_msg(self, e: Exception) -> str:
        """相机断连时的中文引导提示：先查 CCAPI 连通性，再查相机配置。"""
        addr = f"http://{self.config.camera_ip}:{self.config.camera_port}"
        return (
            f"相机连接失败（{e}）。请在浏览器中访问 {addr} 检查 CCAPI 是否通畅："
            f"能打开说明网络正常，可点击「重试连接」；"
            f"打不开请检查相机配置——相机是否开机并连接同一网络、"
            f"相机菜单中是否已开启 CCAPI 网络设置、相机 IP 是否发生变化，修改后保存设置再重试。"
        )

    async def reconnect(self) -> bool:
        """立即重连相机并刷新设备信息（由前端重试按钮触发）。"""
        ok = await self.check_camera()
        if ok:
            self.state.set_error(None)
            await self.refresh_camera_info(force=True)
        return ok

    def _clear_info(self):
        self.camera_info = {
            "device": None,
            "battery": None,
            "temperature": None,
            "storage": None,
        }
        self._device_fetched = False
        self.event_listening = False

    async def refresh_camera_info(self, force: bool = False) -> None:
        """刷新电量/温度/卡状态（节流）；设备信息仅在首次连接后获取一次。"""
        cam = self.camera()
        if not self._device_fetched:
            try:
                self.camera_info["device"] = await cam.device_info()
                self._device_fetched = True
            except Exception:
                self.camera_info["device"] = None
        now = time.monotonic()
        if not force and now - self._info_refreshed_at < INFO_REFRESH_INTERVAL:
            return
        self._info_refreshed_at = now
        try:
            self.camera_info["battery"] = await cam.battery()
        except Exception:
            self.camera_info["battery"] = None
        try:
            self.camera_info["temperature"] = await cam.temperature()
        except Exception:
            self.camera_info["temperature"] = None
        try:
            self.camera_info["storage"] = await cam.storage()
        except Exception:
            self.camera_info["storage"] = None

    async def _guard_health(self) -> str | None:
        """同步前的健康检查，返回阻止同步的原因（None 表示允许）。"""
        temp = self.camera_info.get("temperature") or {}
        if temp.get("status") in ("high", "error"):
            return f"相机温度过高（{temp.get('status')}），暂停同步以防止过热断连"
        batt = self.camera_info.get("battery") or {}
        if batt.get("level") == "empty":
            return "相机电量耗尽，暂停同步"
        if batt.get("level") == "low":
            self.camera_warning = "相机电量低，建议连接充电器后再进行大批量传输"
        else:
            self.camera_warning = None
        return None

    async def sync_once(self) -> dict:
        if self.syncing:
            return {"skipped": True, "reason": "正在同步中，已跳过"}
        self._stop_requested = False
        self.syncing = True
        self.progress = {"current": None, "done": 0, "total": 0}
        try:
            reason = await self._guard_health()
            if reason:
                log.warning("同步被暂停：%s", reason)
                self.state.set_error(reason)
                return {"error": reason}
            cam = self.camera()
            files = await cam.list_all_files()
            self.camera_files = files
            pending = [
                p for p in files
                if Path(p).suffix.lower() in IMAGE_EXTS and not self.state.is_synced(p)
            ]
            self.pending_count = len(pending)
            self.progress["total"] = len(pending)
            if pending:
                log.info("开始同步：待备份 %d 个文件", len(pending))
            deleted = 0
            delete_failed = 0
            for cam_path in pending:
                if self._stop_requested:
                    log.info("同步已手动停止（已完成 %d/%d）", self.progress["done"], len(pending))
                    return {"stopped": True}
                dest = self.dest_for(cam_path)
                dest.parent.mkdir(parents=True, exist_ok=True)
                self.progress["current"] = cam_path
                size = await cam.download(
                    cam_path, dest,
                    should_stop=lambda: self._stop_requested,
                    on_slow=self._warn_slow_transfer,
                )
                self.state.mark_synced(cam_path, size, str(dest))
                log.info("已备份 %s → %s（%.1f MB）", cam_path, dest, size / 1024 / 1024)
                if self.pending_count > 0:
                    self.pending_count -= 1
                self.progress["done"] += 1
                if self.config.delete_after_sync:
                    try:
                        await cam.delete(cam_path)
                        deleted += 1
                    except Exception as e:
                        delete_failed += 1
                        log.warning("已备份但删除卡上文件失败：%s（%s）", cam_path, e)
                        self.camera_warning = f"已备份但删除卡上文件失败：{cam_path}（{e}）"
            self.state.set_last_sync()
            self.state.set_error(None)
            result = {"downloaded": len(pending), "deleted": deleted}
            if delete_failed:
                result["delete_failed"] = delete_failed
            if pending:
                log.info("同步完成：%s", result)
            return result
        except SyncStopped:
            log.info("同步已手动停止（已完成 %d/%d）", self.progress["done"], self.progress["total"])
            return {"stopped": True}
        except CameraUnreachable as e:
            self.camera_online = False
            msg = self._unreachable_msg(e)
            log.warning("同步中断：相机连接失败（%s）", e)
            self.state.set_error(msg)
            return {"error": msg}
        except Exception as e:
            msg = f"同步出错：{e}"
            log.exception("同步出错：%s", e)
            self.state.set_error(msg)
            return {"error": msg}
        finally:
            self.syncing = False
            self.progress["current"] = None
            # 任何退出路径都强制落盘一次，保证进度/错误/上次同步时间不丢失
            await self.state.flush(force=True)

    def _warn_slow_transfer(self, rate: float) -> None:
        """传输速率持续偏低：提示可能是 Wi-Fi 信号弱，不中止同步。"""
        log.warning("传输速率偏低（%.0f KB/s），可能是 Wi-Fi 信号弱", rate / 1024)
        self.camera_warning = (
            f"传输速率偏低（{rate / 1024:.0f} KB/s），可能是 Wi-Fi 信号弱，"
            "建议将相机靠近路由器后再继续大批量备份"
        )

    async def _event_wait(self) -> bool:
        """事件驱动等待：收到相机事件返回 True；超过兜底间隔无事件返回 False（触发扫描）。"""
        cam = self.camera()
        self.sync_mode = "event"
        deadline = time.monotonic() + max(self.config.poll_interval, 10)
        # 整个等待窗口复用同一连接：相机对新建连接的轮询立即返回空响应，
        # 只有复用连接长轮询才会真正挂起，事件才能秒级送达
        async with httpx.AsyncClient() as poll_client:
            while time.monotonic() < deadline:
                started = time.monotonic()
                try:
                    events = await cam.event_poll(client=poll_client)
                except EventPollUnsupported:
                    log.info("相机不支持事件轮询，降级为定时扫描")
                    self.sync_mode = "poll"
                    self.event_listening = False
                    return False
                except CameraUnreachable:
                    self.camera_online = False
                    return False
                except httpx.HTTPError as e:
                    # 相机忙碌等场景 polling 可能返回 4xx：记录后降级，下一轮循环重试
                    log.warning("事件轮询出错：%r，5 秒后重试", e)
                    self.event_listening = False
                    await asyncio.sleep(5)
                    return False
                if events:
                    log.info("收到相机事件（%s），立即触发同步", ",".join(events))
                    return True
                # 相机仍未挂起长轮询、立即返回空响应时：限速到约 1 次/秒，避免空转打满相机
                await asyncio.sleep(max(0.0, 1.0 - (time.monotonic() - started)))
        return False

    async def _loop(self):
        while True:
            try:
                if await self.check_camera():
                    await self.refresh_camera_info()
                    if self.config.auto_sync:
                        await self.sync_once()
                    if self.config.sync_on_event:
                        self.event_listening = True
                        changed = await self._event_wait()
                        self.event_listening = False
                        if changed:
                            continue  # 相机有变化，立即重新同步
                    else:
                        await asyncio.sleep(self.config.poll_interval)
                else:
                    await asyncio.sleep(5)
            except Exception as e:
                # 兜底：任何未捕获异常都不能让主循环死亡
                log.exception("同步主循环异常：%r", e)
                await asyncio.sleep(5)

    def request_stop(self) -> None:
        """请求停止当前同步（前端「停止同步」按钮触发，下一数据块到达时生效）。"""
        self._stop_requested = True

    def start(self):
        self._task = asyncio.create_task(self._loop())

    def stop(self):
        if self._task:
            self._task.cancel()

    def recount_pending(self) -> None:
        """已备份记录被清空后（如更换备份目录）重算待备份数，让 status 立即反映。"""
        self.pending_count = len([
            p for p in self.camera_files
            if Path(p).suffix.lower() in IMAGE_EXTS and not self.state.is_synced(p)
        ])

    def status(self) -> dict:
        return {
            "camera_online": self.camera_online,
            "camera_ip": self.config.camera_ip,
            "camera": self.camera_info,
            "camera_warning": self.camera_warning,
            "sync_mode": self.sync_mode,
            "event_listening": self.event_listening,
            "nas_path": self.config.nas_path,
            "auto_sync": self.config.auto_sync,
            "sync_on_event": self.config.sync_on_event,
            "delete_after_sync": self.config.delete_after_sync,
            "poll_interval": self.config.poll_interval,
            "syncing": self.syncing,
            "progress": self.progress,
            "camera_file_count": len(self.camera_files),
            "pending_count": self.pending_count,
            "synced_count": len(self.state.synced),
            "last_sync": self.state.last_sync,
            "last_error": self.state.last_error,
        }
