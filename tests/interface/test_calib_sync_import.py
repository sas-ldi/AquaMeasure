"""Import << depuis Synchronisation >> de la page Calibration.

Le bouton << Importer les videos >> remplace l'entree enfouie dans le menu
<< reload >> de chaque champ, et publie le decalage + le nombre de frames.
"""

from __future__ import annotations

import os
import sys
import tempfile
import unittest
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import numpy as np
from PySide6.QtWidgets import QApplication

APP_ROOT = Path(__file__).resolve().parents[2] / "src" / "interface"
if str(APP_ROOT) not in sys.path:
    sys.path.insert(0, str(APP_ROOT))

from src.controllers.calibration_controller import CalibrationController  # noqa: E402
from src.controllers.settings_controller import SettingsController  # noqa: E402
from src.util import paths  # noqa: E402


def _write_video(path: Path, frames: int) -> None:
    import cv2

    writer = cv2.VideoWriter(
        str(path), cv2.VideoWriter_fourcc(*"mp4v"), 30.0, (64, 48)
    )
    assert writer.isOpened(), f"encodeur indisponible pour {path}"
    for i in range(frames):
        img = np.full((48, 64, 3), i % 255, dtype=np.uint8)
        writer.write(img)
    writer.release()


class CalibSyncImportTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])
        cls.tmp = tempfile.TemporaryDirectory()
        cls.root = Path(cls.tmp.name)
        cls.cam = cls.root / "camera_parameters"
        cls.cam.mkdir(parents=True)
        cls.left = cls.root / "LEFT.mp4"
        cls.right = cls.root / "RIGHT.mp4"
        _write_video(cls.left, 40)
        _write_video(cls.right, 40)
        cls._orig_dir = paths.camera_params_dir
        paths.camera_params_dir = staticmethod(lambda: cls.cam)

    @classmethod
    def tearDownClass(cls):
        paths.camera_params_dir = cls._orig_dir
        cls.tmp.cleanup()

    def setUp(self):
        for f in self.cam.iterdir():
            f.unlink()
        self.settings = SettingsController()

    def _write_sync(self, left_frame: int, right_frame: int) -> None:
        (self.cam / "videos.txt").write_text(
            f"{self.left}\n{self.right}\n", encoding="utf-8"
        )
        np.save(str(self.cam / "sync_frames.npy"),
                np.asarray([left_frame, right_frame], dtype=np.int64))

    def test_sans_sync_le_bouton_est_indisponible(self):
        c = CalibrationController(self.settings)
        self.assertFalse(c.syncImportAvailable)
        self.assertFalse(c.videosFromSync)

    def test_sans_sync_l_import_explique_pourquoi(self):
        c = CalibrationController(self.settings)
        c.loadVideosFromSync()
        self.assertEqual(c.leftVideo, "")
        self.assertIn("Aucune paire synchronisee", c.logs.fullText)

    def test_fichier_manquant_est_signale(self):
        (self.cam / "videos.txt").write_text(
            f"{self.root / 'absent_g.mp4'}\n{self.root / 'absent_d.mp4'}\n",
            encoding="utf-8",
        )
        c = CalibrationController(self.settings)
        self.assertFalse(c.syncImportAvailable)
        c.loadVideosFromSync()
        self.assertEqual(c.leftVideo, "")
        self.assertIn("introuvable", c.logs.fullText)

    def test_import_charge_la_paire_et_publie_le_decalage(self):
        self._write_sync(73, 85)
        c = CalibrationController(self.settings)
        self.assertTrue(c.syncImportAvailable)
        c.loadVideosFromSync()
        self.assertEqual(Path(c.leftVideo), self.left)
        self.assertEqual(Path(c.rightVideo), self.right)
        self.assertTrue(c.bothVideosSelected)
        self.assertTrue(c.videosFromSync)
        self.assertEqual(c.syncOffsetLabel, "+12 fr")
        self.assertEqual(c.syncFramesLabel, "40 fr")

    def test_decalage_negatif_et_nul(self):
        self._write_sync(85, 73)
        c = CalibrationController(self.settings)
        c.loadVideosFromSync()
        self.assertEqual(c.syncOffsetLabel, "-12 fr")

        self._write_sync(50, 50)
        c2 = CalibrationController(self.settings)
        c2.loadVideosFromSync()
        self.assertEqual(c2.syncOffsetLabel, "0 fr")

    def test_le_signal_est_emis_a_l_import(self):
        self._write_sync(10, 14)
        c = CalibrationController(self.settings)
        seen = []
        c.syncImportChanged.connect(lambda: seen.append(1))
        c.loadVideosFromSync()
        self.assertTrue(seen, "syncImportChanged jamais emis")

    def test_paire_manuelle_n_est_pas_marquee_synchronisee(self):
        self._write_sync(10, 14)
        other = self.root / "AUTRE.mp4"
        _write_video(other, 12)
        c = CalibrationController(self.settings)
        c.importFromSync(str(other), str(self.right))
        self.assertTrue(c.bothVideosSelected)
        self.assertFalse(
            c.videosFromSync,
            "une paire qui n'est pas celle de videos.txt ne doit pas etre "
            "presentee comme synchronisee",
        )


if __name__ == "__main__":
    unittest.main()
