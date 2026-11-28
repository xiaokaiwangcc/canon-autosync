import hashlib
import json
import ssl
import subprocess
import tempfile
import threading
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from unittest.mock import patch
from urllib.parse import urlsplit

from app import main
from app.ccapi import CameraUnreachable
from app.config import Config, load_config, save_config
from app.state import State
from app.sync import SyncEngine


ROOT = "/ccapi/ver140/contents"
PHOTO = ROOT + "/IMG.JPG"
EVENT = "/ccapi/ver110/event/polling"
PHOTO_BYTES = b"camera photo"


class CameraHandler(BaseHTTPRequestHandler):
    def log_message(self, *args):
        pass

    def do_PUT(self):
        self.server.requests.append(self.path)
        self.send_response(204)
        self.end_headers()

    def do_DELETE(self):
        self.server.requests.append(self.path)
        self.send_error(405)

    def do_GET(self):
        path = urlsplit(self.path).path
        self.server.requests.append(self.path)
        if path == "/ccapi/":
            body = json.dumps({"paths": [ROOT, EVENT]}).encode()
        elif path == ROOT and "kind=chunked" in self.path:
            # Include 100 non-media entries in the first chunk.
            first = [ROOT + f"/{i}.TXT" for i in range(100)]
            body = (json.dumps({"path": first}) + json.dumps({"path": [PHOTO]})).encode()
        elif path == EVENT:
            body = json.dumps({"addedcontents": [PHOTO]}).encode()
        elif path == PHOTO:
            body = PHOTO_BYTES
        else:
            self.send_error(404)
            return
        self.send_response(200)
        self.send_header("Content-Type", "image/jpeg" if path == PHOTO else "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)


class CameraTransportTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.folder = Path(self.enterContext(tempfile.TemporaryDirectory()))
        self.enterContext(patch("app.config.CONFIG_FILE", self.folder / "config.json"))
        self.enterContext(patch("app.state.STATE_FILE", self.folder / "state.json"))
        self.enterContext(patch.object(main, "_thumb_cache", {}))
        self.server = ThreadingHTTPServer(("127.0.0.1", 0), CameraHandler)
        self.server.requests = []
        self.addCleanup(self.server.server_close)
        self.settings = dict(camera_ip="127.0.0.1", camera_port=self.server.server_port,
                             nas_path=str(self.folder / "photos"))

    def start(self):
        thread = threading.Thread(target=self.server.serve_forever, kwargs={"poll_interval": .02})
        thread.start()
        self.addCleanup(thread.join)
        self.addCleanup(self.server.shutdown)

    def certificate(self, name):
        cert = self.folder / (name + ".pem")
        key = self.folder / (name + ".key")
        subprocess.run(["openssl", "req", "-x509", "-newkey", "rsa:2048", "-nodes",
                        "-subj", "/CN=Camera", "-days", "1", "-keyout", key, "-out", cert],
                       check=True, capture_output=True)
        return cert, key

    async def exercise_sync(self, cfg):
        engine = SyncEngine(cfg, State(cfg))
        self.assertTrue(await engine.check_camera())
        self.assertTrue(await engine._event_wait())
        await engine.camera().disable_autopoweroff()
        self.assertIn("/ccapi/ver100/functions/autopoweroff", self.server.requests)
        with patch.object(main, "engine", engine):
            preview = await main.thumbnail(PHOTO)
            self.assertEqual(b"".join([chunk async for chunk in preview.body_iterator]), PHOTO_BYTES)
        self.assertIn(PHOTO + "?kind=thumbnail", self.server.requests)
        result = await engine.sync_once()
        self.assertEqual(result, {"downloaded": 1, "failed": 0, "deleted": 0})
        self.assertEqual(len(engine.camera_files), 101)
        self.assertEqual((Path(cfg.nas_path) / "IMG.JPG").read_bytes(), PHOTO_BYTES)
        self.assertEqual((await engine.sync_once())["downloaded"], 0)
        return engine.camera()

    async def test_unpaired_config_and_plain_peer(self):
        self.start()
        cfg = Config(**self.settings, camera_protocol="https")
        engine = SyncEngine(cfg, State(cfg))
        self.assertFalse(await engine._event_wait())
        with self.assertRaises(main.HTTPException) as rejected:
            await main.update_config(cfg)
        self.assertEqual(rejected.exception.status_code, 422)
        with self.assertRaises(main.HTTPException) as rejected:
            await main.camera_fingerprint(main.CameraAddress(**self.settings))
        self.assertEqual(rejected.exception.status_code, 502)

    async def test_existing_http_configuration(self):
        self.start()
        (self.folder / "config.json").write_text(json.dumps(self.settings))
        cfg = load_config()
        self.assertEqual(cfg.camera_protocol, "http")
        await self.exercise_sync(cfg)

    async def test_https_pairing_restart_and_replaced_certificate(self):
        cert, key = self.certificate("first")
        context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
        context.load_cert_chain(cert, key)
        self.server.socket = context.wrap_socket(self.server.socket, server_side=True)
        self.start()
        cfg = Config(**self.settings, camera_protocol="https")
        with self.assertRaises(CameraUnreachable):
            await SyncEngine(cfg, State(cfg)).camera().ping()
        observed = await main.camera_fingerprint(main.CameraAddress(**self.settings))
        self.assertEqual(self.server.requests, [])
        self.assertEqual(observed["fingerprint"], hashlib.sha256(
            ssl.PEM_cert_to_DER_cert(cert.read_text())).hexdigest())
        cfg.camera_fingerprint = observed["fingerprint"]
        save_config(cfg)
        camera = await self.exercise_sync(load_config())
        context.load_cert_chain(*self.certificate("replacement"))
        self.server.requests.clear()
        with self.assertRaisesRegex(CameraUnreachable, "certificate changed"):
            await camera.delete(PHOTO)
        self.assertEqual(self.server.requests, [])
