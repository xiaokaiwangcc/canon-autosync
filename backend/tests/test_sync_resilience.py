import json
import tempfile
import unittest
from pathlib import Path

import httpx

from app.ccapi import CapabilityUnsupported, CameraUnreachable, CanonCamera
from app.config import Config
from app.sync import SyncEngine


class _Response:
    def __init__(self, payload=None, text=None):
        self.payload = payload
        self.text = text if text is not None else json.dumps(payload)

    def json(self):
        return self.payload


class _RecordingCamera(CanonCamera):
    def __init__(self):
        super().__init__("192.0.2.1")
        self.paths = []
        self.response = _Response({"path": []})

    async def _get(self, path, **kwargs):
        self.paths.append(path)
        return self.response


class _RoutingCamera(CanonCamera):
    def __init__(self, routes):
        super().__init__("192.0.2.1")
        self.routes = routes
        self.paths = []

    async def _get(self, path, **kwargs):
        self.paths.append(path)
        result = self.routes.get(path, 404)
        if isinstance(result, int):
            request = httpx.Request("GET", f"http://192.0.2.1:8080{path}")
            response = httpx.Response(result, request=request)
            raise httpx.HTTPStatusError(
                f"HTTP {result}", request=request, response=response,
            )
        return result


class _State:
    def __init__(self):
        self.synced = {}
        self.ignored = set()
        self.last_error = None
        self.last_sync = None

    def is_synced(self, path):
        return path in self.synced

    def is_ignored(self, path):
        return path in self.ignored

    def mark_synced(self, path, size, dest):
        self.synced[path] = {"size": size, "dest": dest}

    def set_last_sync(self):
        self.last_sync = "now"

    def set_error(self, message):
        self.last_error = message

    async def flush(self, force=False):
        pass


class _FakeCamera:
    def __init__(self, paths, failure_path):
        self.paths = paths
        self.failure_path = failure_path
        self.downloaded = []

    async def list_all_files(self):
        return self.paths

    async def download(self, path, dest, **kwargs):
        self.downloaded.append(path)
        if path == self.failure_path:
            raise CameraUnreachable("simulated camera disconnect")
        Path(dest).write_bytes(path.encode())
        return len(path)


