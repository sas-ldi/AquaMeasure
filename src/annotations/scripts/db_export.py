#!/usr/bin/env python3
"""Entrée CLI du noyau d'export : base unifiée → dataset versionné.

Six formats, trois usages, un seul noyau :

| `--format`     | Usage        | Ce qui est écrit                          |
| -------------- | ------------ | ----------------------------------------- |
| `coco`         | détection    | `instances_fish.json` + `instances_<rang>.json` |
| `yolo`         | détection    | le COCO, puis `yolo/` qui en dérive       |
| `coco_vid`     | suivi        | `coco_vid.json` (pivot TAO)               |
| `mot`          | suivi        | le COCO-VID, puis `mot/` qui en dérive    |
| `ava`          | comportement | `events.csv` + `actions.csv` + `events.jsonl` |
| `events_jsonl` | comportement | `events.jsonl` seul                       |

Le split n'a **pas** de valeur par défaut : `--split-by` est obligatoire.
Choisir entre média, session et site est une décision scientifique — un split
par frame gonfle les métriques de 20 à 40 points et s'effondre sur un nouveau
site.
"""

from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
REPO_ROOT = ROOT.parent
sys.path.insert(0, str(ROOT))

from src.annodb.connection import get_db_path, init_db, session_scope
from src.annodb.export_behavior import DEFAULT_AVA_HZ, DEFAULT_AVA_MAX_GAP_S
from src.annodb.export_core import (
    BEHAVIOR_FORMATS,
    DEFAULT_DATASET_NAME,
    DEFAULT_DATASET_VERSION,
    DEFAULT_MIN_INSTANCES,
    DEFAULT_MIN_MEDIA,
    DEFAULT_SEED,
    DEFAULT_STRICT_GROUPING,
    FORMAT_COCO,
    SPLIT_BY_VALUES,
    SPLIT_NAMES,
    SUPPORTED_FORMATS,
    TAXONOMY_RANKS,
    TRACKING_FORMATS,
    export_dataset,
)
from src.annodb.projects import resolve_project_ids
from src.annodb.storage_config import exports_dir


def _parse_ratios(text: str) -> dict:
    parts = [float(p) for p in str(text).replace(";", ",").split(",") if p.strip()]
    if len(parts) != 3:
        raise argparse.ArgumentTypeError(
            "--ratios attend trois valeurs train,val,test (ex. 70,15,15)"
        )
    return dict(zip(SPLIT_NAMES, parts))


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Export d'un dataset versionné depuis la base d'annotations",
    )
    parser.add_argument(
        "--format", choices=list(SUPPORTED_FORMATS), default=FORMAT_COCO,
        help="Détection : coco | yolo — suivi : coco_vid | mot — "
             "comportement : ava | events_jsonl",
    )
    parser.add_argument("--rank", choices=list(TAXONOMY_RANKS), default="fish")
    parser.add_argument(
        "--split-by", choices=list(SPLIT_BY_VALUES), required=True,
        help="Grain du split : media | session | site (obligatoire)",
    )
    parser.add_argument(
        # `--out` explicite reste prioritaire ; à défaut on suit la racine
        # des données choisie dans la page Paramètres (storage_config), et
        # sans configuration on retombe sur `<dépôt>/data/exports`.
        "--out", type=Path, default=None,
        help="Racine des exports (défaut : racine des données configurée) ; "
             "le dossier versionné y est créé",
    )
    parser.add_argument("--project", action="append", dest="projects",
                        help="Filtre par id ou nom de projet (répétable)")
    parser.add_argument(
        "--session", dest="capture_session_id",
        help="Limiter l'export à une session de terrain et à toutes ses vidéos",
    )
    parser.add_argument("--db", type=Path, default=None)
    parser.add_argument("--seed", type=int, default=DEFAULT_SEED)
    parser.add_argument("--ratios", type=_parse_ratios, default=None,
                        help="Ratios train,val,test (défaut 70,15,15)")
    parser.add_argument("--frame-stride", type=int, default=1,
                        help="Ne garder qu'une frame sur N par média (défaut 1)")
    parser.add_argument("--min-instances", type=int, default=DEFAULT_MIN_INSTANCES,
                        help="Instances minimum pour qu'une classe existe")
    parser.add_argument("--min-media", type=int, default=DEFAULT_MIN_MEDIA,
                        help="Médias distincts minimum pour qu'une classe existe")
    parser.add_argument("--dataset-name", default=DEFAULT_DATASET_NAME)
    parser.add_argument("--version", default=DEFAULT_DATASET_VERSION,
                        help="Version semver du dataset (défaut 1.0.0)")
    parser.add_argument("--replay-splits", type=Path, default=None,
                        help="splits.json à rejouer pour figer le découpage")
    parser.add_argument(
        "--strict-grouping", type=float, default=DEFAULT_STRICT_GROUPING,
        help="Part maximale de médias sans la métadonnée du grain demandé "
             f"avant refus (défaut {DEFAULT_STRICT_GROUPING}). "
             "Utilisez --no-strict-grouping pour ne jamais refuser.",
    )
    parser.add_argument(
        "--no-strict-grouping", dest="strict_grouping", action="store_const",
        const=None, help="Accepter n'importe quelle proportion de replis de grain "
                         "(le manifeste les compte quand même)",
    )
    parser.add_argument("--created-at", default=None,
                        help="Date figée AAAA-MM-JJ[THH:MM:SS] inscrite dans les "
                             "fichiers hachés (reproductibilité)")
    parser.add_argument("--no-rectify", action="store_true",
                        help="Exporter les frames brutes (déconseillé)")
    parser.add_argument(
        "--ava-hz", type=float, default=DEFAULT_AVA_HZ,
        help="Densification du CSV AVA, en lignes par seconde "
             f"(défaut {DEFAULT_AVA_HZ}) — inscrite au manifeste",
    )
    parser.add_argument(
        "--ava-max-gap-s", type=float, default=DEFAULT_AVA_MAX_GAP_S,
        help="Écart maximal entre deux échantillons de piste pour interpoler "
             f"une position (défaut {DEFAULT_AVA_MAX_GAP_S} s) ; au-delà la "
             "ligne est sautée et comptée plutôt qu'inventée",
    )
    args = parser.parse_args()

    db_path = args.db or get_db_path()
    if not db_path.exists():
        init_db(db_path)

    project_ids = None
    media_ids = None
    if args.projects:
        # Noms ou identifiants : comparer un nom à un UUID viderait l'export.
        with session_scope(db_path) as session:
            project_ids = resolve_project_ids(session, args.projects)
    if args.capture_session_id:
        from src.annodb.models import CaptureSession
        from src.annodb.sessions import session_media_ids

        with session_scope(db_path) as session:
            capture = session.get(CaptureSession, args.capture_session_id)
            if capture is None:
                parser.error(f"session introuvable : {args.capture_session_id}")
            media_ids = session_media_ids(session, capture)
            if not media_ids:
                parser.error("cette session ne contient encore aucune vidéo")

    created_at = None
    if args.created_at:
        created_at = datetime.fromisoformat(str(args.created_at))

    # `--out` explicite prioritaire ; sinon la racine des données configurée.
    output_root = args.out or exports_dir()

    with session_scope(db_path) as session:
        result = export_dataset(
            session,
            output_root,
            split_by=args.split_by,
            taxonomy_rank=args.rank,
            fmt=args.format,
            project_ids=project_ids,
            media_ids=media_ids,
            dataset_name=args.dataset_name,
            dataset_version=args.version,
            seed=args.seed,
            ratios=args.ratios,
            frame_stride=args.frame_stride,
            min_instances=args.min_instances,
            min_media=args.min_media,
            rectify_images=not args.no_rectify,
            created_at=created_at,
            replay_splits=args.replay_splits,
            strict_grouping=args.strict_grouping,
            ava_hz=args.ava_hz,
            ava_max_gap_s=args.ava_max_gap_s,
        )
        print(json.dumps(_summary(result, args.format), indent=2, ensure_ascii=False))

    return 0


