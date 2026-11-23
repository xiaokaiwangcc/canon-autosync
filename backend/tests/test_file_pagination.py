import tempfile
import unittest
from datetime import datetime, timedelta
from pathlib import Path
from unittest.mock import patch

import httpx

from app import main
from app.backup import BackupTarget, media_path
from app.config import Config
from app.state import State


class FilePaginationTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.workspace = Path(self.enterContext(tempfile.TemporaryDirectory()))
        self.enterContext(patch("app.state.STATE_FILE", self.workspace / "state.json"))
        config = Config(nas_path=str(self.workspace / "backup"))
        self.target = BackupTarget.from_config(config)
        self.target.root.mkdir()
        self.state = State(config)
        # These API tests seed in-memory records and do not exercise state writes.
        self.enterContext(patch.object(self.state, "_schedule_flush"))
        self.enterContext(patch.multiple(main, config=config, state=self.state))
        self.enterContext(patch.object(main.app.state, "api_password", None))
        self.at = datetime(2026, 11, 1, 12)

    async def asyncSetUp(self):
        self.client = await self.enterAsyncContext(httpx.AsyncClient(
            transport=httpx.ASGITransport(app=main.app, client=("127.0.0.1", 12345)),
            base_url="http://localhost",
        ))

    def add_file(self, name, timestamp, folder="DCIM"):
        href = f"/ccapi/ver130/contents/sd/{folder}/{name}"
        file_id = self.target.file_id(href)
        dest = self.target.root / media_path(href)
        with patch("app.state.datetime") as clock:
            clock.now.return_value = timestamp
            self.state.mark_synced(file_id, 10, dest, href=href, root=self.target.root)
        return file_id

    async def list_files(self, **params):
        response = await self.client.get("/api/files", params=params)
        self.assertEqual(response.status_code, 200, response.text)
        return response.json()

    async def test_search_and_pagination_can_see_records_beyond_first_200(self):
        match = self.add_file("Archived-Photo.JPG", self.at)
        for index in range(200):
            self.add_file(f"IMG_{index:04}.JPG", self.at + timedelta(seconds=1), folder="Archived")

        first = await self.list_files(limit=200)
        self.assertEqual(first["total"], 201)
        self.assertEqual(len(first["items"]), 200)
        self.assertNotIn(match, [item["id"] for item in first["items"]])
        second = await self.list_files(limit=200, cursor=first["next_cursor"])
        self.assertEqual(second, await self.list_files(limit=200, offset=200))
        self.assertEqual([item["id"] for item in second["items"]], [match])
        self.assertIsNone(second["next_cursor"])

        search = await self.list_files(q=" aRcHiVeD ")
        self.assertEqual(search["total"], 1)
        self.assertEqual(search["unfiltered_total"], 201)
        self.assertEqual([item["id"] for item in search["items"]], [match])

    async def test_cursor_resumes_after_boundary_removal_and_newer_insert(self):
        ids = sorted([
            self.add_file(f"IMG_{index}.JPG", self.at) for index in range(4)
        ], reverse=True)
        first = await self.list_files(limit=2)
        self.assertEqual([item["id"] for item in first["items"]], ids[:2])

        self.add_file("IMG_new.JPG", self.at + timedelta(seconds=1))
        self.state.remove_synced(ids[1])
        second = await self.list_files(limit=2, cursor=first["next_cursor"])
        self.assertEqual([item["id"] for item in second["items"]], ids[2:])
        self.assertIsNone(second["next_cursor"])

    async def test_invalid_cursor_returns_400(self):
        response = await self.client.get("/api/files", params={"cursor": "invalid"})
        self.assertEqual(response.status_code, 400)
