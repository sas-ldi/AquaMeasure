"""Backend Ultralytics - YOLOv8 / v11 / v12 / v26, poids .pt, .onnx ou .engine."""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any

import numpy as np

from fish_detectors.backends.base import DetectorBackend, register_backend


def _mapping_value(source: Any, key: str) -> Any:
    return source.get(key) if isinstance(source, dict) else None


def _architecture_name(model: Any, weights: Path) -> str:
    """Retrouve le nom YOLO declare dans le checkpoint, avec repli fichier."""
    native = getattr(model, "model", None)
    yaml = getattr(native, "yaml", None)
    checkpoint = getattr(model, "ckpt", None)
    train_args = _mapping_value(checkpoint, "train_args")
    overrides = getattr(model, "overrides", None)
    native_args = getattr(native, "args", None)
    scale = str(_mapping_value(yaml, "scale") or "").lower()
    if scale not in {"n", "s", "m", "l", "x"}:
        scale = ""

    candidates = (
        _mapping_value(yaml, "yaml_file"),
        _mapping_value(yaml, "model"),
        _mapping_value(train_args, "model"),
        _mapping_value(overrides, "model"),
        _mapping_value(native_args, "model"),
        weights.name,
    )
    for candidate in candidates:
        if not candidate:
            continue
        match = re.search(r"yolo(?:v)?\s*[-_.]?\s*(\d+)(?:[-_.]?([nslmx]))?", str(candidate), re.I)
        if match:
            suffix = (match.group(2) or scale).lower()
            return f"YOLO{match.group(1)}{suffix}"

    class_name = type(native).__name__ if native is not None else ""
    return class_name if class_name and class_name != "NoneType" else weights.stem


def _input_size(model: Any, configured: int) -> int:
    if configured:
        return int(configured)
    native = getattr(model, "model", None)
    checkpoint = getattr(model, "ckpt", None)
    candidates = (
        _mapping_value(getattr(model, "overrides", None), "imgsz"),
        _mapping_value(getattr(native, "args", None), "imgsz"),
        _mapping_value(_mapping_value(checkpoint, "train_args"), "imgsz"),
    )
    for value in candidates:
        if isinstance(value, (list, tuple)) and value:
            value = max(value)
        try:
            size = int(value)
        except (TypeError, ValueError):
            continue
        if size > 0:
            return size
    return 640


@register_backend
class UltralyticsBackend(DetectorBackend):
    id = "ultralytics"
    label = "Ultralytics YOLO"
    requires = ("ultralytics", "torch")
    weight_suffixes = (".pt", ".onnx", ".engine", ".torchscript")
    supports_tracking = True

    def load(self) -> None:
        from ultralytics import YOLO

        self._model = YOLO(str(self.weights))
        names = getattr(self._model, "names", None)
        if names is None:
            names = getattr(getattr(self._model, "model", None), "names", None)
        if isinstance(names, dict):
            self._names = {int(k): str(v) for k, v in names.items()}
        elif isinstance(names, list):
            self._names = {i: str(n) for i, n in enumerate(names)}
        else:
            self._names = {0: "fish"}

    def infer(self, bgr: np.ndarray, conf: float) -> list[dict[str, Any]]:
        kwargs: dict[str, Any] = {
            "conf": conf,
            "verbose": False,
            "device": self._pick_device(),
        }
        if self.spec.imgsz:
            kwargs["imgsz"] = int(self.spec.imgsz)
        iou = self.spec.options.get("iou")
        if iou is not None:
            kwargs["iou"] = float(iou)

        results = self._model.predict(bgr, **kwargs)
        if not results:
            return []
        boxes = results[0].boxes
        if boxes is None or len(boxes) == 0:
            return []

        xyxy = boxes.xyxy.cpu().numpy()
        scores = boxes.conf.cpu().numpy()
        if boxes.cls is not None:
            cls_ids = boxes.cls.cpu().numpy().astype(int)
        else:
            cls_ids = np.zeros(len(xyxy), dtype=int)

        out: list[dict[str, Any]] = []
        for box, score, cls_id in zip(xyxy, scores, cls_ids):
            out.append({
                "x1": float(box[0]),
                "y1": float(box[1]),
                "x2": float(box[2]),
                "y2": float(box[3]),
                "conf": float(score),
                "cls_id": int(cls_id),
                "cls_name": self._names.get(int(cls_id), "fish"),
            })
        return out

    def native_model(self):
        return self._model

    def inspection_info(self) -> dict[str, Any]:
        info = super().inspection_info()
        task = str(getattr(self._model, "task", None) or "detect").lower()
        task_labels = {
            "detect": "détection",
            "segment": "segmentation (boîtes exploitées)",
            "pose": "pose",
            "classify": "classification",
            "obb": "boîtes orientées",
        }
        info.update({
            "engine": "Ultralytics",
            "architecture": _architecture_name(self._model, self.weights),
            "task": task_labels.get(task, task),
            "taskKey": task,
            "inputSize": _input_size(self._model, self.spec.imgsz),
        })
        return info

    def unload(self) -> None:
        self._model = None
        super().unload()
