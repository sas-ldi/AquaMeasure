"""Export CSV de l'abondance image par image (comptages MaxN).

Le bouton « Abondance par image (CSV) » de l'onglet Exports produisait en
réalité la **chronologie détaillée** (une ligne par poisson suivi) : le
libellé promettait des comptages, le fichier livrait autre chose, et le format
`csv_abundance` déclaré dans `models.EXPORT_FORMATS` n'était jamais écrit.

Ce module produit le fichier annoncé : une ligne par image comptée, avec le
comptage proposé par l'IA, le comptage corrigé par l'opérateur, et lequel des
deux fait foi. C'est la matière première du **MaxN** - le plus grand nombre de
poissons vus simultanément sur une image, l'indicateur d'abondance standard
des vidéos sous-marines.

Comme partout depuis la phase 0, les index de frame sortis sont **absolus**
dans le fichier vidéo source ; `frame_ref` dit d'où vient chaque ligne.
"""

from __future__ import annotations

import csv
import json
import logging
import uuid
from pathlib import Path
from typing import Any, Iterator, Optional

from sqlalchemy import select
from sqlalchemy.orm import Session

from . import frame_ref as fref
from . import sessions as sessions_mod
from .connection import session_scope
from .models import FrameAbundance, MediaAsset

log = logging.getLogger(__name__)

ABUNDANCE_CSV_FIELDS = [
    "session_date", "site", "session_title", "video_name", "media_id",
    # frame_index : index ABSOLU dans le fichier vidéo source.
    # frame_ref : référentiel d'origine de la ligne (absolute | timeline_legacy).
    "frame_index", "frame_ref", "time_offset_s",
    "ai_count", "manual_count", "count_used", "count_source", "validated",
    "updated_at",
]

COUNT_SOURCE_MANUAL = "manual"
COUNT_SOURCE_AI = "ai"


def _dt_iso(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, str):
        return value
    return value.isoformat(sep=" ", timespec="seconds")


def iter_abundance_rows(session: Session, media: MediaAsset) -> Iterator[dict]:
    """Une ligne par image comptée, index ramenés en absolu."""
    media_id = media.id
    fps = float(media.fps or 0.0)
    offset = sessions_mod.frame_offset_for_media(session, media_id)
    if offset is None:
        offset = fref.timeline_offset()

    session_date = _dt_iso(media.session_date or media.captured_at)
    rows = session.scalars(
        select(FrameAbundance)
        .where(FrameAbundance.media_id == media_id)
        .order_by(FrameAbundance.frame_index)
    ).all()
    for row in rows:
        row_ref = fref.normalize(getattr(row, "frame_ref", None))
        abs_idx = fref.to_absolute(row.frame_index, row_ref, offset)
        if abs_idx is None:
            log.warning(
                "Abondance frame %s en index timeline historique et offset de "
                "sync indisponible - index laissé tel quel.", row.frame_index,
            )
            abs_idx = int(row.frame_index)
        manual = row.manual_count
        if manual is None:
            count_used, count_source = int(row.ai_count or 0), COUNT_SOURCE_AI
        else:
            count_used, count_source = int(manual), COUNT_SOURCE_MANUAL
        yield {
            "session_date": session_date,
            "site": media.site or "",
            "session_title": media.session_title or "",
            "video_name": Path(media.rel_path).name,
            "media_id": media_id,
            "frame_index": abs_idx,
            "frame_ref": row_ref,
            "time_offset_s": round(abs_idx / fps, 3) if fps > 0 else "",
            "ai_count": int(row.ai_count or 0),
            "manual_count": "" if manual is None else int(manual),
            "count_used": count_used,
            "count_source": count_source,
            "validated": 1 if row.validated else 0,
            "updated_at": _dt_iso(row.updated_at),
        }


def default_abundance_filename(media: MediaAsset) -> str:
    from .session_stats import _session_datetime

    stem = Path(media.rel_path).stem
    site = (media.site or "site").replace(" ", "_")
    dt = _session_datetime(media)
    date_part = dt.strftime("%Y%m%d_%H%M") if dt else "session"
    return f"{date_part}_{site}_{stem}_abondance.csv"


def _log_export_run(
    session: Session, media_id: str, output_path: Path, row_count: int
) -> None:
    from .export_media import git_commit
    from .models import EXPORT_STATUS_COMPLETED, ExportRun

    path = Path(output_path)
    try:
        size = path.stat().st_size if path.is_file() else None
    except OSError:
        size = None
    session.add(ExportRun(
        id=str(uuid.uuid4()),
        format="csv_abundance",
        taxonomy_rank="fish",
        filter_json=json.dumps({"media_id": media_id}),
        output_path=str(output_path),
        annotation_count=row_count,
        git_commit=git_commit(),
        total_bytes=size,
        status=EXPORT_STATUS_COMPLETED,
    ))


def export_abundance_csv(
    media_id: str,
    output_path: Path,
    *,
    db_path: Optional[Path] = None,
) -> dict:
    """Écrit le CSV d'abondance **et** son descripteur Frictionless."""
    from .datapackage import write_datapackage_for_csv

    output_path = Path(output_path)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    row_count = 0
    with session_scope(db_path) as session:
        media = session.get(MediaAsset, media_id)
        if media is None:
            raise ValueError(f"media_id introuvable : {media_id}")
        with output_path.open("w", encoding="utf-8-sig", newline="") as handle:
            writer = csv.DictWriter(
                handle, fieldnames=ABUNDANCE_CSV_FIELDS, extrasaction="ignore"
            )
            writer.writeheader()
            for row in iter_abundance_rows(session, media):
                writer.writerow(row)
                row_count += 1
        _log_export_run(session, media_id, output_path, row_count)
    descriptor = write_datapackage_for_csv("abundance", output_path)
    return {
        "path": str(output_path),
        "output_path": str(output_path),
        "row_count": row_count,
        "datapackage": str(descriptor),
    }


def export_abundance_auto_path(
    media_id: str,
    output_dir: Path,
    *,
    db_path: Optional[Path] = None,
) -> dict:
    with session_scope(db_path) as session:
        media = session.get(MediaAsset, media_id)
        if media is None:
            raise ValueError(f"media_id introuvable : {media_id}")
        fname = default_abundance_filename(media)
    return export_abundance_csv(
        media_id, Path(output_dir) / fname, db_path=db_path
    )
