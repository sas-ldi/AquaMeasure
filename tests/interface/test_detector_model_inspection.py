"""Diagnostic des poids importes et variantes de prompt SAM 3."""

from __future__ import annotations

import importlib
import os
import sys
import threading
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

APP_ROOT = Path(__file__).resolve().parents[2] / "src" / "interface"
REPO_ROOT = APP_ROOT.parent
for path in (APP_ROOT, REPO_ROOT):
    if str(path) not in sys.path:
        sys.path.insert(0, str(path))

from fish_detectors.backends.base import DetectorBackend  # noqa: E402
from fish_detectors.backends.ultralytics_yolo import (  # noqa: E402
    _architecture_name,
    _input_size,
)
from fish_detectors.registry import DetectorRegistry, DetectorStatus  # noqa: E402
from fish_detectors.spec import (  # noqa: E402
    SAM3_CUSTOM_ID,
    DetectorSpec,
    sam3_prompt_entry,
)


class UltralyticsMetadataTest(unittest.TestCase):
    def test_architecture_vient_du_checkpoint_avant_le_nom_du_fichier(self):
        model = SimpleNamespace(
            model=SimpleNamespace(yaml={"yaml_file": "yolo11.yaml", "scale": "x"}, args={}),
            ckpt={},
            overrides={},
        )
        self.assertEqual(_architecture_name(model, Path("renomme-poisson.pt")), "YOLO11x")

    def test_architecture_peut_etre_retrouvee_dans_le_nom(self):
        model = SimpleNamespace(model=SimpleNamespace(yaml={}, args={}), ckpt={}, overrides={})
        self.assertEqual(
            _architecture_name(model, Path("community-yolov12x-1.00.pt")),
            "YOLO12x",
        )

    def test_taille_du_checkpoint_est_preferee_au_repli(self):
        model = SimpleNamespace(
            model=SimpleNamespace(args={}),
            ckpt={"train_args": {"imgsz": [640, 960]}},
            overrides={},
        )
        self.assertEqual(_input_size(model, 0), 960)
        self.assertEqual(_input_size(model, 1280), 1280)


class Sam3PromptVariantTest(unittest.TestCase):
    def test_la_variante_conserve_le_checkpoint_et_change_le_prompt(self):
        base = DetectorSpec(
            id="sam3-fish",
            label="SAM 3 fish",
            backend="sam3",
            family="sam3",
            origin="Meta AI",
            options={"text_prompt": "fish", "checkpoint_path": "sam3.pt"},
        )
        entry = sam3_prompt_entry(base, "reef shark")
        self.assertEqual(entry["id"], SAM3_CUSTOM_ID)
        self.assertEqual(entry["backend"], "sam3")
        self.assertEqual(entry["classes_label"], "Prompt : reef shark")
        self.assertEqual(entry["options"]["text_prompt"], "reef shark")
        self.assertEqual(entry["options"]["checkpoint_path"], "sam3.pt")


class _FakeBackend(DetectorBackend):
    id = "fake"
    label = "Fake Engine"

    def load(self) -> None:
        self._names = {0: "fish"}

    def infer(self, bgr, conf):
        return [{
            "x1": 1,
            "y1": 2,
            "x2": 20,
            "y2": 30,
            "conf": 0.9,
            "cls_id": 0,
            "cls_name": "fish",
        }]

    def inspection_info(self):
        return {
            "engine": "Ultralytics",
            "architecture": "YOLO11x",
            "task": "detection",
            "taskKey": "detect",
            "classNames": ["fish"],
            "classCount": 1,
            "inputSize": 640,
        }


class RegistryInspectionTest(unittest.TestCase):
    def _registry(self, backend):
        spec = DetectorSpec(id="fake", label="Poisson", backend="fake")
        status = DetectorStatus(spec, Path("fake.pt"), [], True)
        registry = DetectorRegistry.__new__(DetectorRegistry)
        registry._lock = threading.RLock()
        registry._active_id = ""
        registry._instance = backend
        registry._instance_id = "fake"
        registry.status = lambda detector_id: status
        registry._ensure_loaded = lambda detector_id: backend
        return registry

    def test_un_chargement_et_une_inference_reussis_valident_le_modele(self):
        spec = DetectorSpec(id="fake", label="Poisson", backend="fake")
        backend = _FakeBackend(spec, Path("fake.pt"))
        backend.load()
        registry = self._registry(backend)
        registry_module = importlib.import_module("fish_detectors.registry")
        with patch.object(registry_module, "get_backend", return_value=_FakeBackend):
            result = DetectorRegistry.inspect(registry, "fake")
        self.assertTrue(result["compatible"])
        self.assertTrue(result["inferencePassed"])
        self.assertEqual(result["architecture"], "YOLO11x")
        self.assertEqual(result["classNames"], ["fish"])
        self.assertEqual(result["classNamesLabel"], "fish")
        self.assertEqual(result["detectionsOnTestImage"], 1)

    def test_une_tache_de_classification_est_refusee(self):
        spec = DetectorSpec(id="fake", label="Poisson", backend="fake")
        backend = _FakeBackend(spec, Path("fake.pt"))
        backend.load()
        backend.inspection_info = lambda: {
            "engine": "Ultralytics",
            "architecture": "YOLO11x-cls",
            "task": "classification",
            "taskKey": "classify",
            "classNames": ["fish"],
            "classCount": 1,
            "inputSize": 640,
        }
        registry = self._registry(backend)
        registry_module = importlib.import_module("fish_detectors.registry")
        with patch.object(registry_module, "get_backend", return_value=_FakeBackend):
            result = DetectorRegistry.inspect(registry, "fake")
        self.assertFalse(result["compatible"])
        self.assertIn("classify", result["error"])


if __name__ == "__main__":
    unittest.main()
