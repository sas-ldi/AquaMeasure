#!/usr/bin/env python3
"""Un bouton, un paquet autonome pour une session AquaMeasure."""

from __future__ import annotations

import argparse
import json
import re
import shutil
import sys
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from src.annodb.connection import get_db_path, session_scope
from src.annodb.export_core import FORMAT_COCO, FORMAT_MOT, export_dataset
from src.annodb.models import CaptureSession
from src.annodb.export_session_tables import CSV_README, export_session_tables
from src.annodb.sessions import (
    STATUS_EXPORTED,
    session_as_dict,
    session_media_ids,
)
from src.annodb.storage_config import exports_dir


def _safe_name(value: str) -> str:
    text = re.sub(r"[^A-Za-z0-9._-]+", "_", value.strip()).strip("_.-")
    return text or "session"


def _available_media(preview: dict) -> tuple[list[dict], list[dict]]:
    items: list[dict] = []
    for pair in preview.get("pairs") or []:
        for role in ("left", "right"):
            media = dict(pair.get(role) or {})
            if not media.get("media_id"):
                continue
            media["pair_number"] = int(pair.get("position", 0)) + 1
            media["role"] = "gauche" if role == "left" else "droite"
            items.append(media)
    return (
        [row for row in items if row.get("available")],
        [row for row in items if not row.get("available")],
    )


