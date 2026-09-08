"""Export session timeline as CSV for scientific analysis."""

from __future__ import annotations

import csv
from pathlib import Path
from typing import Optional

from sqlalchemy import select

from .connection import session_scope
from .datapackage import write_datapackage_for_csv
from .models import MediaAsset
from .session_stats import (
    TIMELINE_CSV_FIELDS,
    default_timeline_filename,
    iter_session_timeline_rows,
    log_export_run,
)


def export_session_timeline_csv(
    media_id: str,
    output_path: Path,
    *,
    db_path: Optional[Path] = None,
) -> dict:
    output_path = Path(output_path)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    row_count = 0
    with session_scope(db_path) as session:
        media = session.get(MediaAsset, media_id)
        if media is None:
            raise ValueError(f"media_id introuvable : {media_id}")
        with output_path.open("w", encoding="utf-8-sig", newline="") as f:
            writer = csv.DictWriter(f, fieldnames=TIMELINE_CSV_FIELDS, extrasaction="ignore")
            writer.writeheader()
            for row in iter_session_timeline_rows(session, media):
                writer.writerow(row)
                row_count += 1
        log_export_run(session, media_id, output_path, row_count)
    # Descripteur Frictionless à côté du CSV : sans lui, personne ne sait que
    # `measurement_mm` est en millimètres ni que `frame_index` est absolu.
    descriptor = write_datapackage_for_csv("timeline", output_path)
    return {
        "path": str(output_path),
        "output_path": str(output_path),
        "row_count": row_count,
        "datapackage": str(descriptor),
    }


def export_session_timeline_auto_path(
    media_id: str,
    output_dir: Path,
    *,
    db_path: Optional[Path] = None,
) -> dict:
    with session_scope(db_path) as session:
        media = session.get(MediaAsset, media_id)
        if media is None:
            raise ValueError(f"media_id introuvable : {media_id}")
        fname = default_timeline_filename(media)
    return export_session_timeline_csv(
        media_id,
        Path(output_dir) / fname,
        db_path=db_path,
    )


def resolve_media_id_by_video_name(
    video_name: str,
    *,
    db_path: Optional[Path] = None,
) -> Optional[str]:
    name = Path(video_name).name
    with session_scope(db_path) as session:
        for media in session.scalars(select(MediaAsset)):
            if Path(media.rel_path).name == name or media.rel_path.endswith(name):
                return media.id
    return None
