"""Le premier démarrage respecte le dossier de données, sans base près de l'exe."""
from __future__ import annotations

import json
import os
from pathlib import Path
import sys
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch

APP_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(APP_ROOT))
import main
main._setup_paths()

from src.controllers.data_controller import DataController
from src.annodb import connection


class FirstRunDatabaseTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix="aquameasure-db-location-")
        self.root = Path(self.tmp.name)
        self.bundle = self.root / "App"
        self.legacy = self.bundle / "fish-vision/data/fish_annotations.db"
        self.legacy.parent.mkdir(parents=True)
        self.configured = self.root / "Documents/AquaMeasure - Donnees"
        config = self.root / "storage.json"
        config.write_text(json.dumps({"data_root": str(self.configured)}), encoding="utf-8")
        self.env = patch.dict(os.environ, {"AQUAMEASURE_STORAGE_CONFIG": str(config)})
        self.env.start()
        os.environ.pop("FISH_VISION_DB", None)
        self.default = patch.object(connection, "_DEFAULT_DB", self.legacy)
        self.default.start()
        self.check = SimpleNamespace(_ensure_fv=lambda: None, _repo=lambda: self.bundle, _logs=[])

    def tearDown(self):
        if connection._engine is not None:
            connection._engine.dispose()
        self.default.stop()
        self.env.stop()
        self.tmp.cleanup()

    def _open(self):
        with patch("fish_annotate.is_available", return_value=False):
            self.assertTrue(DataController._check_db(self.check), self.check._logs)

    def test_new_configured_directory_is_created_without_a_second_database(self):
        self._open()
        self.assertTrue((self.configured / "fish_annotations.db").is_file())
        self.assertFalse(self.legacy.exists())

    def test_environment_override_wins_over_configured_directory(self):
        target = self.root / "isolated/test.db"
        os.environ["FISH_VISION_DB"] = str(target)
        self._open()
        self.assertTrue(target.is_file())
        self.assertFalse(self.legacy.exists())
        self.assertFalse((self.configured / "fish_annotations.db").exists())

    def test_no_configuration_keeps_the_portable_default(self):
        Path(os.environ["AQUAMEASURE_STORAGE_CONFIG"]).unlink()
        self._open()
        self.assertTrue(self.legacy.is_file())


if __name__ == "__main__":
    unittest.main()
