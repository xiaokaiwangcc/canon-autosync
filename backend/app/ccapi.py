import asyncio
import json
import os
import re
import time
from collections.abc import Callable
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

import httpx

from .backup import run_io

CCAPI_ROOT = "/ccapi/"
VERSION_ROOT_RE = re.compile(r"^/ccapi/ver(\d+)/?$")
FALLBACK_VERSIONS = {
    "contents": ("130", "140", "120", "110", "100"),
    "event": ("100", "110", "120", "130", "140"),
    "device": ("100", "110", "120", "130", "140"),
    "battery": ("100", "110", "120", "130", "140"),
    "temperature": ("100", "110", "120", "130", "140"),
    "storage": ("110", "100", "120", "130", "140"),
}
# 相机无事件时保持连接约 60s 后才返回，read 超时必须大于该时长
EVENT_POLL_READ_TIMEOUT = 65.0
# 下载停滞判定：窗口内累计传输低于下限即视为相机已断线/卡死，避免界面无限停在“同步中”
STALL_SECONDS = 30
STALL_MIN_BYTES = 1024 * 1024
# 低速预警：窗口速率低于该值（B/s）时回调提示 Wi-Fi 信号可能偏弱（不中止，仅提示）
LOW_SPEED_THRESHOLD = 256 * 1024


class CameraUnreachable(Exception):
    def __init__(self, message: str = "", *, english: str | None = None):
        super().__init__(message)
        self.english = message if english is None else english


class SyncStopped(Exception):
    """用户主动停止同步"""
    pass


class EventPollUnsupported(Exception):
    """相机不支持事件轮询（老机型），应降级为定时扫描"""
    pass


class CapabilityUnsupported(Exception):
    """相机未公布且候选版本均不支持某项可选能力。"""
    pass


