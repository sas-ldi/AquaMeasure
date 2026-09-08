"""La mesure recharge et affiche la calibration reellement utilisee.

Cas vecu : deux calibrations pour la meme paire de videos (une calculee, une
importee en ZIP). Revenir sur l'onglet Mesure ne rechargeait rien tant qu'une
paire etait deja ouverte, et rien a l'ecran ne disait laquelle etait chargee.
"""

from __future__ import annotations

import os
import sys
import tempfile
import time
import unittest
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import numpy as np
from PySide6.QtWidgets import QApplication

APP_ROOT = Path(__file__).resolve().parents[1]
if str(APP_ROOT) not in sys.path:
    sys.path.insert(0, str(APP_ROOT))

from src.controllers.app_controller import AppController  # noqa: E402
from src.util import paths  # noqa: E402

PAGE_CALIB = 3
PAGE_MEASURE = 4

CALIB_FILES = ("mtx1.npy", "dist1.npy", "mtx2.npy", "dist2.npy", "R.npy", "T.npy")


class MeasureCalibrationReloadTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])
        cls.tmp = tempfile.TemporaryDirectory()
        cls.cam = Path(cls.tmp.name) / "camera_parameters"
        cls.cam.mkdir(parents=True)
        cls._orig = paths.camera_params_dir
        paths.camera_params_dir = staticmethod(lambda: cls.cam)

    @classmethod
    def tearDownClass(cls):
        paths.camera_params_dir = cls._orig
        cls.tmp.cleanup()

    def setUp(self):
        for f in self.cam.iterdir():
            if f.is_file():
                f.unlink()
        self.ctrl = AppController()
        self.measure = self.ctrl.measure()

    def _write_calibration(self, rmse: float):
        for name in CALIB_FILES:
            np.save(str(self.cam / name), np.eye(3))
        np.save(str(self.cam / "stereo_rmse.npy"), np.asarray([rmse]))

    # ── Provenance affichee ─────────────────────────────────────────

    def test_sans_calibration_le_resume_est_vide(self):
        self.measure.loadCalibration()
        self.assertEqual(self.measure.calibrationSummary, "")

    def test_le_resume_donne_date_origine_et_rmse(self):
        self._write_calibration(0.5418)
        self.measure.loadCalibration()
        summary = self.measure.calibrationSummary
        self.assertIn("RMSE 0.54 px", summary)
        self.assertIn("calcul local", summary)
        self.assertRegex(summary, r"\d{2}/\d{2}/\d{4}")

    def test_une_archive_importee_est_signalee(self):
        self._write_calibration(0.42)
        (self.cam / "manifest.json").write_text(
            '{"format": "aquameasure-calib-v1"}', encoding="utf-8"
        )
        self.measure.loadCalibration()
        self.assertIn("archive importée", self.measure.calibrationSummary)

    def test_le_dossier_est_expose(self):
        self._write_calibration(0.42)
        self.measure.loadCalibration()
        self.assertEqual(Path(self.measure.calibrationDir), self.cam)

    # ── Rechargement en entrant sur l'onglet Mesure ─────────────────

    def test_une_deuxieme_calibration_est_bien_rechargee(self):
        self._write_calibration(0.90)
        self.measure.loadCalibration()
        self.assertIn("RMSE 0.90 px", self.measure.calibrationSummary)

        # Paire deja ouverte : c'est le cas ou l'ancien code ne rechargeait rien.
        self.measure._frame_count = 600
        self.measure.frameCountChanged.emit()

        time.sleep(1.1)  # mtime a la seconde : garantir une date differente
        self._write_calibration(0.31)

        self.ctrl.currentPage = PAGE_CALIB
        self.ctrl.currentPage = PAGE_MEASURE
        self.assertIn(
            "RMSE 0.31 px", self.measure.calibrationSummary,
            "l'onglet Mesure utilise encore l'ancienne calibration",
        )

    def test_le_journal_dit_quelle_calibration_est_chargee(self):
        self._write_calibration(0.5418)
        self.measure.logs.clear()
        self.measure.loadCalibration()
        # logLine est relie en QueuedConnection : il faut laisser tourner la
        # boucle d'evenements pour que les lignes arrivent dans le modele.
        self.app.processEvents()
        text = self.measure.logs.fullText
        self.assertIn("Calibration chargee", text)
        self.assertIn("dossier :", text)
        self.assertIn("RMSE 0.54 px", text)


if __name__ == "__main__":
    unittest.main()
