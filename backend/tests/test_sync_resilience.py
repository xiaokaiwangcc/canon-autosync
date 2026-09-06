import tempfile
import unittest
from pathlib import Path

from app.ccapi import CameraUnreachable, CanonCamera
from app.config import Config
from app.sync import SyncEngine


class _Response:
    def __init__(self, payload):
        self.payload = payload

    def json(self):
        return self.payload


class _RecordingCamera(CanonCamera):
    def __init__(self):
        super().__init__("192.0.2.1")
        self.paths = []

    async def _get(self, path, **kwargs):
        self.paths.append(path)
        return _Response({"path": []})


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