def _summary(result, fmt: str) -> dict:
    """Résumé lisible, adapté à ce que le format a réellement produit.

    Un export de comportement n'a ni image ni classe taxonomique : afficher
    `image_count: 0` et `classes: []` laisserait croire à un export raté.
    """
    manifest = result.manifest
    report = result.report
    summary = {
        "export_id": result.run.id,
        "format": result.run.format,
        "taxonomy_rank": result.run.taxonomy_rank,
        "dataset": f"{manifest['dataset_name']} v{manifest['dataset_version']}",
        "output_path": str(result.output_dir),
        "content_sha256": manifest["content_sha256"],
        "annotation_count": report["annotation_count"],
        "split_strategy": manifest["split"]["strategy"],
        # Le grain demandé n'est pas toujours tenable : le dire ici évite
        # de croire un `group_by_site` qui a massivement replié sur media.
        "degraded_group_count": manifest["split"]["degraded_groups"]["count"],
        "db_snapshot_wal_checkpointed": (
            manifest["source"]["db_snapshot_wal_checkpointed"]
        ),
        # Aucune annotation ne disparaît sans raison : le décompte et le
        # détail sont dans export_report.json, le résumé ici.
        "excluded_count": manifest["exclusions"]["count"],
        "excluded_by_reason": manifest["exclusions"]["by_reason"],
        "image_space": (manifest.get("coordinate_frame") or {}).get("image_space"),
        "sessions_marked_exported": [
            row["name"] for row in manifest.get("sessions_marked_exported", [])
        ],
    }

    if fmt in BEHAVIOR_FORMATS:
        summary.update({
            "event_count": manifest["source"]["event_count"],
            "events_by_source": manifest["source"]["events_by_source"],
            "media_count": manifest["source"]["media_count"],
            "split": {
                name: manifest["split"]["composition"][name]["events"]
                for name in SPLIT_NAMES
            },
            "metrics": manifest["metrics"]["by_type"],
        })
        if manifest.get("ava"):
            summary["ava"] = {
                key: manifest["ava"].get(key)
                for key in ("hz", "max_gap_s", "row_count", "rows_exact",
                            "rows_interpolated", "rows_skipped",
                            "rows_skipped_by_reason", "events_kept",
                            "events_excluded")
            }
        return summary

    summary.update({
        "image_count": report["image_count"],
        "ignored_annotation_count": report["ignored_annotation_count"],
        "classes": manifest["class_stats"]["names"],
        "split": {
            name: manifest["split"]["composition"][name]["images"]
            for name in SPLIT_NAMES
        },
        "bbox_clamped_count": manifest["source"]["bbox_clamped_count"],
    })
    if fmt in TRACKING_FORMATS:
        summary.update({
            "video_count": report["video_count"],
            "track_count": report["track_count"],
            "coverage": {
                key: manifest["coverage"][key]
                for key in ("exported_frames", "covered_span", "coverage_ratio")
            },
            "mot_sequences": (manifest.get("mot") or {}).get("sequence_count", 0),
        })
        return summary

    summary.update({
        "replayed_from": manifest["split"]["replayed_from"],
        "na_groups_moved_from_replay": (
            manifest["split"]["na_rule"]["groups_moved_from_replay"]
        ),
    })
    return summary

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
