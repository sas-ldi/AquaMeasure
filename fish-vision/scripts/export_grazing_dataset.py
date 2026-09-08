#!/usr/bin/env python3
"""Export grazing intervals and track timeline for ML (pilier 3)."""

from __future__ import annotations

import argparse
import csv
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from src.annodb import frame_ref as fref
from src.annodb.connection import get_db_path, init_db, session_scope
from src.annodb.datapackage import write_datapackage_for_csv
from src.annodb.export_session_csv import resolve_media_id_by_video_name
from src.annodb.models import MediaAsset, TemporalEvent, Track
from src.annodb.session_stats import iter_session_timeline_rows
from src.annodb.sessions import frame_offset_for_media
from sqlalchemy import select

# En-têtes du CSV d'événements. Constante nommée : le descripteur Frictionless
# (`src/annodb/datapackage.py`, ressource « grazing ») déclare exactement ces
# colonnes, et un test compare les deux listes.
GRAZING_CSV_FIELDS = [
    "media_id", "track_id", "external_track_id", "event_id",
    "frame_start", "frame_end", "frame_ref", "duration_frames", "source",
]


def export_grazing_events(media_id: str, out_path: Path, db_path: Path | None = None) -> int:
    """Intervalles de broute en index ABSOLU du fichier source.

    `frame_ref` dit d'où vient chaque ligne ; une ligne historique non
    convertible sort avec ses bornes d'origine et `frame_ref` le signale
    plutôt que de laisser croire à de l'absolu.

    L'offset de conversion vient de la **session** du média quand elle l'a figé
    (celui avec lequel les lignes ont été écrites) ; le `sync_frames.npy`
    courant n'est qu'un repli.

    Un descripteur Frictionless est écrit à côté du CSV.
    """
    count = 0
    fields = GRAZING_CSV_FIELDS
    with session_scope(db_path) as session, out_path.open("w", encoding="utf-8-sig", newline="") as f:
        offset = frame_offset_for_media(session, media_id)
        if offset is None:
            offset = fref.timeline_offset()
        writer = csv.DictWriter(f, fieldnames=fields)
        writer.writeheader()
        rows = session.execute(
            select(TemporalEvent, Track)
            .join(Track, TemporalEvent.track_id == Track.id)
            .where(Track.media_id == media_id, TemporalEvent.event_type == "grazing")
        ).all()
        for event, track in rows:
            row_ref = fref.normalize(getattr(event, "frame_ref", None))
            start = fref.to_absolute(event.frame_start, row_ref, offset)
            end = fref.to_absolute(event.frame_end, row_ref, offset)
            if start is None or end is None:
                print(
                    f"[!] Broute {event.id} en index timeline historique et offset "
                    "de sync indisponible — bornes laissées telles quelles",
                    file=sys.stderr,
                )
                start, end = event.frame_start, event.frame_end
            writer.writerow({
                "media_id": media_id,
                "track_id": track.id,
                "external_track_id": track.external_track_id,
                "event_id": event.id,
                "frame_start": start,
                "frame_end": end,
                "frame_ref": row_ref,
                "duration_frames": end - start + 1,
                "source": event.source,
            })
            count += 1
    write_datapackage_for_csv("grazing", Path(out_path))
    return count


def export_tracks_jsonl(media_id: str, out_path: Path, db_path: Path | None = None) -> int:
    count = 0
    with session_scope(db_path) as session, out_path.open("w", encoding="utf-8") as f:
        media = session.get(MediaAsset, media_id)
        if not media:
            return 0
        for row in iter_session_timeline_rows(session, media):
            f.write(json.dumps(row, ensure_ascii=False) + "\n")
            count += 1
    return count


def main() -> int:
    parser = argparse.ArgumentParser(description="Export grazing + tracks for ML")
    parser.add_argument("--media-id", type=str, default=None)
    parser.add_argument("--video", type=str, default=None)
    parser.add_argument("--out", type=Path, required=True)
    parser.add_argument("--db", type=Path, default=None)
    args = parser.parse_args()

    db_path = args.db or get_db_path()
    if not db_path.exists():
        init_db(db_path)

    media_id = args.media_id
    if not media_id and args.video:
        media_id = resolve_media_id_by_video_name(args.video, db_path=db_path)
    if not media_id:
        print("Erreur : --media-id ou --video requis", file=sys.stderr)
        return 1

    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)
    n1 = export_grazing_events(media_id, out / "grazing_events.csv", db_path=db_path)
    n2 = export_tracks_jsonl(media_id, out / "tracks_timeline.jsonl", db_path=db_path)
    print(f"grazing_events.csv : {n1} lignes")
    print(f"tracks_timeline.jsonl : {n2} lignes")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
