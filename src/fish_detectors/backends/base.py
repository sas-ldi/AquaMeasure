"""Contrat des backends de detection et leur enregistrement.

Un backend sait faire tourner une *famille* de modeles (Ultralytics YOLO,
RF-DETR, ONNX Runtime, segmentation Fishial...). Ajouter une nouvelle
architecture au logiciel = ajouter une sous-classe et l'enregistrer, sans
toucher au reste de la chaine.
"""

from __future__ import annotations

import importlib
import importlib.util
from abc import ABC, abstractmethod
from pathlib import Path
from typing import Any, ClassVar

import numpy as np

from fish_detectors.spec import DetectorSpec, normalize_boxes


class DetectorBackend(ABC):
    """Moteur d'inference pour une famille de modeles."""

    id: ClassVar[str] = ""
    label: ClassVar[str] = ""
    #: modules Python necessaires, testes sans les importer reellement
    requires: ClassVar[tuple[str, ...]] = ()
    #: extensions de poids acceptees (vide = tout)
    weight_suffixes: ClassVar[tuple[str, ...]] = ()
    #: le modele natif expose un .track() exploitable par ByteTrack
    supports_tracking: ClassVar[bool] = False
    #: False pour les modeles dont les poids sont recuperes a la demande (ex. SAM 3 / HF)
    needs_local_weights: ClassVar[bool] = True

    def __init__(self, spec: DetectorSpec, weights: Path):
        self.spec = spec
        self.weights = weights
        self._names: dict[int, str] = {}

    # -- disponibilite ----------------------------------------------------

    @classmethod
    def missing_requirements(cls) -> list[str]:
        return [m for m in cls.requires if importlib.util.find_spec(m) is None]

    @classmethod
    def is_installed(cls) -> bool:
        return not cls.missing_requirements()

    # -- cycle de vie -----------------------------------------------------

    @abstractmethod
    def load(self) -> None:
        """Charge le modele en memoire. Appele une seule fois."""

    @abstractmethod
    def infer(self, bgr: np.ndarray, conf: float) -> list[dict[str, Any]]:
        """Inference brute : retourne des boites au format BBOX_KEYS."""

    def unload(self) -> None:
        self._names = {}

    def class_names(self) -> dict[int, str]:
        return dict(self._names)

    def native_model(self) -> Any | None:
        """Objet modele sous-jacent, pour les usages avances comme le tracking."""
        return None

    def inspection_info(self) -> dict[str, Any]:
        """Metadonnees affichees apres le chargement d'un modele.

        Le diagnostic reste volontairement generique : chaque backend peut
        enrichir ou corriger ces informations quand sa bibliotheque expose des
        metadonnees plus precises.
        """
        names = self.class_names()
        return {
            "engine": self.label or self.id,
            "architecture": self.spec.family or self.id,
            "task": "détection",
            "taskKey": "detect",
            "classNames": [names[key] for key in sorted(names)],
            "classCount": len(names),
            "inputSize": int(self.spec.imgsz or 640),
        }

    # -- utilitaire partage -----------------------------------------------

    def detect(self, bgr: np.ndarray, conf: float) -> list[dict[str, Any]]:
        if bgr is None or getattr(bgr, "size", 0) == 0:
            return []
        h, w = bgr.shape[:2]
        raw = self.infer(bgr, conf)
        return normalize_boxes(
            raw,
            width=w,
            height=h,
            fish_classes=self.spec.fish_classes,
        )

    def _pick_device(self) -> str:
        try:
            torch = importlib.import_module("torch")
        except Exception:
            return "cpu"
        try:
            return "cuda" if torch.cuda.is_available() else "cpu"
        except Exception:
            return "cpu"


_BACKENDS: dict[str, type[DetectorBackend]] = {}


def register_backend(backend: type[DetectorBackend]) -> type[DetectorBackend]:
    """Enregistre un backend. Utilisable comme decorateur par un plugin tiers."""
    if not backend.id:
        raise ValueError(f"{backend.__name__} doit definir un id")
    _BACKENDS[backend.id] = backend
    return backend


def get_backend(backend_id: str) -> type[DetectorBackend] | None:
    return _BACKENDS.get(backend_id)


def all_backends() -> dict[str, type[DetectorBackend]]:
    return dict(_BACKENDS)
