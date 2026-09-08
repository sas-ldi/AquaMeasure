#!/usr/bin/env python3
"""Import CVAT export into unified annotation DB."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from src.annodb.connection import get_db_path, init_db, session_scope
from src.annodb.ingest_cvat import ingest_cvat_export, load_label_mapping


def main() -> int:
    parser = argparse.ArgumentParser(description="Ingest CVAT export into annotation DB")
    parser.add_argument("--zip", "--export", dest="export_path", type=Path, required=True)
    parser.add_argument("--project", required=True, help="Project name")
    parser.add_argument("--mapping", type=Path, default=None, help="YAML label mapping file")
    parser.add_argument("--db", type=Path, default=None)
    args = parser.parse_args()

    db_path = args.db or get_db_path()
    if not db_path.exists():
        init_db(db_path)

    mapping = load_label_mapping(args.mapping)

    with session_scope(db_path) as session:
        media_n, ann_n = ingest_cvat_export(
            session,
            args.export_path,
            project_name=args.project,
            mapping=mapping,
            mapping_file=args.mapping,
        )

    print(f"Imported {media_n} media, {ann_n} spatial annotations")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
