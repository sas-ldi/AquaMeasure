"""Le moteur de calibration travaille dans la racine des donnees configuree.

Installe, il ecrivait la calibration a cote du programme et y cherchait la
fenetre In/Out, alors que l'appli lisait Documents : bouton « Passer a la
mesure » grise et fenetre ignoree.
"""

from __future__ import annotations

import json
import os
import sys
import tempfile
import unittest
from pathlib import Path

APP_ROOT = Path(__file__).resolve().parents[2] / "src" / "interface"
REPO_ROOT = APP_ROOT.parent
# L'appli d'abord (son paquet `src`), puis la racine ou vit aquameasure.py.
for candidate in (REPO_ROOT, APP_ROOT):
    if str(candidate) not in sys.path:
        sys.path.insert(0, str(candidate))

from src.util import paths  # noqa: E402


class EngineDataRootTest(unittest.TestCase):
    def test_le_moteur_suit_la_racine_configuree(self):
        import aquameasure

        before = aquameasure._APP_ROOT
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder) / "AquaMeasure - Donnees"
            config = Path(folder) / "storage.json"
            config.write_text(json.dumps({"data_root": str(root)}), encoding="utf-8")
            old_env = os.environ.get("AQUAMEASURE_STORAGE_CONFIG")
            os.environ["AQUAMEASURE_STORAGE_CONFIG"] = str(config)
            try:
                paths.bind_engine_root()
                self.assertEqual(
                    Path(aquameasure.cam_param("trim_frames.npy")),
                    paths.cam_param("trim_frames.npy"),
                )
            finally:
                aquameasure._APP_ROOT = before
                if old_env is None:
                    os.environ.pop("AQUAMEASURE_STORAGE_CONFIG", None)
                else:
                    os.environ["AQUAMEASURE_STORAGE_CONFIG"] = old_env


class CalibFailureNoticeTest(unittest.TestCase):
    """Un lancement qui n'ecrit rien ne doit pas passer pour un resultat."""

    def setUp(self):
        os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
        from PySide6.QtWidgets import QApplication

        self.app = QApplication.instance() or QApplication([])
        self.tmp = tempfile.TemporaryDirectory()
        cam = Path(self.tmp.name)
        self._orig = paths.camera_params_dir
        paths.camera_params_dir = staticmethod(lambda: cam)
        from src.controllers.calibration_controller import CalibrationController
        from src.controllers.settings_controller import SettingsController

        self.calib = CalibrationController(SettingsController())

    def tearDown(self):
        paths.camera_params_dir = self._orig
        self.tmp.cleanup()

    def test_rien_d_ecrit_affiche_l_echec(self):
        self.calib._r_stamp = self.calib._r_file_stamp()
        self.calib._on_finished()
        self.assertIn("non enregistrée", self.calib.lastRunError)

    def test_nouvelle_calibration_pas_d_alerte(self):
        import numpy as np

        self.calib._r_stamp = self.calib._r_file_stamp()
        np.save(paths.cam_param("R.npy"), np.eye(3))
        self.calib._on_finished()
        self.assertEqual(self.calib.lastRunError, "")


if __name__ == "__main__":
    unittest.main()
