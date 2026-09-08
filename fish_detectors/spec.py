"""Description declarative d'un modele de detection poisson.

Un DetectorSpec est purement descriptif : il dit quel backend sait le faire
tourner, ou trouver les poids, et comment interpreter ses classes. Ajouter un
modele au logiciel = ajouter une entree JSON, sans toucher au code.
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

# Contrat de sortie commun a tous les backends. Toute la chaine aval
# (FishController, fish_track, fish_annotate, DataController) consomme ce format.
BBOX_KEYS = ("x1", "y1", "x2", "y2", "conf", "cls_id", "cls_name")
SAM3_CUSTOM_ID = "sam3-custom-prompt"


@dataclass(frozen=True)
class DetectorSpec:
    """Un modele de detection installable et selectionnable dans l'UI."""

    id: str
    label: str
    backend: str
    family: str = "yolo"
    description: str = ""
    origin: str = ""
    homepage: str = ""
    license: str = ""
    classes_label: str = ""
    # Poids deja presents dans le depot : premier chemin existant utilise tel quel.
    bundled_paths: tuple[str, ...] = ()
    # Telechargement a la demande.
    download_url: str = ""
    filename: str = ""
    sha256: str = ""
    size_bytes: int = 0
    # Reglages d'inference.
    imgsz: int = 0
    default_conf: float = 0.25
    # Modeles multi-classes : ne garder que ces classes (sous-chaine, insensible a la casse).
    fish_classes: tuple[str, ...] = ()
    # Options libres transmises au backend (variante rfdetr, seuil IoU, etc.).
    options: dict[str, Any] = field(default_factory=dict)
    # Renseigne par le catalogue : "builtin", "user" ou "remote".
    source: str = "builtin"

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> DetectorSpec:
        missing = [k for k in ("id", "label", "backend") if not data.get(k)]
        if missing:
            raise ValueError(f"Entree catalogue invalide, champs manquants : {missing}")
        known = {f for f in cls.__dataclass_fields__}
        kwargs: dict[str, Any] = {}
        for key, value in data.items():
            if key not in known:
                continue
            if key in ("bundled_paths", "fish_classes"):
                kwargs[key] = tuple(str(v) for v in (value or ()))
            elif key == "options":
                kwargs[key] = dict(value or {})
            else:
                kwargs[key] = value
        return cls(**kwargs)

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "label": self.label,
            "backend": self.backend,
            "family": self.family,
            "description": self.description,
            "origin": self.origin,
            "homepage": self.homepage,
            "license": self.license,
            "classes_label": self.classes_label,
            "bundled_paths": list(self.bundled_paths),
            "download_url": self.download_url,
            "filename": self.filename,
            "sha256": self.sha256,
            "size_bytes": self.size_bytes,
            "imgsz": self.imgsz,
            "default_conf": self.default_conf,
            "fish_classes": list(self.fish_classes),
            "options": dict(self.options),
            "source": self.source,
        }

    def resolve_weights(self, models_dir: Path, app_root: Path) -> Path | None:
        """Chemin local des poids si le modele est deja installe."""
        for rel in self.bundled_paths:
            for base in (app_root, models_dir):
                candidate = Path(rel) if os.path.isabs(rel) else base / rel
                if candidate.exists():
                    return candidate
        if self.filename:
            candidate = models_dir / self.filename
            if candidate.exists():
                return candidate
        return None

    def target_path(self, models_dir: Path) -> Path | None:
        """Ou seront ecrits les poids en cas de telechargement."""
        if not self.filename:
            return None
        return models_dir / self.filename

    @property
    def downloadable(self) -> bool:
        return bool(self.download_url and self.filename)


def sam3_prompt_entry(base_spec: DetectorSpec, prompt: str) -> dict[str, Any]:
    """Construit une variante SAM 3 locale sans modifier le catalogue livre."""
    payload = base_spec.to_dict()
    payload.pop("source", None)
    payload.update({
        "id": SAM3_CUSTOM_ID,
        "label": "SAM 3 - prompt personnalisé",
        "description": (
            f"Détection SAM 3 guidée par le prompt anglais « {prompt} ». "
            "Variante locale créée depuis le gestionnaire de modèles."
        ),
        "classes_label": f"Prompt : {prompt}",
        "download_url": "",
        "filename": "",
        "sha256": "",
        "size_bytes": 0,
    })
    options = dict(payload.get("options") or {})
    options["text_prompt"] = prompt
    payload["options"] = options
    return payload


def normalize_boxes(
    raw: list[dict[str, Any]],
    *,
    width: int,
    height: int,
    fish_classes: tuple[str, ...] = (),
    min_size: float = 2.0,
) -> list[dict[str, Any]]:
    """Nettoie, filtre et trie les detections d'un backend."""
    wanted = tuple(c.lower() for c in fish_classes if c)
    out: list[dict[str, Any]] = []
    for box in raw:
        try:
            x1 = max(0.0, min(float(box["x1"]), float(width)))
            y1 = max(0.0, min(float(box["y1"]), float(height)))
            x2 = max(0.0, min(float(box["x2"]), float(width)))
            y2 = max(0.0, min(float(box["y2"]), float(height)))
        except (KeyError, TypeError, ValueError):
            continue
        if x2 - x1 < min_size or y2 - y1 < min_size:
            continue
        cls_name = str(box.get("cls_name") or "fish")
        if wanted and not any(w in cls_name.lower() for w in wanted):
            continue
        entry = dict(box)
        entry.update({
            "x1": x1,
            "y1": y1,
            "x2": x2,
            "y2": y2,
            "conf": float(box.get("conf", 0.0) or 0.0),
            "cls_id": int(box.get("cls_id", 0) or 0),
            "cls_name": cls_name,
        })
        out.append(entry)
    out.sort(key=lambda b: b["conf"], reverse=True)
    return out
