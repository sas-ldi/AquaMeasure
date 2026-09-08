"""Backend RF-DETR (Roboflow) - architecture du Community Fish Detector 2026.

Le CFD est passe de YOLOv12x a RF-DETR entre 2025 et 2026 : ce backend permet
de suivre cette evolution sans toucher au reste de l'application.

Necessite `pip install rfdetr`.
"""

from __future__ import annotations

from typing import Any

import cv2
import numpy as np

from fish_detectors.backends.base import DetectorBackend, register_backend

_VARIANTS = ("Nano", "Small", "Medium", "Base", "Large")


@register_backend
class RfDetrBackend(DetectorBackend):
    id = "rfdetr"
    label = "RF-DETR (Roboflow)"
    requires = ("rfdetr", "torch")
    weight_suffixes = (".pth", ".pt")

    def load(self) -> None:
        import rfdetr

        variant = str(self.spec.options.get("variant", "")).capitalize()
        candidates = [variant] if variant in _VARIANTS else list(_VARIANTS)

        errors: list[str] = []
        self._model = None
        for name in candidates:
            klass = getattr(rfdetr, f"RFDETR{name}", None)
            if klass is None:
                continue
            try:
                # from_checkpoint deduit la bonne variante depuis le fichier ;
                # certaines versions du package ne l'exposent pas encore.
                loader = getattr(klass, "from_checkpoint", None)
                if callable(loader):
                    self._model = loader(str(self.weights))
                else:
                    self._model = klass(pretrain_weights=str(self.weights))
                self._variant = name
                break
            except Exception as exc:
                errors.append(f"{name}: {exc}")
        if self._model is None:
            raise RuntimeError("Chargement RF-DETR impossible - " + " | ".join(errors))

        names = getattr(self._model, "class_names", None)
        if isinstance(names, dict):
            self._names = {int(k): str(v) for k, v in names.items()}
        elif isinstance(names, (list, tuple)):
            self._names = {i: str(n) for i, n in enumerate(names)}
        else:
            self._names = {0: str(self.spec.options.get("class_name", "fish"))}

    def infer(self, bgr: np.ndarray, conf: float) -> list[dict[str, Any]]:
        rgb = cv2.cvtColor(bgr, cv2.COLOR_BGR2RGB)
        kwargs: dict[str, Any] = {"threshold": float(conf)}
        if self.spec.imgsz:
            size = int(self.spec.imgsz)
            kwargs["resolution"] = (size, size)
        try:
            detections = self._model.predict(rgb, **kwargs)
        except TypeError:
            detections = self._model.predict(rgb, threshold=float(conf))

        xyxy = getattr(detections, "xyxy", None)
        if xyxy is None or len(xyxy) == 0:
            return []
        scores = getattr(detections, "confidence", None)
        cls_ids = getattr(detections, "class_id", None)
        data = getattr(detections, "data", None) or {}
        class_names = data.get("class_name")

        out: list[dict[str, Any]] = []
        for i, box in enumerate(np.asarray(xyxy)):
            cls_id = int(cls_ids[i]) if cls_ids is not None else 0
            if class_names is not None and i < len(class_names):
                cls_name = str(class_names[i])
            else:
                cls_name = self._names.get(cls_id, "fish")
            out.append({
                "x1": float(box[0]),
                "y1": float(box[1]),
                "x2": float(box[2]),
                "y2": float(box[3]),
                "conf": float(scores[i]) if scores is not None else 1.0,
                "cls_id": cls_id,
                "cls_name": cls_name,
            })
        return out

    def inspection_info(self) -> dict[str, Any]:
        info = super().inspection_info()
        variant = str(getattr(self, "_variant", "") or self.spec.options.get("variant", ""))
        info.update({
            "engine": "RF-DETR",
            "architecture": "RF-DETR" + (f" {variant}" if variant else ""),
        })
        return info

    def unload(self) -> None:
        self._model = None
        self._variant = ""
        super().unload()
