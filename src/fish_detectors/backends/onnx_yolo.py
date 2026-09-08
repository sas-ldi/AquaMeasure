"""Backend ONNX Runtime - exports YOLO .onnx sans dependance PyTorch.

Utile pour les postes clients sans GPU ni torch : l'installeur reste leger et
l'inference CPU est nettement plus rapide qu'Ultralytics en mode CPU.
"""

from __future__ import annotations

import json
from typing import Any

import cv2
import numpy as np

from fish_detectors.backends.base import DetectorBackend, register_backend


def _letterbox(bgr: np.ndarray, size: int) -> tuple[np.ndarray, float, float, float]:
    h, w = bgr.shape[:2]
    scale = min(size / w, size / h)
    nw, nh = int(round(w * scale)), int(round(h * scale))
    resized = cv2.resize(bgr, (nw, nh), interpolation=cv2.INTER_LINEAR)
    canvas = np.full((size, size, 3), 114, dtype=np.uint8)
    dx, dy = (size - nw) // 2, (size - nh) // 2
    canvas[dy:dy + nh, dx:dx + nw] = resized
    return canvas, scale, float(dx), float(dy)


@register_backend
class OnnxYoloBackend(DetectorBackend):
    id = "onnx"
    label = "ONNX Runtime (YOLO)"
    requires = ("onnxruntime",)
    weight_suffixes = (".onnx",)

    def load(self) -> None:
        import onnxruntime as ort

        providers = [p for p in ("CUDAExecutionProvider", "CPUExecutionProvider")
                     if p in ort.get_available_providers()]
        self._session = ort.InferenceSession(str(self.weights), providers=providers)
        self._input = self._session.get_inputs()[0]
        shape = self._input.shape
        # shape = [batch, 3, H, W] ; dimensions dynamiques -> repli sur le spec.
        static = [s for s in shape[2:] if isinstance(s, int) and s > 0]
        self._size = static[0] if static else (self.spec.imgsz or 640)
        self._names = self._read_names()

    def _read_names(self) -> dict[int, str]:
        meta = self._session.get_modelmeta().custom_metadata_map or {}
        raw = meta.get("names")
        if raw:
            try:
                parsed = json.loads(raw.replace("'", '"'))
                if isinstance(parsed, dict):
                    return {int(k): str(v) for k, v in parsed.items()}
                if isinstance(parsed, list):
                    return {i: str(v) for i, v in enumerate(parsed)}
            except (ValueError, TypeError):
                pass
        return {0: "fish"}

    def infer(self, bgr: np.ndarray, conf: float) -> list[dict[str, Any]]:
        size = int(self.spec.imgsz or self._size)
        canvas, scale, dx, dy = _letterbox(bgr, size)
        blob = canvas[:, :, ::-1].transpose(2, 0, 1)[None].astype(np.float32) / 255.0
        outputs = self._session.run(None, {self._input.name: blob})
        pred = np.squeeze(outputs[0])
        if pred.ndim != 2:
            return []
        # Ultralytics exporte (4 + nc, N) ; certains exports donnent (N, 4 + nc).
        if pred.shape[0] < pred.shape[1]:
            pred = pred.T

        xywh = pred[:, :4]
        scores_all = pred[:, 4:]
        if scores_all.shape[1] == 0:
            return []
        cls_ids = scores_all.argmax(axis=1)
        scores = scores_all.max(axis=1)
        keep = scores >= conf
        if not keep.any():
            return []
        xywh, scores, cls_ids = xywh[keep], scores[keep], cls_ids[keep]

        boxes_xyxy = np.empty_like(xywh)
        boxes_xyxy[:, 0] = xywh[:, 0] - xywh[:, 2] / 2
        boxes_xyxy[:, 1] = xywh[:, 1] - xywh[:, 3] / 2
        boxes_xyxy[:, 2] = xywh[:, 0] + xywh[:, 2] / 2
        boxes_xyxy[:, 3] = xywh[:, 1] + xywh[:, 3] / 2

        iou = float(self.spec.options.get("iou", 0.45))
        rects = [[float(b[0]), float(b[1]), float(b[2] - b[0]), float(b[3] - b[1])]
                 for b in boxes_xyxy]
        kept = cv2.dnn.NMSBoxes(rects, scores.astype(float).tolist(), conf, iou)
        if len(kept) == 0:
            return []

        out: list[dict[str, Any]] = []
        for i in np.array(kept).ravel():
            b = boxes_xyxy[int(i)]
            cls_id = int(cls_ids[int(i)])
            out.append({
                "x1": (float(b[0]) - dx) / scale,
                "y1": (float(b[1]) - dy) / scale,
                "x2": (float(b[2]) - dx) / scale,
                "y2": (float(b[3]) - dy) / scale,
                "conf": float(scores[int(i)]),
                "cls_id": cls_id,
                "cls_name": self._names.get(cls_id, "fish"),
            })
        return out

    def inspection_info(self) -> dict[str, Any]:
        info = super().inspection_info()
        info.update({
            "engine": "ONNX Runtime",
            "architecture": str(self.spec.options.get("architecture") or "YOLO (ONNX)"),
            "inputSize": int(self.spec.imgsz or self._size),
        })
        return info

    def unload(self) -> None:
        self._session = None
        super().unload()
