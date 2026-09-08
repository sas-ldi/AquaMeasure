#!/usr/bin/env python3
"""Import a local YOLO folder into fish_annotations.db."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from src.annodb.connection import get_db_path, init_db, session_scope
from src.annodb.ingest_cvat import ingest_yolo_folder, load_label_mapping


def main() -> int:
    parser = argparse.ArgumentParser(description="Ingest local YOLO folder into annotation DB")
    parser.add_argument("--folder", type=Path, required=True, help="YOLO dataset root (data.yaml)")
    parser.add_argument("--project", required=True, help="Project name")
    parser.add_argument(
        "--mapping",
        type=Path,
        default=ROOT / "configs" / "public_family_label_map.yaml",
        help="YAML label -> taxon_node_id mapping",
    )
    parser.add_argument("--db", type=Path, default=None)
    args = parser.parse_args()

    folder = args.folder
    if not folder.is_absolute():
        folder = ROOT / folder
    if not folder.exists():
        print(f"Folder not found: {folder}")
        return 1

    db_path = args.db or get_db_path()
    if not db_path.exists():
        init_db(db_path)

    mapping = load_label_mapping(args.mapping)

    with session_scope(db_path) as session:
        media_n, ann_n = ingest_yolo_folder(
            session,
            folder,
            project_name=args.project,
            mapping=mapping,
        )

    print(f"Imported {media_n} media, {ann_n} spatial annotations")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
