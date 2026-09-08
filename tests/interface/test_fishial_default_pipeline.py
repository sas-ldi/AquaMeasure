"""Fishial reste le socle de detection et d'identification de l'application."""

from __future__ import annotations

import sys
import types
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import numpy as np

APP_ROOT = Path(__file__).resolve().parents[2] / "src" / "interface"
REPO_ROOT = APP_ROOT.parent
for path in (APP_ROOT, REPO_ROOT):
    if str(path) not in sys.path:
        sys.path.insert(0, str(path))

from fish_detectors.registry import _PREFERRED  # noqa: E402
from src.backend.tracking_worker import TrackingWorker  # noqa: E402
from src.controllers.fish_controller import FishController, _DetectWorker  # noqa: E402


class FishialDefaultPipelineTest(unittest.TestCase):
    def test_fishial_detector_is_the_first_fresh_install_choice(self):
        self.assertEqual(_PREFERRED[0], "fishial-detector-v26")

    def test_detection_worker_calls_the_full_detection_classification_facade(self):
        frame = np.zeros((20, 30, 3), dtype=np.uint8)
        expected = [{
            "x1": 1.0,
            "y1": 2.0,
            "x2": 10.0,
            "y2": 12.0,
            "cls_name": "fish",
            "conf": 0.91,
            "species_name": "Acanthurus triostegus",
            "species_conf": 0.82,
        }]
        fake_facade = types.ModuleType("fish_detect")
        calls = []

        def detect_fish(image, conf, classify_species):
            calls.append((image, conf, classify_species))
            return expected

        fake_facade.detect_fish = detect_fish
        fake_registry = SimpleNamespace(
            is_available=lambda: True,
            active_spec=lambda: SimpleNamespace(label="Fishial"),
            unavailable_reason=lambda: "",
        )
        received = []
        worker = _DetectWorker(frame, 0.37)
        worker.finished_ok.connect(lambda boxes, _image: received.append(boxes))

        with patch.dict(sys.modules, {"fish_detect": fake_facade}), patch(
            "fish_detectors.registry", return_value=fake_registry
        ):
            worker.run()

        self.assertEqual(received, [expected])
        self.assertEqual(len(calls), 1)
        self.assertIs(calls[0][0], frame)
        self.assertEqual(calls[0][1:], (0.37, True))

    def test_qml_payload_keeps_both_detection_and_fishial_scores(self):
        # `_boxes_for_qml` calcule aussi les pictogrammes de comportement.
        # On branche la vraie methode sur le faux controleur plutot qu'un
        # bouchon : le test verifie ainsi la charge utile reellement envoyee
        # a QML, pictogrammes compris.
        fake_controller = SimpleNamespace(
            _measure=SimpleNamespace(leftAbsFrame=4),
            _grazing_intervals=lambda: [],
            _validated_track_label=lambda *_: ("", ""),
            _grazing_index={},
            _grazing_index_source=None,
        )
        fake_controller._active_interval_badges = (
            lambda *args, **kwargs: FishController._active_interval_badges(
                fake_controller, *args, **kwargs,
            )
        )
        rows = FishController._boxes_for_qml(fake_controller, [{
            "x1": 1,
            "y1": 2,
            "x2": 10,
            "y2": 12,
            "conf": 0.91,
            "cls_name": "fish",
            "species_name": "Acanthurus triostegus",
            "species_conf": 0.82,
        }])

        self.assertEqual(rows[0]["speciesName"], "Acanthurus triostegus")
        self.assertEqual(rows[0]["speciesConf"], 0.82)
        self.assertEqual(rows[0]["conf"], 0.91)

    def test_interactive_tracking_frame_requests_fishial_classification(self):
        worker = TrackingWorker()
        expected = [{"track_id": 3, "species_name": "Chromis viridis"}]
        received = []
        worker.frameTracked.connect(
            lambda generation, frame, boxes: received.append(
                (generation, frame, boxes)
            )
        )

        with patch.object(worker, "_process", return_value=expected) as process:
            worker._track_single(17, 6)

        process.assert_called_once_with(
            17,
            generation=6,
            classify_species=True,
        )
        self.assertEqual(received, [(6, 17, expected)])


if __name__ == "__main__":
    unittest.main()
