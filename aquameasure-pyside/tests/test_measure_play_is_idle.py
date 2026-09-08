"""Une lecture simple ne doit declencher aucun calcul par frame.

Regression : chaque notification de position du lecteur natif appelait
seekFromLeftAbsFrame, qui emettait frameIndexChanged, qui relancait le
comptage d'abondance (upsert + select SQLite), la republication des overlays
et le rechargement des bbox - environ 50 ms de travail Python par
notification, soit plus que le budget d'une frame a 30 img/s.
"""

from __future__ import annotations

import os
import sys
import unittest
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtWidgets import QApplication

APP_ROOT = Path(__file__).resolve().parents[1]
if str(APP_ROOT) not in sys.path:
    sys.path.insert(0, str(APP_ROOT))

from src.controllers.app_controller import AppController  # noqa: E402


class MeasurePlayIsIdleTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self):
        self.ctrl = AppController()
        self.measure = self.ctrl.measure()
        self.data = self.ctrl.data()
        self.fish = self.ctrl.fish()

        # Timeline factice : pas besoin de fichiers video pour mesurer le
        # travail declenche par un deplacement de tete de lecture.
        svc = self.measure._service
        svc._total_frames = 600
        svc._left_start = 0
        svc._sync_delta = 0
        self.measure._frame_count = 600
        self.measure.frameCountChanged.emit()

        self.calls = {"abundance": 0, "overlay": 0, "boxes": 0, "grazing": 0}
        self._patch(self.data, "refreshFrameAbundance", "abundance")
        self._patch(self.fish, "_publish_overlay", "overlay")
        self._patch(self.fish, "_load_manual_boxes_for_frame", "boxes")
        self.data.grazingOverlayChanged.connect(
            lambda: self.calls.__setitem__("grazing", self.calls["grazing"] + 1)
        )

    def _patch(self, obj, name, key):
        original = getattr(obj, name)

        def counted(*args, **kwargs):
            self.calls[key] += 1
            return original(*args, **kwargs)

        setattr(obj, name, counted)

    def _reset(self):
        for key in self.calls:
            self.calls[key] = 0

    def _play(self, playing: bool):
        self.measure._playing = playing
        self.measure.playingChanged.emit()

    def test_lecture_ne_recharge_ni_bbox_ni_overlay(self):
        self._play(True)
        self._reset()
        for frame in range(60):
            self.measure.seekFromLeftAbsFrame(frame)
        self.assertEqual(self.calls["boxes"], 0, "bbox rechargees pendant la lecture")
        self.assertEqual(
            self.calls["overlay"], 0, "overlay republie pendant la lecture"
        )
        self.assertEqual(
            self.calls["grazing"], 0, "overlay de broutage emis pendant la lecture"
        )

    def test_lecture_ne_touche_pas_la_base_dabondance(self):
        self._play(True)
        self.data._db_ok = True
        self.data._media_id = "media-test"
        touched = []
        self.data._set_frame_count_current = lambda *a: touched.append(a)
        self._reset()
        for frame in range(60):
            self.measure.seekFromLeftAbsFrame(frame)
        self.assertEqual(
            touched, [], "le comptage d'abondance a tourne pendant la lecture"
        )

    def test_la_timeline_avance_quand_meme(self):
        self._play(True)
        self.measure.seekFromLeftAbsFrame(42)
        self.assertEqual(
            self.measure.frameIndex, 42,
            "la tete de lecture doit continuer a avancer pendant le play",
        )

    def test_la_pause_rejoue_le_travail_reporte(self):
        self._play(True)
        for frame in range(60):
            self.measure.seekFromLeftAbsFrame(frame)
        self._reset()
        self._play(False)
        self.assertGreaterEqual(
            self.calls["boxes"], 1, "bbox non rechargees au retour en pause"
        )
        self.assertGreaterEqual(
            self.calls["overlay"], 1, "overlay non republie au retour en pause"
        )
        self.assertGreaterEqual(
            self.calls["grazing"], 1,
            "overlay de broutage non rafraichi au retour en pause",
        )

    def test_hors_lecture_le_travail_par_frame_a_bien_lieu(self):
        self._play(False)
        self._reset()
        self.measure.seekFromLeftAbsFrame(12)
        self.assertGreaterEqual(self.calls["boxes"], 1)
        self.assertGreaterEqual(self.calls["overlay"], 1)
        self.assertGreaterEqual(self.calls["abundance"], 1)


if __name__ == "__main__":
    unittest.main()
