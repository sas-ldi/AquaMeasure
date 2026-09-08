#!/usr/bin/env python3
"""Create sample annotations for testing export pipelines."""

from __future__ import annotations

import sys
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from src.annodb.connection import get_db_path, init_db, session_scope
from src.annodb.projects import get_or_create_project, register_media
from src.annodb.spatial import add_spatial_annotation
from src.annodb.taxonomy import get_node_by_name


def main() -> int:
    db_path = get_db_path()
    init_db(db_path)

    sample_dir = ROOT / "data" / "samples"
    sample_dir.mkdir(parents=True, exist_ok=True)

    img_path = sample_dir / "sample_fish_01.png"
    if not img_path.exists():
        img = np.zeros((480, 640, 3), dtype=np.uint8)
        img[200:280, 250:400] = (80, 160, 200)
        import cv2
        cv2.imwrite(str(img_path), img)

    with session_scope(db_path) as session:
        proj = get_or_create_project(session, "sample_project", "Annotations de test")
        media = register_media(session, project_id=proj.id, file_path=img_path, copy_into_store=True)

        fish_id = get_node_by_name(session, "fish").id
        genus_id = get_node_by_name(session, "Acanthurus").id

        add_spatial_annotation(
            session,
            media_id=media.id,
            geom_type="bbox",
            geometry={"cx": 0.5, "cy": 0.5, "w": 0.25, "h": 0.18, "normalized": True},
            taxon_node_id=fish_id,
            source="manual",
            author="seed",
        )
        add_spatial_annotation(
            session,
            media_id=media.id,
            geom_type="bbox",
            geometry={"cx": 0.3, "cy": 0.45, "w": 0.15, "h": 0.12, "normalized": True},
            taxon_node_id=genus_id,
            source="manual",
            author="seed",
            is_provisional=True,
        )

    print(f"Sample data created in {db_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
