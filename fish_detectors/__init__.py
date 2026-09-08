"""Systeme de detecteurs poisson enfichables d'AquaMeasure.

Usage courant :

    import fish_detectors as fd

    fd.registry().set_active("cfd-rfdetr-small")
    boxes = fd.detect(frame_bgr, conf=0.4)

Ajouter un modele : une entree JSON dans le catalogue (aucun code).
Ajouter une architecture : une sous-classe de DetectorBackend deposee dans
`plugins/detectors/` (aucune modification du coeur).
"""

from __future__ import annotations

from typing import Any

import numpy as np

from fish_detectors.backends import DetectorBackend, register_backend
from fish_detectors.registry import DetectorRegistry, DetectorStatus, registry
from fish_detectors.spec import BBOX_KEYS, DetectorSpec, normalize_boxes

__all__ = [
    "BBOX_KEYS",
    "DetectorBackend",
    "DetectorRegistry",
    "DetectorSpec",
    "DetectorStatus",
    "class_names",
    "detect",
    "is_available",
    "normalize_boxes",
    "register_backend",
    "registry",
    "unavailable_reason",
]


def detect(
    bgr: np.ndarray,
    conf: float | None = None,
    detector_id: str | None = None,
) -> list[dict[str, Any]]:
    return registry().detect(bgr, conf=conf, detector_id=detector_id)


def is_available(detector_id: str | None = None) -> bool:
    return registry().is_available(detector_id)


def unavailable_reason(detector_id: str | None = None) -> str:
    return registry().unavailable_reason(detector_id)


def class_names(detector_id: str | None = None) -> dict[int, str]:
    return registry().class_names(detector_id)