class CCAPIContentsTests(unittest.IsolatedAsyncioTestCase):
    async def test_directory_listing_requests_chunked_contents(self):
        camera = _RecordingCamera()

        await camera.list_dir("/ccapi/ver130/contents/sd/100CANON")

        self.assertEqual(
            camera.paths,
            ["/ccapi/ver130/contents/sd/100CANON?kind=chunked&order=desc"],
        )

    async def test_directory_listing_merges_chunked_json_objects(self):
        camera = _RecordingCamera()
        camera.response = _Response(text=(
            '{"path":["/contents/IMG_0003.JPG"]}\n'
            '{"path":["/contents/IMG_0002.JPG","/contents/IMG_0001.JPG"]}'
        ))

        paths = await camera.list_dir("/ccapi/ver130/contents/sd/100CANON")

        self.assertEqual(paths, [
            "/contents/IMG_0003.JPG",
            "/contents/IMG_0002.JPG",
            "/contents/IMG_0001.JPG",
        ])

    async def test_uses_contents_endpoint_advertised_by_camera(self):
        camera = _RoutingCamera({
            "/ccapi/": _Response({
                "url": [
                    "http://192.0.2.1:8080/ccapi/ver100/",
                    "http://192.0.2.1:8080/ccapi/ver110/",
                ],
            }),
            "/ccapi/ver110": _Response({
                "url": ["http://192.0.2.1:8080/ccapi/ver110/contents"],
            }),
            "/ccapi/ver100": _Response({"url": []}),
            "/ccapi/ver110/contents?kind=chunked&order=desc": _Response({
                "path": ["/ccapi/ver110/contents/sd/IMG_0001.JPG"],
            }),
        })

        files = await camera.list_all_files()

        self.assertEqual(files, ["/ccapi/ver110/contents/sd/IMG_0001.JPG"])
        self.assertNotIn(
            "/ccapi/ver130/contents?kind=chunked&order=desc", camera.paths,
        )
        self.assertEqual(camera.capabilities()["endpoints"]["contents"],
                         "/ccapi/ver110/contents")

    async def test_falls_back_across_contents_versions(self):
        camera = _RoutingCamera({
            "/ccapi/": _Response({"message": "Welcome to CCAPI"}),
            "/ccapi/ver110/contents?kind=chunked&order=desc": _Response({
                "path": ["/ccapi/ver110/contents/sd/IMG_0002.JPG"],
            }),
        })

        files = await camera.list_all_files()

        self.assertEqual(files, ["/ccapi/ver110/contents/sd/IMG_0002.JPG"])
        self.assertEqual(camera.capabilities()["endpoints"]["contents"],
                         "/ccapi/ver110/contents")

    async def test_plain_listing_fallback_when_chunked_is_unsupported(self):
        href = "/ccapi/ver100/contents/sd"
        camera = _RoutingCamera({
            f"{href}?kind=chunked&order=desc": 400,
            href: _Response({"path": [f"{href}/IMG_0003.JPG"]}),
        })

        paths = await camera.list_dir(href)
        paths_again = await camera.list_dir(href)

        self.assertEqual(paths, [f"{href}/IMG_0003.JPG"])
        self.assertEqual(paths_again, paths)
        self.assertEqual(camera.paths.count(
            f"{href}?kind=chunked&order=desc",
        ), 1)
        self.assertEqual(camera.capabilities()["contents_list_mode"], "plain")

    async def test_event_endpoint_can_be_discovered_in_another_version(self):
        camera = _RoutingCamera({
            "/ccapi/": _Response({
                "url": ["http://192.0.2.1:8080/ccapi/ver120/"],
            }),
            "/ccapi/ver120": _Response({
                "url": ["http://192.0.2.1:8080/ccapi/ver120/event/polling"],
            }),
            "/ccapi/ver120/event/polling": _Response({
                "addedcontents": ["/contents/IMG_0004.JPG"],
            }),
        })

        events = await camera.event_poll()

        self.assertEqual(events, {
            "addedcontents": ["/contents/IMG_0004.JPG"],
        })
        self.assertEqual(camera.capabilities()["versions"], ["120"])

    async def test_unsupported_optional_capability_is_not_reprobed(self):
        camera = _RoutingCamera({
            "/ccapi/": _Response({"message": "Welcome to CCAPI"}),
        })

        with self.assertRaises(CapabilityUnsupported):
            await camera.temperature()
        request_count = len(camera.paths)
        with self.assertRaises(CapabilityUnsupported):
            await camera.temperature()

        self.assertEqual(len(camera.paths), request_count)
        self.assertEqual(camera.capabilities()["unsupported"], ["temperature"])


class SyncResilienceTests(unittest.IsolatedAsyncioTestCase):
    async def test_failed_file_does_not_stop_later_files(self):
        paths = [
            "/ccapi/ver130/contents/sd/100CANON/IMG_0001.JPG",
            "/ccapi/ver130/contents/sd/100CANON/IMG_0002.JPG",
            "/ccapi/ver130/contents/sd/100CANON/IMG_0003.JPG",
        ]
        state = _State()
        with tempfile.TemporaryDirectory() as backup_dir:
            engine = SyncEngine(Config(nas_path=backup_dir), state)
            camera = _FakeCamera(paths, paths[1])
            engine.camera = lambda: camera

            result = await engine.sync_once()

        self.assertEqual(camera.downloaded, paths)
        self.assertEqual(result["downloaded"], 2)
        self.assertEqual(result["failed"], 1)
        self.assertEqual(set(state.synced), {paths[0], paths[2]})
        self.assertIn("1 个文件备份失败", state.last_error)


class SyncEngineCameraTests(unittest.TestCase):
    def test_camera_client_is_reused_until_address_changes(self):
        engine = SyncEngine(Config(), _State())

        first = engine.camera()
        self.assertIs(first, engine.camera())

        engine.config.camera_ip = "192.0.2.2"
        second = engine.camera()
        self.assertIsNot(first, second)
        self.assertEqual(second.ip, "192.0.2.2")
