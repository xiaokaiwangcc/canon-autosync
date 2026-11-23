import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from app.config import Config, load_config, save_config


class ConfigTests(unittest.TestCase):
    def setUp(self):
        workspace = Path(self.enterContext(tempfile.TemporaryDirectory()))
        self.config_file = workspace / "config.json"
        self.enterContext(patch("app.config.CONFIG_FILE", self.config_file))
        self.enterContext(patch.dict(os.environ, {
            "CANON_IP": "192.168.1.50",
            "NAS_PATH": "/volume/photos",
            "CANON_CONFIG_ENV_DEFAULTS": "1",
        }))

    def test_docker_uses_environment_until_settings_are_saved(self):
        initial = load_config()
        self.assertEqual(initial.camera_ip, "192.168.1.50")
        self.assertEqual(initial.nas_path, "/volume/photos")

        saved = Config(camera_ip="192.168.1.51", nas_path="/volume/photos/studio", auto_sync=False)
        save_config(saved)

        self.assertEqual(load_config(), saved)

    def test_docker_uses_environment_for_unsaved_fields(self):
        self.config_file.write_text('{"camera_ip": "192.168.1.51"}', encoding="utf-8")

        config = load_config()

        self.assertEqual(config.camera_ip, "192.168.1.51")
        self.assertEqual(config.nas_path, "/volume/photos")

    def test_local_environment_overrides_saved_settings(self):
        os.environ.pop("CANON_CONFIG_ENV_DEFAULTS")
        save_config(Config(camera_ip="192.168.1.51", nas_path="/volume/photos/studio", auto_sync=False))

        config = load_config()

        self.assertEqual(config.camera_ip, "192.168.1.50")
        self.assertEqual(config.nas_path, "/volume/photos")
        self.assertFalse(config.auto_sync)
