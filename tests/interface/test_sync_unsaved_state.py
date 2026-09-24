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

    def test_glisser_une_poignee_n_ecrit_qu_au_repos(self):
        for f in range(100, 121):
            self.sync.setLeftIn(f)
        self.assertFalse((self.cam / "trim_frames.npy").exists())
        self.assertTrue(self.sync._trim_timer.isActive())

    def test_la_fenetre_est_enregistree_au_repos(self):
        # La calibration ne lit que le fichier : ce qu'on voit doit y etre.
        self.sync.setLeftIn(120)
        self.sync.setRightOut(500)
        self.sync._trim_timer.timeout.emit()
        self.assertFalse(self.sync.trimDirty)
        saved = list(np.load(self.cam / "trim_frames.npy"))
        self.assertEqual(saved, [120, 599, 0, 500])

    def test_passer_a_la_calibration_ecrit_la_fenetre_en_attente(self):
        self.sync.setLeftIn(120)
        self.sync.saveCurrentVideos()
        self.assertTrue((self.cam / "trim_frames.npy").is_file())
        self.assertFalse(self.sync._trim_timer.isActive())

    def test_sans_videos_chargees_rien_n_est_ecrit(self):
        self.sync._left_fc = 0
        self.sync.setLeftIn(120)
        self.sync._flush_trim()
        self.assertFalse((self.cam / "trim_frames.npy").exists())

    def _paire_precedente(self, left, right):
        (self.cam / "videos.txt").write_text(f"{left}\n{right}\n", encoding="utf-8")
        np.save(self.cam / "trim_frames.npy", np.array([20000, 25000, 20010, 25010]))
        np.save(self.cam / "sync_frames.npy", np.array([150, 160]))

    def test_nouvelle_paire_repart_de_la_video_entiere(self):
        # Extrait d'un seul passage de mire : la fenetre et la synchro de la
        # longue video precedente ne s'appliquent pas.
        self._paire_precedente("C:/v/longue_G.mp4", "C:/v/longue_D.mp4")
        self.sync._left, self.sync._right = "C:/v/passage2_G.mp4", "C:/v/passage2_D.mp4"
        self.sync._load_persisted_trim()
        self.assertEqual(list(np.load(self.cam / "trim_frames.npy")), [0, 599, 0, 599])
        self.assertFalse((self.cam / "sync_frames.npy").exists())
        self.assertEqual(self.sync.syncOffset, 0)

    def test_changer_seulement_la_gauche_repart_de_la_video_entiere(self):
        import cv2
        from unittest.mock import patch

        new_left = Path(self.tmp.name) / "autre_G.mp4"
        writer = cv2.VideoWriter(str(new_left), cv2.VideoWriter_fourcc(*"mp4v"), 30.0, (64, 48))
        for _ in range(12):
            writer.write(np.zeros((48, 64, 3), dtype=np.uint8))
        writer.release()
        self._paire_precedente("C:/v/p_G.mp4", "C:/v/p_D.mp4")
        self.sync._left, self.sync._right = "C:/v/p_G.mp4", "C:/v/p_D.mp4"
        self.sync._right_in, self.sync._right_out = 20010, 25010
        with patch(
            "src.controllers.sync_controller.QFileDialog.getOpenFileName",
            return_value=(str(new_left), ""),
        ):
            self.sync.pickLeftVideo()
        self.assertFalse((self.cam / "sync_frames.npy").exists())
        self.assertEqual(
            list(np.load(self.cam / "trim_frames.npy")),
            [0, self.sync._left_fc - 1, 0, 599],
        )

    def test_meme_paire_garde_fenetre_et_synchro(self):
        self._paire_precedente("C:/v/p_G.mp4", "C:/v/p_D.mp4")
        np.save(self.cam / "trim_frames.npy", np.array([100, 500, 110, 510]))
        self.sync._left, self.sync._right = "C:/v/p_G.mp4", "C:/v/p_D.mp4"
        self.sync._load_persisted_trim()
        self.assertEqual(self.sync._current_trim(), (100, 500, 110, 510))
        self.assertTrue((self.cam / "sync_frames.npy").is_file())

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