class CanonCamera:
    def __init__(self, ip: str, port: int = 8080, timeout: float = 10.0):
        self.ip = ip
        self.port = port
        self.base = f"http://{ip}:{port}"
        self.timeout = timeout
        self._catalog_loaded = False
        self._advertised_paths: set[str] = set()
        self._endpoint_cache: dict[str, str] = {}
        self._unsupported_capabilities: set[str] = set()
        self._contents_list_mode: str | None = None

    async def _get(self, path: str, retries: int = 0, timeout=None, client: httpx.AsyncClient | None = None, **kwargs) -> httpx.Response:
        last_err: Exception | None = None
        url = f"{self.base}{path}"
        for attempt in range(retries + 1):
            try:
                if client is not None:
                    # 复用调用方传入的连接（事件长轮询等场景）
                    resp = await client.get(url, timeout=timeout or self.timeout, **kwargs)
                else:
                    async with httpx.AsyncClient(timeout=timeout or self.timeout) as c:
                        resp = await c.get(url, **kwargs)
                resp.raise_for_status()
                return resp
            except httpx.HTTPStatusError as e:
                if e.response.status_code < 500:
                    raise
                last_err = CameraUnreachable(
                    f"相机 CCAPI 未就绪（HTTP {e.response.status_code}），相机服务可能仍在启动中",
                    english=f"Camera CCAPI is unavailable (HTTP {e.response.status_code}). It may still be starting.",
                )
            except (httpx.ConnectError, httpx.TimeoutException, httpx.RemoteProtocolError) as e:
                last_err = CameraUnreachable(str(e))
            if attempt < retries:
                await asyncio.sleep(2)
        raise last_err

    async def ping(self) -> dict:
        resp = await self._get(CCAPI_ROOT, retries=2)
        payload = resp.json()
        self._advertised_paths.update(self._extract_ccapi_paths(payload))
        return payload

    @staticmethod
    def _extract_ccapi_paths(payload) -> set[str]:
        """从不同版本的入口 JSON 中递归提取 CCAPI URL/路径。"""
        paths: set[str] = set()

        def visit(value) -> None:
            if isinstance(value, dict):
                for key, item in value.items():
                    visit(key)
                    visit(item)
            elif isinstance(value, (list, tuple)):
                for item in value:
                    visit(item)
            elif isinstance(value, str):
                candidate = value.strip()
                if re.fullmatch(r"ver\d+", candidate):
                    paths.add(f"/ccapi/{candidate}")
                    return
                if candidate.startswith(("http://", "https://")):
                    candidate = urlsplit(candidate).path
                elif "/ccapi/" in candidate and not candidate.startswith("/ccapi/"):
                    candidate = candidate[candidate.index("/ccapi/"):]
                if candidate.startswith("/ccapi/"):
                    normalized = candidate.rstrip("/")
                    paths.add(CCAPI_ROOT if normalized == "/ccapi" else normalized)

        visit(payload)
        return paths

    async def _ensure_catalog(self) -> None:
        if self._catalog_loaded:
            return
        if not self._advertised_paths:
            await self.ping()

        # /ccapi/ 通常先公布版本入口，再由版本入口公布具体资源。
        version_roots = sorted(
            (path for path in self._advertised_paths if VERSION_ROOT_RE.match(path)),
            key=lambda path: int(VERSION_ROOT_RE.match(path).group(1)),
            reverse=True,
        )
        for root in version_roots:
            try:
                resp = await self._get(root)
                self._advertised_paths.update(self._extract_ccapi_paths(resp.json()))
            except httpx.HTTPStatusError as e:
                if e.response.status_code not in (404, 405):
                    raise
        self._catalog_loaded = True

    @staticmethod
    def _path_version(path: str) -> int:
        match = re.search(r"/ver(\d+)(?:/|$)", path)
        return int(match.group(1)) if match else 0

    async def _candidate_endpoints(
        self,
        capability: str,
        suffix: str,
    ) -> list[str]:
        await self._ensure_catalog()
        normalized_suffix = "/" + suffix.strip("/")
        advertised = sorted(
            (
                path for path in self._advertised_paths
                if path.rstrip("/").endswith(normalized_suffix)
            ),
            key=self._path_version,
            reverse=True,
        )
        fallbacks = [
            f"/ccapi/ver{version}{normalized_suffix}"
            for version in FALLBACK_VERSIONS[capability]
        ]
        return list(dict.fromkeys([*advertised, *fallbacks]))

    async def _get_capability(
        self,
        capability: str,
        suffix: str,
        *,
        client: httpx.AsyncClient | None = None,
        timeout=None,
    ) -> httpx.Response:
        if capability in self._unsupported_capabilities:
            raise CapabilityUnsupported(f"相机不支持 {capability} 接口")
        cached = self._endpoint_cache.get(capability)
        candidates = [cached] if cached else []
        candidates.extend(await self._candidate_endpoints(capability, suffix))
        last_missing: httpx.HTTPStatusError | None = None
        for path in dict.fromkeys(path for path in candidates if path):
            try:
                resp = await self._get(path, client=client, timeout=timeout)
                self._endpoint_cache[capability] = path
                return resp
            except httpx.HTTPStatusError as e:
                if e.response.status_code not in (404, 405):
                    raise
                last_missing = e
                if cached == path:
                    self._endpoint_cache.pop(capability, None)
        if last_missing is not None:
            self._unsupported_capabilities.add(capability)
        raise CapabilityUnsupported(f"相机不支持 {capability} 接口")

    def capabilities(self) -> dict:
        versions = sorted(
            {
                match.group(1)
                for path in self._advertised_paths
                if (match := re.search(r"/ccapi/ver(\d+)(?:/|$)", path))
            },
            key=int,
        )
        return {
            "versions": versions,
            "endpoints": dict(self._endpoint_cache),
            "unsupported": sorted(self._unsupported_capabilities),
            "contents_list_mode": self._contents_list_mode,
        }

    async def poll_events(self) -> list[str]:
        resp = await self._get_capability("event", "event/polling")
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
        resp = await self._get_capability("device", "deviceinformation")
        return resp.json()

    async def battery(self) -> dict:
        resp = await self._get_capability("battery", "devicestatus/battery")
        return resp.json()

    async def temperature(self) -> dict:
        resp = await self._get_capability("temperature", "devicestatus/temperature")
        return resp.json()

    async def storage(self) -> dict:
        resp = await self._get_capability("storage", "devicestatus/storage")
        return resp.json()

    async def event_poll(self, client: httpx.AsyncClient | None = None) -> dict:
        """事件轮询：长连接等待相机事件（新文件/文件删除等）。

        无事件时相机保持连接约 60s 后返回空响应；不支持事件轮询的相机返回 404。
        传入共享 client 复用 TCP 连接——相机对每次新建连接的轮询都立即返回空响应，
        只有连接复用时长轮询才会真正挂起等待事件。
        """
        try:
            resp = await self._get_capability(
                "event",
                "event/polling",
                client=client,
                # httpx.Timeout 要求四个参数全给或带默认值，缺 write/pool 会抛 ValueError
                timeout=httpx.Timeout(
                    connect=self.timeout, read=EVENT_POLL_READ_TIMEOUT,
                    write=self.timeout, pool=self.timeout,
                ),
            )
        except CapabilityUnsupported:
            raise EventPollUnsupported("相机不支持事件轮询")
        except httpx.HTTPStatusError as e:
            if e.response.status_code not in (404, 405):
                raise
            raise EventPollUnsupported("相机不支持事件轮询")
        if not resp.text.strip():
            return {}
        # 响应形如 {"addedcontents": [...], "deletedcontents": [...], "batterychanged": ...}
        # 注意没有 "events" 键；任一键值非空即代表有变化
        return {k: v for k, v in resp.json().items() if v}

    async def list_dir(self, href: str) -> list[str]:
        # CCAPI 的普通 contents 列表每页最多只返回 100 条。kind=chunked
        # 让相机返回完整目录，避免存储卡中较早的文件永远不会进入同步队列。
        parts = urlsplit(href)
        query = dict(parse_qsl(parts.query, keep_blank_values=True))
        query.setdefault("kind", "chunked")
        query.setdefault("order", "desc")
        chunked_href = urlunsplit((
            parts.scheme, parts.netloc, parts.path, urlencode(query), parts.fragment,
        ))
        if self._contents_list_mode == "plain":
            resp = await self._get(href)
        else:
            try:
                resp = await self._get(chunked_href)
                self._contents_list_mode = "chunked"
            except httpx.HTTPStatusError as e:
                # 旧 CCAPI 可能不接受 kind/order；退回普通目录响应。
                if e.response.status_code not in (400, 404, 405):
                    raise
                resp = await self._get(href)
                self._contents_list_mode = "plain"
        # kind=chunked 的响应由多个 JSON 对象直接拼接而成，而非一个 JSON 数组。
        # httpx.Response.json() 只能解析单个对象，会在第二段起报 “Extra data”。
        decoder = json.JSONDecoder()
        body = resp.text
        offset = 0
        paths: list[str] = []
        while offset < len(body):
            while offset < len(body) and body[offset].isspace():
                offset += 1
            if offset == len(body):
                break
            chunk, offset = decoder.raw_decode(body, offset)
            if not isinstance(chunk, dict):
                raise ValueError("Each CCAPI directory response chunk must be a JSON object")
            chunk_paths = chunk.get("path", [])
            if not isinstance(chunk_paths, list):
                raise ValueError("The path field in a CCAPI directory response must be an array")
            paths.extend(chunk_paths)
        return paths

    async def list_all_files(self) -> list[str]:
        cached = self._endpoint_cache.get("contents")
        roots = list(dict.fromkeys([
            *([cached] if cached else []),
            *await self._candidate_endpoints("contents", "contents"),
        ]))
        last_missing: httpx.HTTPStatusError | None = None
        for root in roots:
            try:
                files = await self._walk_contents(root)
                self._endpoint_cache["contents"] = root
                return files
            except httpx.HTTPStatusError as e:
                if e.response.status_code not in (404, 405):
                    raise
                last_missing = e
        if last_missing is not None:
            raise last_missing
        raise CameraUnreachable("相机未提供可用的 contents 接口")

    async def _walk_contents(self, root: str) -> list[str]:
        files: list[str] = []
        queue = [root]
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
                    async with client.stream(
                        "GET", self.file_url(href), headers={"Accept-Encoding": "identity"},
                    ) as resp:
                        if resp.status_code == 503:
                            if attempt < 2:
                                await asyncio.sleep(1.5)
                                continue
                            raise CameraUnreachable(
                                "相机持续忙碌（HTTP 503），可能正在处理其他操作或过热，请稍后重试"
                            )
                        resp.raise_for_status()
                        content_type = resp.headers.get("content-type", "").partition(";")[0].lower()
                        if (
                            resp.status_code != 200
                            or resp.headers.get("content-encoding", "identity").lower() != "identity"
                            or content_type in ("application/json", "text/html")
                            or content_type.endswith("+json")
                        ):
                            raise CameraUnreachable("相机返回的文件响应格式不受支持")
                        length = resp.headers.get("content-length")
                        if length is not None and not length.isdecimal():
                            raise CameraUnreachable("相机返回的文件长度无效")
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
                                await run_io(f.write, chunk)
                                size += len(chunk)
                            if not size or (length is not None and size != int(length)):
                                raise CameraUnreachable("相机返回的文件为空或未完整传输")
                            await run_io(f.flush)
                            await run_io(os.fsync, f.fileno())
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
