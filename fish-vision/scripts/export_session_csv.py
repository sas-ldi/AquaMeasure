#!/usr/bin/env python3
"""CLI — export session timeline CSV from fish_annotations.db."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from src.annodb.connection import get_db_path, init_db
from src.annodb.export_session_csv import (
    export_session_timeline_auto_path,
    export_session_timeline_csv,
    resolve_media_id_by_video_name,
)


def main() -> int:
    parser = argparse.ArgumentParser(description="Export session timeline CSV")
    parser.add_argument("--media-id", type=str, default=None)
    parser.add_argument("--video", type=str, default=None, help="Video file name or path")
    parser.add_argument("--out", type=Path, default=None, help="Output CSV path or directory")
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

    out = args.out or (ROOT / "data" / "exports")
    try:
        if out.suffix.lower() == ".csv":
            result = export_session_timeline_csv(media_id, out, db_path=db_path)
        else:
            result = export_session_timeline_auto_path(media_id, out, db_path=db_path)
    except ValueError as exc:
        print(f"Erreur : {exc}", file=sys.stderr)
        return 1

    print(f"Export OK : {result['path']} ({result['row_count']} lignes)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
