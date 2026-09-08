"""Backend YOLOv5 historique (torch.hub) - MegaFishDetector et modeles d'epoque.

Les poids YOLOv5 d'origine ne sont pas lisibles par Ultralytics : ils exigent
les definitions de modules du depot ultralytics/yolov5. torch.hub les recupere
et les met en cache (premier chargement = connexion internet requise).
"""

from __future__ import annotations

from typing import Any
from pathlib import Path

import numpy as np

from fish_detectors.backends.base import DetectorBackend, register_backend


@register_backend
class Yolov5HubBackend(DetectorBackend):
    id = "yolov5"
    label = "YOLOv5 (torch.hub)"
    # Le depot YOLOv5 importe pandas et seaborn des le chargement du modele.
    requires = ("torch", "torchvision", "pandas", "seaborn", "requests", "psutil")
    weight_suffixes = (".pt",)

    def load(self) -> None:
        import torch
        from app_paths import app_root
        from ultralytics.utils import checks

        # Une inférence ne doit jamais exécuter pip/uv, y compris depuis Hub.
        # Le moteur amont vérifie ses dépendances au chargement du checkpoint.
        checks.AUTOINSTALL = False

        bundled = Path(app_root()) / "vendor" / "yolov5"
        self._model = torch.hub.load(
            str(bundled) if (bundled / "hubconf.py").is_file() else "ultralytics/yolov5",
            "custom",
            source="local" if (bundled / "hubconf.py").is_file() else "github",
            path=str(self.weights),
            verbose=False,
            trust_repo=True,
        )
        self._model.to(self._pick_device())
        names = getattr(self._model, "names", None)
        if isinstance(names, dict):
            self._names = {int(k): str(v) for k, v in names.items()}
        elif isinstance(names, (list, tuple)):
            self._names = {i: str(n) for i, n in enumerate(names)}
        else:
            self._names = {0: "fish"}

    def infer(self, bgr: np.ndarray, conf: float) -> list[dict[str, Any]]:
        self._model.conf = float(conf)
        iou = self.spec.options.get("iou")
        if iou is not None:
            self._model.iou = float(iou)
        size = int(self.spec.imgsz or 640)
        results = self._model(bgr[:, :, ::-1], size=size)
        pred = results.xyxy[0].cpu().numpy()

        out: list[dict[str, Any]] = []
        for row in pred:
            cls_id = int(row[5])
            out.append({
                "x1": float(row[0]),
                "y1": float(row[1]),
                "x2": float(row[2]),
                "y2": float(row[3]),
                "conf": float(row[4]),
                "cls_id": cls_id,
                "cls_name": self._names.get(cls_id, "fish"),
            })
        return out

    def inspection_info(self) -> dict[str, Any]:
        info = super().inspection_info()
        info.update({
            "engine": "PyTorch Hub",
            "architecture": "YOLOv5",
        })
        return info

    def unload(self) -> None:
        self._model = None
        super().unload()
