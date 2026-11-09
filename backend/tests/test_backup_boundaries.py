import asyncio
import errno
import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import AsyncMock, patch

import httpx

from app import access, main
from app.backup import BackupTarget, media_path
from app.ccapi import CanonCamera, EventPollUnsupported
from app.config import Config, load_config, save_config
from app.state import State
from app.sync import SyncEngine

PHOTO = "/ccapi/ver130/contents/sd/DCIM/IMG_0001.JPG"
ALIAS = PHOTO.replace("ver130", "ver140")
SECOND = PHOTO.replace("0001", "0002")


class InterruptedStream(httpx.AsyncByteStream):
    async def __aiter__(self):
        yield b"x" * (256 * 1024)
        raise httpx.RemoteProtocolError("interrupted")


class BackupBoundaryTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.workspace = Path(self.enterContext(tempfile.TemporaryDirectory()))
        self.state_file = self.workspace / "state.json"
        self.enterContext(patch("app.state.STATE_FILE", self.state_file))
        self.config = Config(nas_path=str(self.workspace / "backup"), auto_sync=False)
        self.target = BackupTarget.from_config(self.config)
        self.state = State(self.config)
        self.engine = SyncEngine(self.config, self.state)
        self.camera = CanonCamera(self.config.camera_ip)
        self.camera.list_all_files = AsyncMock(return_value=[PHOTO])
        self.camera.delete = AsyncMock()
        self.engine.camera = lambda: self.camera

    async def sync_response(self, response):
        transport = httpx.MockTransport(lambda request: response)
        client = httpx.AsyncClient
        with patch("app.ccapi.httpx.AsyncClient", side_effect=lambda **kwargs: client(transport=transport, **kwargs)):
            return await self.engine.sync_once()

    def app_client(self, password=None, host="localhost", peer=("127.0.0.1", 12345)):
        for name in ("config", "state", "engine"):
            self.enterContext(patch.object(main, name, getattr(self, name)))
        self.enterContext(patch.object(main.app.state, "api_password", password))
        self.enterContext(patch("app.config.CONFIG_FILE", self.workspace / "config.json"))
        return httpx.AsyncClient(
            transport=httpx.ASGITransport(app=main.app, client=peer),
            base_url=f"http://{host}",
        )

    async def test_endpoint_alias_keeps_existing_backup_and_ignore_record(self):
        first = await self.sync_response(httpx.Response(200, content=b"original"))
        self.assertEqual(first["downloaded"], 1)
        self.camera.list_all_files.return_value = [ALIAS, PHOTO]
        self.camera.download = AsyncMock(side_effect=AssertionError("Unexpected download"))
        result = await self.engine.sync_once()
        self.assertEqual(result["downloaded"], 0)
        self.camera.download.assert_not_awaited()
        self.assertEqual(self.target.file_id(PHOTO), self.target.file_id(ALIAS))
        self.assertEqual((self.target.root / media_path(PHOTO)).read_bytes(), b"original")
        async with self.app_client() as client:
            response = await client.delete("/api/files", params={"path": self.target.file_id(PHOTO)})
            self.assertEqual(response.status_code, 200)
            self.assertTrue((await client.get("/api/pending")).json()[0]["ignored"])
            response = await client.post("/api/files/restore", params={"path": ALIAS})
            self.assertEqual(response.json(), {"ok": True})
            self.assertFalse((await client.get("/api/pending")).json()[0]["ignored"])

    async def test_interrupted_download_preserves_existing_file(self):
        existing = self.target.root / media_path(PHOTO)
        existing.parent.mkdir(parents=True)
        existing.write_bytes(b"existing backup")
        result = await self.sync_response(httpx.Response(200, stream=InterruptedStream()))
        self.assertEqual(result["failed"], 1)
        self.assertEqual(existing.read_bytes(), b"existing backup")
        self.assertEqual(list(existing.parent.iterdir()), [existing])
        self.assertFalse(self.state.is_synced(self.target.file_id(PHOTO)))
        self.camera.delete.assert_not_awaited()

    async def test_bad_http_responses_never_publish_or_delete(self):
        self.config.delete_after_sync = True
        for response in (
            httpx.Response(200, content=b""),
            httpx.Response(200, headers={"Content-Length": "10"}, content=b"short"),
            httpx.Response(200, headers={"Content-Type": "application/json"}, content=b"{}"),
            httpx.Response(206, content=b"partial"),
        ):
            with self.subTest(status=response.status_code, headers=response.headers):
                result = await self.sync_response(response)
                self.assertEqual(result["failed"], 1)
                self.assertEqual(list((self.target.root / media_path(PHOTO)).parent.iterdir()), [])
                self.camera.delete.assert_not_awaited()

    async def test_same_name_with_different_bytes_gets_a_separate_destination(self):
        existing = self.target.root / media_path(PHOTO)
        existing.parent.mkdir(parents=True)
        existing.write_bytes(b"existing backup")
        result = await self.sync_response(httpx.Response(200, content=b"new content"))
        self.assertEqual(result["downloaded"], 1)
        [(_, record)] = self.state.snapshot_synced(self.target.root)
        self.assertNotEqual(Path(record["dest"]), existing)
        self.assertEqual(Path(record["dest"]).read_bytes(), b"new content")
        self.assertEqual(existing.read_bytes(), b"existing backup")

    async def test_retry_reuses_byte_identical_file_after_interrupted_commit(self):
        existing = self.target.root / media_path(PHOTO)
        existing.parent.mkdir(parents=True)
        existing.write_bytes(b"complete")
        result = await self.sync_response(httpx.Response(200, content=b"complete"))
        self.assertEqual(result["downloaded"], 1)
        self.assertEqual(list(existing.parent.iterdir()), [existing])
        self.assertEqual(self.state.get_synced(self.target.file_id(PHOTO), self.target.root)["dest"], str(existing))

    @unittest.skipIf(os.name == "nt", "Windows uses an exclusive rename")
    async def test_network_share_without_hard_links_can_publish(self):
        with patch("app.backup.os.link", side_effect=OSError(errno.ENOTSUP, "No hard links")):
            result = await self.sync_response(httpx.Response(200, content=b"complete"))
        self.assertEqual(result["downloaded"], 1)
        self.assertEqual((self.target.root / media_path(PHOTO)).read_bytes(), b"complete")

    async def test_state_failure_never_deletes_and_dirty_state_can_retry(self):
        self.config.delete_after_sync = True
        with patch.object(self.state, "_write_atomic", side_effect=OSError("storage offline")):
            result = await self.sync_response(httpx.Response(200, content=b"complete"))
        self.assertEqual(result["deleted"], 0)
        self.assertEqual(result["delete_failed"], 1)
        self.camera.delete.assert_not_awaited()
        self.assertFalse(self.state_file.exists())
        await self.state.flush(force=True)
        self.assertTrue(State(self.config).is_synced(self.target.file_id(PHOTO)))

    async def test_delete_sees_file_and_record_committed_to_disk(self):
        self.config.delete_after_sync = True

        async with self.app_client() as client:
            async def delete(href):
                file_id = self.target.file_id(href)
                record = State(self.config).get_synced(file_id, self.target.root)
                response = await client.delete("/api/files", params={"path": file_id})
                self.assertEqual(response.status_code, 409)
                self.assertEqual(Path(record["dest"]).read_bytes(), b"complete")

            self.camera.delete.side_effect = delete
            result = await self.sync_response(httpx.Response(200, content=b"complete"))
        self.assertEqual(result["deleted"], 1)
        self.camera.delete.assert_awaited_once_with(PHOTO)

    async def test_stop_cleans_up_staging_and_leaves_source(self):
        self.config.delete_after_sync = True

        async def stop(href, dest, **kwargs):
            dest.write_bytes(b"complete")
            self.engine.request_stop()
            return 8

        self.camera.download = AsyncMock(side_effect=stop)
        self.assertEqual(await self.engine.sync_once(), {"stopped": True})
        self.assertEqual(list((self.target.root / media_path(PHOTO)).parent.iterdir()), [])
        self.camera.delete.assert_not_awaited()

    async def test_cancelled_batch_cleans_up_staging_and_leaves_source(self):
        started = asyncio.Event()

        async def download(href, dest, **kwargs):
            dest.write_bytes(b"partial")
            started.set()
            await asyncio.Future()

        self.camera.download = AsyncMock(side_effect=download)
        task = asyncio.create_task(self.engine.sync_once())
        await asyncio.wait_for(started.wait(), 2)
        task.cancel()
        with self.assertRaises(asyncio.CancelledError):
            await task
        self.assertFalse(self.engine.syncing)
        self.assertEqual(list((self.target.root / media_path(PHOTO)).parent.iterdir()), [])
        self.camera.delete.assert_not_awaited()

    async def test_running_batch_rejects_settings_change_and_other_syncs(self):
        started = asyncio.Event()
        release = asyncio.Event()
        self.camera.list_all_files.return_value = [PHOTO, SECOND]

        async def download(href, dest, **kwargs):
            started.set()
            await release.wait()
            dest.write_bytes(b"photo")
            return 5

        self.camera.download = AsyncMock(side_effect=download)
        new_config = Config(nas_path=str(self.workspace / "new"), delete_after_sync=True)
        async with self.app_client() as client:
            batch = asyncio.create_task(self.engine.sync_once())
            try:
                await asyncio.wait_for(started.wait(), 2)
                self.assertEqual((await client.post("/api/config", json=new_config.model_dump())).status_code, 409)
                self.assertTrue((await client.post("/api/sync")).json()["skipped"])
            finally:
                release.set()
                await batch
            self.camera.delete.assert_not_awaited()
            self.assertEqual(len(self.state.snapshot_synced(self.target.root)), 2)
            self.assertFalse(Path(new_config.nas_path).exists())
            self.assertEqual((await client.post("/api/config", json=new_config.model_dump())).status_code, 200)
            self.assertEqual((await client.get("/api/files")).json()["total"], 0)
            self.assertEqual((await client.post("/api/config", json=self.config.model_dump())).status_code, 200)
            self.assertEqual((await client.get("/api/files")).json()["total"], 2)

    async def test_unsupported_event_poll_honors_interval(self):
        self.config.auto_sync = True
        self.engine.check_camera = AsyncMock(return_value=True)
        self.engine.refresh_camera_info = AsyncMock()
        self.engine.sync_once = AsyncMock()
        self.camera.event_poll = AsyncMock(side_effect=EventPollUnsupported)
        sleeping = asyncio.Event()

        async def wait(interval):
            self.assertEqual(interval, self.config.poll_interval)
            sleeping.set()
            await asyncio.Future()

        self.engine._wait_for_config = wait
        self.engine.start()
        try:
            await asyncio.wait_for(sleeping.wait(), 2)
            self.assertEqual(self.engine.sync_mode, "poll")
            self.engine.sync_once.assert_awaited_once()
        finally:
            await self.engine.stop()

    async def test_migrates_legacy_paths_and_preserves_existing_ignores(self):
        dest = self.target.root / media_path(PHOTO)
        dest.parent.mkdir(parents=True)
        dest.write_bytes(b"legacy")
        self.state_file.write_text(json.dumps({
            "synced": {PHOTO: {"size": 6, "dest": str(dest), "synced_at": "2026-09-06T12:00:00"}},
            "ignored": [SECOND],
            "last_sync": None,
            "last_error": None,
        }), encoding="utf-8")
        migrated = State(self.config)
        self.assertTrue(migrated.is_synced(self.target.file_id(ALIAS)))
        self.assertTrue(migrated.is_ignored(self.target.file_id(SECOND.replace("ver130", "ver140"))))
        await migrated.flush(force=True)
        self.assertTrue(State(self.config).is_synced(self.target.file_id(ALIAS)))

    async def test_migration_does_not_trust_legacy_truncated_file(self):
        dest = self.target.root / media_path(PHOTO)
        dest.parent.mkdir(parents=True)
        dest.write_bytes(b"short")
        self.state_file.write_text(json.dumps({
            "synced": {PHOTO: {"size": 9, "dest": str(dest), "synced_at": "2026-09-06T12:00:00"}},
            "last_sync": None,
            "last_error": None,
        }), encoding="utf-8")
        self.assertFalse(State(self.config).is_synced(self.target.file_id(ALIAS)))
        self.assertEqual(dest.read_bytes(), b"short")

    async def test_backup_root_and_camera_address_scope_file_identity(self):
        other_camera = BackupTarget.from_config(self.config.model_copy(update={"camera_ip": "192.0.2.1"}))
        other_root = BackupTarget.from_config(self.config.model_copy(update={"nas_path": str(self.workspace / "other")}))
        self.assertNotEqual(other_camera.file_id(PHOTO), self.target.file_id(PHOTO))
        self.assertNotEqual(other_root.file_id(PHOTO), self.target.file_id(PHOTO))

    async def test_rejects_untrusted_paths_and_symlink_escapes(self):
        for href in (
            PHOTO.replace("sd", ".."),
            PHOTO.replace("sd", "%2e%2e"),
            PHOTO.replace("sd", "C%3a"),
            PHOTO.replace("sd", "foo%2fbar"),
            "//host" + PHOTO,
            PHOTO + "?kind=info",
        ):
            with self.subTest(href=href):
                with self.assertRaises(ValueError):
                    media_path(href)
        self.target.root.mkdir()
        outside = self.workspace / "elsewhere"
        outside.mkdir()
        try:
            (self.target.root / "sd").symlink_to(outside, target_is_directory=True)
        except OSError:
            self.skipTest("Symlinks unavailable")
        self.camera.download = AsyncMock()
        self.assertEqual((await self.engine.sync_once())["failed"], 1)
        self.camera.download.assert_not_awaited()

    async def test_network_auth_blocks_unauthenticated_and_cross_origin_requests(self):
        async with self.app_client(password="test-password", host="nas.local", peer=("192.0.2.42", 12345)) as client:
            unauthorized = await client.post("/api/config", json=self.config.model_dump())
            self.assertEqual(unauthorized.status_code, 401)
            self.assertIn("Basic", unauthorized.headers["www-authenticate"])
            self.assertEqual((await client.get("/api/status", auth=("canon", "wrong"))).status_code, 401)
            self.assertEqual((await client.get("/api/status", auth=("canon", "test-password"))).status_code, 200)
            self.assertEqual((await client.post(
                "/api/config", json=self.config.model_dump(), auth=("canon", "test-password"),
                headers={"Origin": "http://other.local"},
            )).status_code, 403)
            self.assertEqual((await client.post(
                "/api/config", json=self.config.model_dump(), auth=("canon", "test-password"),
                headers={"Origin": "http://nas.local"},
            )).status_code, 200)

    async def test_preview_requires_a_backup_record_even_for_authenticated_client(self):
        await self.sync_response(httpx.Response(200, content=b"backup"))
        unrelated = self.target.root / "unrelated.txt"
        unrelated.write_bytes(b"private")
        async with self.app_client() as client:
            self.assertEqual((await client.get("/api/preview", params={"path": str(unrelated)})).status_code, 404)
            response = await client.get("/api/preview", params={"id": self.target.file_id(PHOTO)})
            self.assertEqual(response.status_code, 200)
            self.assertEqual(response.content, b"backup")
            self.assertIn("private", response.headers["cache-control"])

    async def test_thumbnail_endpoint_cannot_serve_camera_html(self):
        transport = httpx.MockTransport(lambda request: httpx.Response(
            200, headers={"Content-Type": "text/html"}, text="<script>alert(1)</script>",
        ))
        client_class = httpx.AsyncClient
        async with self.app_client() as client:
            with patch("app.main.httpx.AsyncClient", side_effect=lambda **kwargs: client_class(transport=transport, **kwargs)):
                response = await client.get("/api/thumb", params={"path": PHOTO})
        self.assertEqual(response.status_code, 502)
        self.assertNotIn("<script>", response.text)

    async def test_without_password_both_peer_and_host_must_be_local(self):
        async with self.app_client(host="unrelated.local") as client:
            self.assertEqual((await client.get("/api/status")).status_code, 403)
        async with self.app_client(peer=("192.0.2.42", 12345)) as client:
            self.assertEqual((await client.get("/api/status")).status_code, 403)

    def test_generated_api_password_is_reused(self):
        with patch("app.access.DATA_DIR", self.workspace), patch.dict(
            os.environ, {"CANON_REQUIRE_AUTH": "1", "CANON_API_PASSWORD": ""},
        ):
            password = access.api_password()
            self.assertGreaterEqual(len(password), 32)
            self.assertEqual(access.api_password(), password)
            if os.name != "nt":
                self.assertEqual((self.workspace / "api-password").stat().st_mode & 0o777, 0o600)

    def test_legacy_system_encoding_is_read_and_rewritten_as_utf8(self):
        config_file = self.workspace / "config.json"
        config_file.write_bytes(json.dumps({"nas_path": "café"}, ensure_ascii=False).encode("cp1252"))
        with (
            patch("app.config.CONFIG_FILE", config_file),
            patch("app.config.locale.getpreferredencoding", return_value="cp1252"),
            patch.dict(os.environ, {"NAS_PATH": "", "CANON_IP": ""}),
        ):
            loaded = load_config()
            self.assertEqual(loaded.nas_path, "café")
            loaded.nas_path = "照片"
            save_config(loaded)
            self.assertEqual(load_config().nas_path, "照片")
            self.assertIn("照片", config_file.read_text(encoding="utf-8"))
