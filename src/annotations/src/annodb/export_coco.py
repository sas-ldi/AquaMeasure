"""Export COCO - enveloppe mince autour du noyau d'export.

COCO est le **pivot** : mécanisme d'`ignore` (décisif pour le NA), identifiants
re-traçables vers SQLite, champs libres tolérés. Toute la mécanique (collecte,
class-map, split par groupe, manifeste, empreintes) vit dans `export_core` :
ce module ne fait plus qu'appeler le noyau avec le format `coco`, pour que
COCO et YOLO ne puissent plus diverger.
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Any, List, Optional

from sqlalchemy.orm import Session

from .export_core import (
    FORMAT_COCO,
    RANK_FISH,
    UNIDENTIFIED_CATEGORY_NAME,
    ExportIntegrityError,
    export_dataset,
)
from .models import ExportRun

log = logging.getLogger(__name__)

__all__ = ["export_coco", "UNIDENTIFIED_CATEGORY_NAME", "ExportIntegrityError"]


def export_coco(
    session: Session,
    output_root: Path,
    *,
    split_by: str,
    taxonomy_rank: str = RANK_FISH,
    project_ids: Optional[List[str]] = None,
    rectify_images: bool = True,
    **options: Any,
) -> ExportRun:
    """Export COCO versionné sous `output_root`.

    `split_by` ('media' | 'session' | 'site') est **obligatoire** : il n'existe
    pas de grain de split par défaut qui soit sûr.

    `output_root` est la **racine** des exports, pas le dossier final : le
    noyau y crée `<nom>_v<version>_<date>_<hash8>/`. Le chemin réel est dans
    `run.output_path`.

    L'`ExportRun` retourné porte, en plus des colonnes persistées, trois
    attributs Python : `run.report` (détail, aussi écrit dans
    `export_report.json`), `run.exclusions` (annotations écartées et leur
    raison) et `run.manifest` (le manifeste scellé).
    """
    result = export_dataset(
        session,
        output_root,
        split_by=split_by,
        taxonomy_rank=taxonomy_rank,
        fmt=FORMAT_COCO,
        project_ids=project_ids,
        rectify_images=rectify_images,
        **options,
    )
    return result.run
