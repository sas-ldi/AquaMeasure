"""Autotest des moteurs et poids de la distribution, sans base utilisateur."""
from __future__ import annotations

import json
import os
from pathlib import Path
import sys
import tempfile
import time
import traceback


def run(output: Path) -> int:
    report = {"ok": False, "models": []}
    with tempfile.TemporaryDirectory(prefix="aquameasure-model-test-") as folder:
        root = Path(folder)
        config = root / "storage.json"
        config.write_text(json.dumps({"data_root": str(root)}), encoding="utf-8")
        os.environ["AQUAMEASURE_STORAGE_CONFIG"] = str(config)
        os.environ["FISH_VISION_DB"] = str(root / "test.db")
        os.environ["FISH_VISION_SETTINGS"] = str(root / "settings.json")
        # Détecter toute tentative de téléchargement : les poids fournis
        # doivent être utilisables sans cache Hugging Face préexistant.
        os.environ["HF_HOME"] = str(root / "hf")
        os.environ["HF_HUB_OFFLINE"] = "1"
        try:
            import numpy as np
            import torch
            import fish_detectors as fd
            from transformers import Sam3Model, Sam3Processor
            from fishial_classify import _get_engine

            assert torch.version.cuda is None, "Le runtime livré doit être CPU"
            torch.set_num_threads(min(4, os.cpu_count() or 1))
            frame = np.full((320, 480, 3), 80, dtype=np.uint8)
            registry = fd.registry()
            targets = ["aquameasure-public", "fishial-detector-v26", "cfd-yolov12x",
                       "cfd-rfdetr-nano", "cfd-rfdetr-small", "cfd-rfdetr-medium",
                       "megafishdetector-s", "megafishdetector-m",
                       "megalodon-2024-yolov11", "mbari-315k-yolov8"]
            manifest = Path(sys.executable).parent / "models-manifest.json"
            if manifest.is_file() and "légère" in json.loads(
                    manifest.read_text(encoding="utf-8")).get("edition", ""):
                # L'édition légère n'embarque que Fishial et les petits modèles.
                targets = ["aquameasure-public", "fishial-detector-v26", "megafishdetector-s"]
            for target in targets:
                started = time.perf_counter()
                row = {"id": target, "ok": False}
                try:
                    boxes = registry.detect(frame, conf=0.7, detector_id=target)
                    row.update(ok=True, boxes=len(boxes))
                except Exception:
                    row["error"] = traceback.format_exc()
                row["seconds"] = round(time.perf_counter() - started, 2)
                report["models"].append(row)
                output.parent.mkdir(parents=True, exist_ok=True)
                output.write_text(json.dumps(report, indent=2), encoding="utf-8")
            engine = _get_engine()
            import fishial_classify
            result = fishial_classify.classify_crop(frame)
            report["fishial_classification"] = bool(result)
            report["sam3_runtime"] = bool(Sam3Model and Sam3Processor)
            report["sam3_weights_tested"] = False
            report["ok"] = all(row["ok"] for row in report["models"]) and bool(result)
        except Exception:
            report["error"] = traceback.format_exc()
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_text(json.dumps(report, indent=2), encoding="utf-8")
    return 0 if report["ok"] else 1
