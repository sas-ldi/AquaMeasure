"""Reglages modifies mais pas encore enregistres, cote Synchronisation.

Rien ne distinguait un trim deja memorise d'un trim qu'on venait de bouger,
ni une paire posee sur le decalage enregistre d'une paire qu'on avait
redeplacee apres une synchro auto.
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

from src.controllers.sync_controller import SyncController  # noqa: E402
from src.util import paths  # noqa: E402


class SyncUnsavedStateTest(unittest.TestCase):
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
            f.unlink()
        self.sync = SyncController()
        # Paire chargee : 600 frames de chaque cote, aucune decoupe.
        self.sync._left_fc = 600
        self.sync._right_fc = 600
        self.sync._left_out = 599
        self.sync._right_out = 599
        self.sync._left_cur = 0
        self.sync._right_cur = 0
        self.sync._refresh_trim_dirty()
        self.sync._refresh_sync_dirty()

    # ── Découpe In/Out ───────────────────────────────────────────────

    def test_trim_intact_n_alerte_pas(self):
        self.assertFalse(self.sync.trimDirty)

    def test_bouger_une_poignee_leve_l_alerte(self):
        self.sync.setLeftIn(120)
        self.assertTrue(self.sync.trimDirty)

    def test_enregistrer_eteint_l_alerte(self):
        self.sync.setLeftIn(120)
        self.sync.setRightOut(500)
        self.assertTrue(self.sync.trimDirty)
        self.sync.saveTrim()
        self.assertFalse(self.sync.trimDirty)
        self.assertTrue((self.cam / "trim_frames.npy").is_file())

    def test_rebouger_apres_enregistrement_realerte(self):
        self.sync.setLeftIn(120)
        self.sync.saveTrim()
        self.sync.setLeftIn(130)
        self.assertTrue(self.sync.trimDirty)

    def test_revenir_aux_valeurs_enregistrees_eteint_l_alerte(self):
        self.sync.setLeftIn(120)
        self.sync.saveTrim()
        self.sync.setLeftIn(130)
        self.sync.setLeftIn(120)
        self.assertFalse(self.sync.trimDirty)

    def test_le_signal_trim_est_emis(self):
        seen = []
        self.sync.trimDirtyChanged.connect(lambda: seen.append(1))
        self.sync.setLeftIn(120)
        self.assertTrue(seen, "trimDirtyChanged jamais emis")

    # ── Décalage de synchronisation ──────────────────────────────────

    def _apply_sync(self, left_frame: int, right_frame: int):
        self.sync.applyManual(left_frame, right_frame)

    def test_sans_synchro_aucune_alerte(self):
        self.sync.seekLeft(40)
        self.assertFalse(
            self.sync.syncDirty,
            "sans decalage enregistre, il n'y a rien a re-valider",
        )

    def test_juste_apres_la_synchro_aucune_alerte(self):
        self._apply_sync(73, 85)
        self.assertEqual(self.sync.syncOffset, 12)
        self.assertFalse(self.sync.syncDirty)

    def test_deplacer_une_seule_vue_leve_l_alerte(self):
        self._apply_sync(73, 85)
        self.sync.seekLeft(70)
        self.assertTrue(self.sync.syncDirty)
        self.assertEqual(self.sync.pendingSyncOffset, 15)

    def test_deplacer_les_deux_vues_du_meme_pas_n_alerte_pas(self):
        self._apply_sync(73, 85)
        self.sync.seekLeft(103)
        self.sync.seekRight(115)
        self.assertFalse(
            self.sync.syncDirty,
            "avancer les deux vues du meme nombre d'images ne change pas le "
            "decalage : il n'y a rien a re-valider",
        )

    def test_revalider_eteint_l_alerte(self):
        self._apply_sync(73, 85)
        self.sync.seekLeft(70)
        self.assertTrue(self.sync.syncDirty)
        self.sync.applyManualFromCurrent()
        self.assertFalse(self.sync.syncDirty)
        self.assertEqual(self.sync.syncOffset, 15)

    def test_le_signal_sync_est_emis(self):
        self._apply_sync(73, 85)
        seen = []
        self.sync.syncDirtyChanged.connect(lambda: seen.append(1))
        self.sync.seekLeft(70)
        self.assertTrue(seen, "syncDirtyChanged jamais emis")


if __name__ == "__main__":
    unittest.main()
