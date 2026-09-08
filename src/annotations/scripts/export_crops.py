#!/usr/bin/env python3
"""Export fish crops from spatial annotations (images + video frames)."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from src.annodb.connection import get_db_path, init_db, session_scope
from src.annodb.export_media import (
    ExportExclusions,
    collect_export_frames,
    crop_from_geometry,
    materialize_frame_image,
)
from src.annodb.models import MediaAsset, TaxonNode
from src.annodb.rectify import load_left_rectifier
from src.annodb.spatial import bbox_from_geometry, parse_geometry


def _taxon_labels(session, taxon_id, cache: dict) -> dict:
    """Resolve scientific/common names and family/genus/species for a taxon."""
    blank = {
        "scientific_name": None, "common_name": None, "rank": None,
        "family": None, "genus": None, "species": None,
    }
    if not taxon_id:
        return dict(blank)
    if taxon_id in cache:
        return cache[taxon_id]
    out = dict(blank)
    node = session.get(TaxonNode, taxon_id)
    if node is not None:
        out["scientific_name"] = node.scientific_name
        out["common_name"] = node.common_name
        out["rank"] = node.rank
        cur = node
        seen: set[str] = set()
        while cur is not None and cur.id not in seen:
            seen.add(cur.id)
            if cur.rank in ("family", "genus", "species") and out.get(cur.rank) is None:
                out[cur.rank] = cur.scientific_name
            cur = session.get(TaxonNode, cur.parent_id) if cur.parent_id else None
    cache[taxon_id] = out
    return out


def _media_meta(media: MediaAsset | None) -> dict:
    if media is None:
        return {
            "media_rel_path": None, "site": None,
            "session_title": None, "session_date": None,
        }
    return {
        "media_rel_path": media.rel_path,
        "site": media.site,
        "session_title": media.session_title,
        "session_date": media.session_date.isoformat() if media.session_date else None,
    }


def _try_fishial_embedding(bgr) -> list[float] | None:
    """Optional Fishial embedding from a BGR crop."""
    try:
        import numpy as np
        from fishial_classify import embed_crop
    except ImportError:
        return None
    try:
        vec = embed_crop(bgr)
        if vec is None:
            return None
        return np.asarray(vec, dtype=float).tolist()
    except Exception:
        return None


def main() -> int:
    parser = argparse.ArgumentParser(description="Export annotation crops from DB")
    parser.add_argument("--out", type=Path, required=True, help="Output directory")
    parser.add_argument("--project", action="append", dest="projects")
    parser.add_argument("--db", type=Path, default=None)
    parser.add_argument("--pad", type=float, default=0.08, help="BBox padding ratio")
    parser.add_argument(
        "--embeddings",
        action="store_true",
        help="Compute Fishial embeddings via fishial_classify.embed_crop",
    )
    parser.add_argument("--limit", type=int, default=0, help="Max crops (0 = all)")
    parser.add_argument(
        "--media-id",
        dest="media_id",
        default=None,
        help="Limit export to a single media/session id",
    )
    args = parser.parse_args()

    db_path = args.db or get_db_path()
    if not db_path.exists():
        init_db(db_path)

    out = Path(args.out)
    crops_dir = out / "crops"
    crops_dir.mkdir(parents=True, exist_ok=True)
    meta_path = out / "crops_meta.jsonl"

    project_ids = None
    if args.projects:
        from sqlalchemy import select
        from src.annodb.models import Project

        project_ids = []
        with session_scope(db_path) as session:
            for p in args.projects:
                row = session.scalar(
                    select(Project).where((Project.id == p) | (Project.name == p))
                )
                if row:
                    project_ids.append(row.id)

    import cv2

    count = 0
    taxon_cache: dict = {}
    exclusions = ExportExclusions()
    # Les boites ont ete tracees sur l'image rectifiee gauche : recadrer la
    # frame brute deplacerait le poisson dans le crop.
    rectifier = load_left_rectifier()
    with session_scope(db_path) as session, meta_path.open("w", encoding="utf-8") as meta_f:
        frames = collect_export_frames(session, project_ids, exclusions=exclusions)
        if args.media_id:
            frames = [ef for ef in frames if ef.media_id == args.media_id]
        for ef in frames:
            tmp = out / "_tmp_frame.jpg"
            dims = materialize_frame_image(
                session, ef, tmp, transform=rectifier, exclusions=exclusions,
            )
            if dims is None:
                continue
            w, h = dims
            bgr = cv2.imread(str(tmp))
            if bgr is None:
                continue
            media_meta = _media_meta(session.get(MediaAsset, ef.media_id))
            for ann in ef.annotations:
                if args.limit and count >= args.limit:
                    break
                geom = parse_geometry(ann)
                crop = crop_from_geometry(
                    bgr, geom, ann.geom_type, w, h, pad=args.pad,
                )
                if crop is None or crop.size == 0:
                    continue
                stem = f"{ann.id}"
                crop_file = crops_dir / f"{stem}.jpg"
                cv2.imwrite(str(crop_file), crop)
                taxon = _taxon_labels(session, ann.taxon_node_id, taxon_cache)
                try:
                    cx, cy, bw, bh = bbox_from_geometry(geom, w, h)
                    bbox_px = {
                        "x1": int((cx - bw / 2) * w),
                        "y1": int((cy - bh / 2) * h),
                        "x2": int((cx + bw / 2) * w),
                        "y2": int((cy + bh / 2) * h),
                    }
                except Exception:
                    bbox_px = None
                row = {
                    "annotation_id": ann.id,
                    "media_id": ef.media_id,
                    "frame_index": ef.frame_index,
                    "taxon_node_id": ann.taxon_node_id,
                    "track_id": ann.track_id,
                    "crop_path": str(crop_file.relative_to(out)),
                    "scientific_name": taxon["scientific_name"],
                    "common_name": taxon["common_name"],
                    "rank": taxon["rank"],
                    "family": taxon["family"],
                    "genus": taxon["genus"],
                    "species": taxon["species"],
                    "measurement_mm": ann.measurement_mm,
                    "position_x_mm": ann.position_x_mm,
                    "position_y_mm": ann.position_y_mm,
                    "position_z_mm": ann.position_z_mm,
                    "confidence": ann.confidence,
                    "source": ann.source,
                    # `is_grazing` n'est plus publié (phase 7) : la colonne
                    # `spatial_annotations.is_grazing` est dépréciée et
                    # toujours nulle — l'écrire dans le manifeste laissait
                    # croire que le comportement était renseigné boîte par
                    # boîte. Le broutage est un intervalle (`temporal_events`),
                    # exporté par le format AVA / le JSONL d'événements.
                    "bbox_px": bbox_px,
                    "frame_width": w,
                    "frame_height": h,
                    "image_space": ef.image_space,
                    "frame_ref": ef.frame_ref,
                    "calibration": ef.calibration,
                    **media_meta,
                }
                if args.embeddings:
                    emb = _try_fishial_embedding(crop)
                    if emb is not None:
                        row["embedding"] = emb
                meta_f.write(json.dumps(row) + "\n")
                count += 1
            if args.limit and count >= args.limit:
                break
            if tmp.is_file():
                tmp.unlink()

    if exclusions:
        (out / "export_report.json").write_text(
            json.dumps(exclusions.as_dict(), indent=2, ensure_ascii=False),
            encoding="utf-8",
        )
    print(json.dumps({
        "crop_count": count,
        "output": str(out.resolve()),
        "excluded": exclusions.summary(),
    }, indent=2, ensure_ascii=False))
    return 0 if count else 1


if __name__ == "__main__":
    raise SystemExit(main())