def main() -> int:
    parser = argparse.ArgumentParser(description="Exporte une session complète")
    parser.add_argument("--session", required=True)
    parser.add_argument("--db", type=Path, default=None)
    parser.add_argument("--out", type=Path, default=None)
    parser.add_argument("--version", default="1.0.0")
    parser.add_argument(
        "--format", choices=("coco", "tracking", "csv"), default="coco",
        help="coco = détection, tracking = COCO-VID + MOT, csv = registre de session",
    )
    # Compatibilité avec l'ancien bouton. La nouvelle interface utilise le
    # choix explicite --format.
    parser.add_argument("--include-tracking", action="store_true")
    parser.add_argument("--allow-missing", action="store_true")
    args = parser.parse_args()

    db_path = args.db or get_db_path()
    with session_scope(db_path) as db:
        capture = db.get(CaptureSession, args.session)
        if capture is None:
            raise ValueError("Session introuvable")
        preview = session_as_dict(db, capture, with_stats=True)
        media_ids = session_media_ids(db, capture)
        if not media_ids:
            raise ValueError("Cette session ne contient encore aucune vidéo")
        available, missing = _available_media(preview)
        all_media_rows = available + missing
        legacy_bundle = bool(args.include_tracking)
        export_format = "tracking" if legacy_bundle else args.format
        needs_source_video = export_format in ("coco", "tracking")
        if needs_source_video and missing and not args.allow_missing:
            names = ", ".join(row.get("name") or row["media_id"] for row in missing)
            raise FileNotFoundError(
                f"Vidéo(s) manquante(s) : {names}. Replacez-les ou autorisez "
                "explicitement l'export des vidéos disponibles."
            )
        if needs_source_video and not available:
            raise FileNotFoundError("Aucune vidéo de cette session n'est accessible")

        stamp = datetime.now()
        safe = _safe_name(preview.get("name") or "session")
        package_root = (args.out or exports_dir()) / (
            f"session_{safe}_{export_format}_{stamp:%Y%m%d_%H%M%S}"
        )
        package_root.mkdir(parents=True, exist_ok=False)
        selected_media = available if needs_source_video else all_media_rows
        export_media_ids = [str(row["media_id"]) for row in selected_media]

        try:
            coco = None
            tracking = None
            csv_path = None
            csv_row_count = 0
            csv_tables = None
            if export_format == "coco" or legacy_bundle:
                coco = export_dataset(
                    db,
                    package_root / "coco",
                    split_by="session",
                    taxonomy_rank="species",
                    fmt=FORMAT_COCO,
                    media_ids=export_media_ids,
                    dataset_name=safe,
                    dataset_version=args.version,
                    min_instances=1,
                    min_media=1,
                    strict_grouping=None,
                    mark_sessions_exported=False,
                )
            if export_format == "tracking":
                if int(preview.get("track_count") or 0) <= 0:
                    raise ValueError("Cette session ne contient aucune piste de tracking")
                tracking = export_dataset(
                    db,
                    package_root / "tracking",
                    split_by="session",
                    taxonomy_rank="fish",
                    fmt=FORMAT_MOT,
                    media_ids=export_media_ids,
                    dataset_name=f"{safe}_tracking",
                    dataset_version=args.version,
                    min_instances=1,
                    min_media=1,
                    strict_grouping=None,
                    mark_sessions_exported=False,
                )
            elif export_format == "csv":
                csv_path = package_root / "session.csv"
                csv_tables = export_session_tables(
                    db, capture, preview, selected_media, package_root,
                )
                csv_row_count = csv_tables["files"]["session.csv"]

            if export_format in ("coco", "tracking"):
                capture.status = STATUS_EXPORTED
            capture.updated_at = datetime.utcnow()
            summary = {
                "format": "AquaMeasure session package v1",
                "export_type": export_format,
                "session": {
                    key: preview.get(key)
                    for key in (
                        "session_id", "name", "site", "session_date", "operator",
                        "notes", "pair_count", "media_count", "observation_count",
                        "measurement_count", "event_count", "track_count", "max_n",
                        "species_summary",
                    )
                },
                "videos_available": available,
                "videos_skipped": missing if needs_source_video else [],
                "coco_directory": (
                    str(coco.output_dir.relative_to(package_root)) if coco else None
                ),
                "tracking_directory": (
                    str(tracking.output_dir.relative_to(package_root)) if tracking else None
                ),
                "tracking_event_count": tracking.report.get("events", {}).get("count", 0) if tracking else 0,
                "tracking_event_images_missing": tracking.report.get("events", {}).get("missing_images", []) if tracking else [],
                "csv_file": csv_path.name if csv_path else None,
                "csv_row_count": csv_row_count,
                "csv_tables": csv_tables,
                "created_at": stamp.isoformat(timespec="seconds"),
            }
            (package_root / "session.json").write_text(
                json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8",
            )
            readme = CSV_README if csv_tables else (
                "coco/ : détection, images JPEG et annotations COCO.\n"
                "tracking/ : suivi COCO-VID et MOTChallenge. Les actions sur piste "
                "sont dans coco_vid.json (events) et track_events.jsonl, avec types et frames exactes.\n"
                "event_images/ dans le dataset de suivi : images des actions absentes des positions exportées.\n"
            )
            (package_root / "LISEZ-MOI.txt").write_text(
                "Export de session AquaMeasure\n\n" + readme
                + "session.json : bilan de la session et liste des vidéos couvertes.\n",
                encoding="utf-8",
            )
            db.flush()
        except Exception:
            # Un dossier nommé comme un export terminé ne doit jamais rester
            # visible si l'une des deux étapes a échoué.
            shutil.rmtree(package_root, ignore_errors=True)
            raise

    print(json.dumps({
        "output_path": str(package_root.resolve()),
        "session": preview.get("name"),
        "export_type": export_format,
        "image_count": coco.report.get("image_count", 0) if coco else (
            tracking.report.get("image_count", 0) if tracking else 0
        ),
        "annotation_count": coco.report.get("annotation_count", 0) if coco else (
            tracking.report.get("annotation_count", 0) if tracking else csv_row_count
        ),
        "tracking_included": tracking is not None,
        "tracking_event_count": tracking.report.get("events", {}).get("count", 0) if tracking else 0,
        "csv_row_count": csv_row_count,
        "csv_tables": csv_tables,
        "missing_media_count": len(missing) if needs_source_video else 0,
    }, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
