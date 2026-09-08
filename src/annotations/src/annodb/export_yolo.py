"""Export YOLO - **dérivé** de COCO, jamais pivot.

YOLO est un format lossy : ni `ignore`, ni attributs, ni identifiants
re-traçables. Il reste produit parce que l'application entraîne avec
Ultralytics, mais il est désormais *converti* depuis la sortie COCO du noyau
(`export_core`) au lieu d'être reconstruit depuis la base par un second
chemin - c'est cette double implémentation qui laissait les deux exports
diverger (split aléatoire par frame d'un côté, aucun de l'autre).

Concrètement, un export YOLO produit **un seul** dossier qui contient à la
fois le COCO (`instances_*.json`, `splits.json`, `manifest.json`, `images/`)
et son dérivé `yolo/` : images partagées par lien matériel, labels tirés de
`instances_<rang>.json` en écartant les images à `ignore`, `data.yaml` généré
depuis la class-map réelle.
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Any, List, Optional

from sqlalchemy.orm import Session

from .export_core import (
    FORMAT_YOLO,
    RANK_FISH,
    ExportIntegrityError,
    export_dataset,
)
from .models import ExportRun

log = logging.getLogger(__name__)

__all__ = ["export_yolo", "ExportIntegrityError"]


def export_yolo(
    session: Session,
    output_root: Path,
    *,
    split_by: str,
    taxonomy_rank: str = RANK_FISH,
    project_ids: Optional[List[str]] = None,
    rectify_images: bool = True,
    **options: Any,
) -> ExportRun:
    """Export YOLO versionné sous `output_root` (COCO + dérivé `yolo/`).

    `split_by` ('media' | 'session' | 'site') est **obligatoire**. Le dossier
    réellement écrit est dans `run.output_path` ; le YAML d'entraînement est
    `<run.output_path>/yolo/data.yaml`.

    Mêmes attributs Python que l'export COCO sur l'objet retourné :
    `run.report`, `run.exclusions`, `run.manifest`.
    """
    result = export_dataset(
        session,
        output_root,
        split_by=split_by,
        taxonomy_rank=taxonomy_rank,
        fmt=FORMAT_YOLO,
        project_ids=project_ids,
        rectify_images=rectify_images,
        **options,
    )
    return result.run


def yolo_data_yaml(output_path: Path | str) -> Path:
    """Chemin du `data.yaml` d'un export YOLO produit par le noyau."""
    return Path(output_path) / "yolo" / "data.yaml"
