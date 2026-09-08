"""Une seule calibration, un seul dossier : camera_parameters/.

Il y avait deux emplacements - la racine (moteur classique + import ZIP) et
camera_parameters/profiles/fast_v2/ (moteur v2) - departages par
active_profile.txt. Une calibration importee en ZIP atterrissait a la racine
pendant que la mesure lisait le profil : deux calibrations pour la meme paire
de videos, et aucun moyen de savoir laquelle servait.
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

APP_ROOT = Path(__file__).resolve().parents[1]
if str(APP_ROOT) not in sys.path:
    sys.path.insert(0, str(APP_ROOT))

from src.controllers.settings_controller import SettingsController  # noqa: E402
from src.util import paths  # noqa: E402


def _write_calibration(directory: Path, rmse: float) -> None:
    directory.mkdir(parents=True, exist_ok=True)
    for name in paths.CALIB_REQUIRED_FILES:
        np.save(str(directory / name), np.eye(3))
    np.save(str(directory / "stereo_rmse.npy"), np.asarray([rmse]))


class SingleCalibrationLocationTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])
        cls._orig = paths.camera_params_dir

    @classmethod
    def tearDownClass(cls):
        paths.camera_params_dir = cls._orig

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.cam = Path(self.tmp.name) / "camera_parameters"
        self.cam.mkdir(parents=True)
        paths.camera_params_dir = staticmethod(lambda: self.cam)

    def tearDown(self):
        self.tmp.cleanup()

    # ── Un seul dossier ──────────────────────────────────────────────

    def test_le_dossier_de_calibration_est_toujours_la_racine(self):
        (self.cam / "active_profile.txt").write_text("fast_v2\n", encoding="utf-8")
        self.assertEqual(paths.calib_profile_dir(), self.cam)
        self.assertEqual(paths.calib_param("mtx1.npy"), self.cam / "mtx1.npy")

    def test_un_ancien_profil_n_est_plus_lu(self):
        legacy = self.cam / "profiles" / "fast_v2"
        _write_calibration(legacy, 0.90)
        (self.cam / "active_profile.txt").write_text("fast_v2\n", encoding="utf-8")
        _write_calibration(self.cam, 0.31)
        self.assertIn("RMSE 0.31 px", paths.calibration_summary())

    # ── Migration ────────────────────────────────────────────────────

    def test_une_calibration_coincee_dans_profiles_est_remontee(self):
        legacy = self.cam / "profiles" / "fast_v2"
        _write_calibration(legacy, 0.77)
        self.assertFalse(paths.calib_profile_complete())
        note = paths.migrate_legacy_calib_profile()
        self.assertTrue(paths.calib_profile_complete())
        self.assertIn("fast_v2", note)
        self.assertIn("RMSE 0.77 px", paths.calibration_summary())

    def test_la_migration_n_ecrase_jamais_la_calibration_visible(self):
        legacy = self.cam / "profiles" / "fast_v2"
        _write_calibration(legacy, 0.90)
        _write_calibration(self.cam, 0.31)
        note = paths.migrate_legacy_calib_profile()
        self.assertIn("RMSE 0.31 px", paths.calibration_summary())
        self.assertIn("ignores", note)

    def test_la_migration_ne_supprime_rien(self):
        legacy = self.cam / "profiles" / "fast_v2"
        _write_calibration(legacy, 0.77)
        paths.migrate_legacy_calib_profile()
        self.assertTrue((legacy / "mtx1.npy").is_file())

    def test_sans_ancien_profil_la_migration_ne_dit_rien(self):
        _write_calibration(self.cam, 0.42)
        self.assertEqual(paths.migrate_legacy_calib_profile(), "")

    # ── Plus de choix de moteur ni de profil ─────────────────────────

    def test_le_moteur_n_est_plus_un_reglage(self):
        settings = SettingsController()
        self.assertFalse(
            hasattr(settings, "calibEngine"),
            "il ne reste qu'un moteur : plus rien a choisir ni a passer",
        )

    def test_les_reglages_n_exposent_plus_de_choix(self):
        settings = SettingsController()
        for name in (
            "calibEngineOptions",
            "calibProfileOptions",
            "activeCalibProfile",
            "calibEngineIsFastV2",
            "calibEngineLabel",
            "setCalibEngine",
            "setActiveCalibProfile",
        ):
            self.assertFalse(
                hasattr(settings, name),
                f"{name} devrait avoir disparu avec le choix de calibration",
            )

    def test_le_resume_des_reglages_ne_parle_plus_de_moteur(self):
        settings = SettingsController()
        summary = settings.calibSettingsSummary
        self.assertNotIn("Rapide v2", summary)
        self.assertNotIn("Classique", summary)
        self.assertIn("saut", summary)


if __name__ == "__main__":
    unittest.main()
