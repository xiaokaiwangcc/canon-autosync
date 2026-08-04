import asyncio
import time
from collections.abc import Callable

import httpx

CONTENTS_ROOT = "/ccapi/ver130/contents"
EVENT_POLL_URL = "/ccapi/ver100/event/polling"
# 相机无事件时保持连接约 60s 后才返回，read 超时必须大于该时长
EVENT_POLL_READ_TIMEOUT = 65.0
# 下载停滞判定：窗口内累计传输低于下限即视为相机已断线/卡死，避免界面无限停在“同步中”
STALL_SECONDS = 30
STALL_MIN_BYTES = 1024 * 1024
# 低速预警：窗口速率低于该值（B/s）时回调提示 Wi-Fi 信号可能偏弱（不中止，仅提示）
LOW_SPEED_THRESHOLD = 256 * 1024


class CameraUnreachable(Exception):
    pass


class SyncStopped(Exception):
    """用户主动停止同步"""
    pass


class EventPollUnsupported(Exception):
    """相机不支持事件轮询（老机型），应降级为定时扫描"""
    pass


class CanonCamera:
    def __init__(self, ip: str, port: int = 8080, timeout: float = 10.0):
        self.base = f"http://{ip}:{port}"
        self.timeout = timeout

    async def _get(self, path: str, retries: int = 0, timeout=None, **kwargs) -> httpx.Response:
        last_err: Exception | None = None
        for attempt in range(retries + 1):
            try:
                async with httpx.AsyncClient(timeout=timeout or self.timeout) as client:
                    resp = await client.get(f"{self.base}{path}", **kwargs)
                    resp.raise_for_status()
                    return resp
            except httpx.HTTPStatusError as e:
                if e.response.status_code < 500:
                    raise
                last_err = CameraUnreachable(
                    f"相机 CCAPI 未就绪（HTTP {e.response.status_code}），相机服务可能仍在启动中"
                )
            except (httpx.ConnectError, httpx.TimeoutException, httpx.RemoteProtocolError) as e:
                last_err = CameraUnreachable(str(e))
            if attempt < retries:
                await asyncio.sleep(2)
        raise last_err

    async def ping(self) -> dict:
        resp = await self._get("/ccapi/", retries=2)
        return resp.json()

    async def poll_events(self) -> list[str]:
        resp = await self._get("/ccapi/ver100/event/polling")
        return resp.json().get("addedcontents") or []

    async def disable_autopoweroff(self) -> None:
        try:
            async with httpx.AsyncClient(timeout=self.timeout) as client:
                await client.put(
                    f"{self.base}/ccapi/ver100/functions/autopoweroff",
                    json={"value": "disable"},
                )
        except (httpx.ConnectError, httpx.TimeoutException, httpx.RemoteProtocolError) as e:
            raise CameraUnreachable(str(e)) from e

    async def device_info(self) -> dict:
        resp = await self._get("/ccapi/ver100/deviceinformation")
        return resp.json()

    async def battery(self) -> dict:
        resp = await self._get("/ccapi/ver100/devicestatus/battery")
        return resp.json()

    async def temperature(self) -> dict:
        resp = await self._get("/ccapi/ver100/devicestatus/temperature")
        return resp.json()

    async def storage(self) -> dict:
        resp = await self._get("/ccapi/ver110/devicestatus/storage")
        return resp.json()

    async def event_poll(self) -> list[dict]:
        """事件轮询：长连接等待相机事件（新文件/文件删除等）。

        无事件时相机保持连接约 60s 后返回空响应；不支持事件轮询的相机返回 404。
        """
        try:
            resp = await self._get(
                EVENT_POLL_URL,
                # httpx.Timeout 要求四个参数全给或带默认值，缺 write/pool 会抛 ValueError
                timeout=httpx.Timeout(
                    connect=self.timeout, read=EVENT_POLL_READ_TIMEOUT,
                    write=self.timeout, pool=self.timeout,
                ),
            )
        except httpx.HTTPStatusError as e:
            if e.response.status_code == 404:
                raise EventPollUnsupported("相机不支持事件轮询")
            raise
        if not resp.text.strip():
            return []
        return resp.json().get("events", [])

    async def list_dir(self, href: str) -> list[str]:
        resp = await self._get(href)
        return resp.json().get("path", [])

    async def list_all_files(self) -> list[str]:
        files: list[str] = []
        queue = [CONTENTS_ROOT]
        while queue:
            current = queue.pop()
            for href in await self.list_dir(current):
                name = href.rsplit("/", 1)[-1]
                if "." in name:
                    files.append(href)
                else:
                    queue.append(href)
        return sorted(files)

    def file_url(self, href: str) -> str:
        return f"{self.base}{href}"

    def thumb_url(self, href: str) -> str:
        return f"{self.base}{href}?kind=thumbnail"

    async def download(
        self, href: str, dest,
        should_stop: Callable[[], bool] | None = None,
        on_slow: Callable[[float], None] | None = None,
    ) -> int:
        """流式下载文件到 dest。

        断线防护：read 超时 20s 兜底“完全无数据”；传输停滞检测——窗口内累计新增
        不足 STALL_MIN_BYTES 判定相机断线/卡死，主动中止同步；
        低速预警——窗口速率低于 LOW_SPEED_THRESHOLD 时回调提示可能是 Wi-Fi 信号弱；
        相机忙碌（503）时等待 1.5s 自动重试，最多 3 次，避免一次 503 中断整批；
        写盘走线程池，避免备份目录为网络挂载时阻塞事件循环导致整体无响应。
        """
        size = 0
        async with httpx.AsyncClient(timeout=httpx.Timeout(20.0, read=20.0)) as client:
            for attempt in range(3):
                try:
                    async with client.stream("GET", self.file_url(href)) as resp:
                        if resp.status_code == 503:
                            if attempt < 2:
                                await asyncio.sleep(1.5)
                                continue
                            raise CameraUnreachable(
                                "相机持续忙碌（HTTP 503），可能正在处理其他操作或过热，请稍后重试"
                            )
                        resp.raise_for_status()
                        with open(dest, "wb") as f:
                            window_start = time.monotonic()
                            window_bytes = 0
                            async for chunk in resp.aiter_bytes(256 * 1024):
                                if should_stop and should_stop():
                                    raise SyncStopped("用户停止同步")
                                now = time.monotonic()
                                window_bytes += len(chunk)
                                if now - window_start >= STALL_SECONDS:
                                    if window_bytes < STALL_MIN_BYTES:
                                        raise CameraUnreachable(
                                            f"相机传输停滞（{STALL_SECONDS}s 内仅传输 {window_bytes} 字节）。"
                                            "常见原因：Wi-Fi 信号弱（相机距路由器过远或隔墙）、相机过热或忙碌。"
                                            "请将相机靠近路由器后点击「重试连接」"
                                        )
                                    if on_slow:
                                        rate = window_bytes / STALL_SECONDS
                                        if rate < LOW_SPEED_THRESHOLD:
                                            on_slow(rate)
                                    window_start = now
                                    window_bytes = 0
                                await asyncio.to_thread(f.write, chunk)
                                size += len(chunk)
                        return size
                except (httpx.ConnectError, httpx.TimeoutException, httpx.RemoteProtocolError) as e:
                    raise CameraUnreachable(str(e)) from e
        raise CameraUnreachable("相机持续忙碌（HTTP 503），可能正在处理其他操作或过热，请稍后重试")

    async def delete(self, href: str) -> None:
        try:
            async with httpx.AsyncClient(timeout=self.timeout) as client:
                resp = await client.delete(f"{self.base}{href}")
                resp.raise_for_status()
        except (httpx.ConnectError, httpx.TimeoutException, httpx.RemoteProtocolError) as e:
            raise CameraUnreachable(str(e)) from e
