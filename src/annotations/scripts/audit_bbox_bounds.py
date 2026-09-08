#!/usr/bin/env python3
"""Enquête : d'où viennent les boîtes qui débordent de l'image ?

Le noyau d'export borne les boîtes hors cadre (`export_core.clamp_box_to_image`)
et compte l'opération dans le manifeste (`source.bbox_clamped_count`). Borner
répare la **sortie** ; ça ne dit pas pourquoi la base contient des boîtes qui
sortent de l'image. Ce script cherche la cause **en amont**, sans rien écrire :
il relit `spatial_annotations` et `track_samples`, reconstitue la boîte en
pixels dans son référentiel déclaré, et classe les débordements par source,
auteur, média et nature du dépassement.

Trois hypothèses sont testées explicitement :

1. **saisie manuelle sans bornage** — une bbox tracée à la souris dont le
   glissement sort du cadre vidéo (`source='manual'`, dépassement modéré) ;
2. **tracker aux bords** — le détecteur rend une boîte qui mord le bord de
   l'image rectifiée (`source='model'`, dépassement de quelques pixels) ;
3. **import CVAT mal dimensionné** — géométrie normalisée multipliée par une
   taille de référence qui n'est pas celle de l'image annotée
   (`source='cvat'`, dépassement proportionnel et systématique).

Le script est **en lecture seule** ; il refuse de tourner sur la base par
défaut sans `--db` explicite, pour qu'on ne l'exécute jamais sur la vraie base
par inadvertance.

Exemple :

    python scripts/audit_bbox_bounds.py --db /chemin/copie.db --json rapport.json
"""

from __future__ import annotations

import argparse
import json
import sys
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from sqlalchemy import select  # noqa: E402

from src.annodb.connection import session_scope  # noqa: E402
from src.annodb.models import (  # noqa: E402
    MediaAsset,
    SpatialAnnotation,
    Track,
    TrackSample,
)
from src.annodb.spatial import (  # noqa: E402
    bbox_from_geometry,
    geometry_ref_size,
    geometry_space,
    geometry_units,
    parse_geometry,
)

# Tolérance par défaut **nulle** : c'est justement la magnitude du dépassement
# qui désigne la cause, et l'écarter d'avance aurait masqué le résultat de
# l'enquête (voir `_bucket`). Le classement par tranche fait le tri.
DEFAULT_TOLERANCE_PX = 0.0

# Seuil sous lequel un dépassement ne peut venir que de l'arrondi d'un fichier
# source — aligné sur `export_core.CLAMP_EPSILON_PX`. Un centième de pixel :
# le plus grand écart constaté sur la vraie base est de 0,0037 px (5 × 10⁻⁷ en
# normalisé sur une image de 7360 px de large).
ROUNDING_PX = 1e-2


def _bucket(overflow_px: float) -> str:
    """Classe le dépassement — sa magnitude désigne la cause probable."""
    if overflow_px < ROUNDING_PX:
        return "<0,01px (arrondi du fichier de labels source)"
    if overflow_px <= 2:
        return "0,01-2px (arrondi grossier ou bord d'image)"
    if overflow_px <= 20:
        return "3-20px (bord d'image / boîte mordant le cadre)"
    if overflow_px <= 200:
        return "21-200px (saisie ou suivi hors cadre)"
    return ">200px (référentiel de taille probablement faux)"


def _overflow(
    x1: float, y1: float, x2: float, y2: float, width: float, height: float,
) -> Dict[str, float]:
    """Dépassement par côté, en pixels (0 = la boîte tient dans l'image)."""
    return {
        "left": max(0.0, -x1),
        "top": max(0.0, -y1),
        "right": max(0.0, x2 - width),
        "bottom": max(0.0, y2 - height),
    }


def _media_index(session) -> Dict[str, MediaAsset]:
    return {m.id: m for m in session.scalars(select(MediaAsset))}


def audit_spatial(
    session, *, tolerance: float = DEFAULT_TOLERANCE_PX,
) -> Dict[str, Any]:
    """Débordements dans `spatial_annotations`, classés par cause probable."""
    media_by_id = _media_index(session)
    rows: List[Dict[str, Any]] = []
    total = 0
    unreadable = 0
    no_reference = 0
    ref_vs_media: Counter = Counter()

    query = select(SpatialAnnotation).where(
        SpatialAnnotation.geom_type.in_(["bbox", "point"])
    )
    for ann in session.scalars(query):
        total += 1
        media = media_by_id.get(ann.media_id)
        try:
            geom = parse_geometry(ann)
        except (ValueError, TypeError):
            unreadable += 1
            continue

        ref_w, ref_h = geometry_ref_size(geom)
        media_w = int(media.width) if media is not None and media.width else None
        media_h = int(media.height) if media is not None and media.height else None
        width = float(ref_w or media_w or 0)
        height = float(ref_h or media_h or 0)
        if width <= 0 or height <= 0:
            # Sans taille de référence, « hors cadre » n'a pas de sens : on ne
            # devine pas une image de 1920×1080 pour pouvoir accuser la boîte.
            no_reference += 1
            continue

        if ref_w and media_w:
            ref_vs_media[
                "identique" if int(ref_w) == media_w else f"ref={ref_w} media={media_w}"
            ] += 1

        try:
            cx, cy, bw, bh = bbox_from_geometry(geom, int(width), int(height))
        except (ValueError, TypeError, KeyError):
            unreadable += 1
            continue

        x1 = (cx - bw / 2) * width
        y1 = (cy - bh / 2) * height
        x2 = (cx + bw / 2) * width
        y2 = (cy + bh / 2) * height
        over = _overflow(x1, y1, x2, y2, width, height)
        worst = max(over.values())
        if worst <= tolerance:
            continue

        rows.append({
            "annotation_id": ann.id,
            "media_id": ann.media_id,
            "media_name": Path(media.rel_path).name if media is not None else None,
            "frame_index": ann.frame_index,
            "frame_ref": ann.frame_ref,
            "source": ann.source,
            "author": ann.author,
            "geom_type": ann.geom_type,
            "units": geometry_units(geom),
            "space": geometry_space(geom),
            "ref_width": ref_w,
            "ref_height": ref_h,
            "media_width": media_w,
            "media_height": media_h,
            "bbox_px": [round(v, 2) for v in (x1, y1, x2, y2)],
            # Arrondi pour la lecture, valeur exacte conservée à côté : c'est
            # elle qui décide de la tranche, et un `round(…, 2)` la ramenait à
            # zéro pour tous les cas d'arrondi — le verdict comptait alors
            # 146 « sans conséquence » là où 15 dépassaient le seuil.
            "overflow_px": {k: round(v, 6) for k, v in over.items() if v > tolerance},
            "worst_overflow_px": round(worst, 6),
            "worst_overflow_px_exact": worst,
            "bucket": _bucket(worst),
            "sides": sorted(k for k, v in over.items() if v > tolerance),
            "created_at": ann.created_at.isoformat() if ann.created_at else None,
        })

    return {
        "table": "spatial_annotations",
        "annotations_examined": total,
        "geometry_unreadable": unreadable,
        "without_reference_size": no_reference,
        "out_of_bounds": len(rows),
        "ratio": round(len(rows) / total, 4) if total else 0.0,
        "by_source": dict(sorted(Counter(r["source"] for r in rows).items())),
        "by_author": dict(sorted(Counter(str(r["author"]) for r in rows).items())),
        # Le détail par média se compte en centaines d'entrées : on garde le
        # décompte et les plus atteints, le reste est dans les lignes.
        "media_touched": len({r["media_name"] for r in rows}),
        "by_media_top": dict(
            Counter(str(r["media_name"]) for r in rows).most_common(10)
        ),
        "by_bucket": dict(sorted(Counter(r["bucket"] for r in rows).items())),
        "by_side": dict(sorted(Counter(
            side for r in rows for side in r["sides"]
        ).items())),
        "by_units": dict(sorted(Counter(str(r["units"]) for r in rows).items())),
        "by_space": dict(sorted(Counter(str(r["space"]) for r in rows).items())),
        "reference_vs_media_size": dict(sorted(ref_vs_media.items())),
        "rows": rows,
    }


def audit_track_samples(
    session, *, tolerance: float = DEFAULT_TOLERANCE_PX,
) -> Dict[str, Any]:
    """Débordements dans `track_samples` — les boîtes du tracker, en pixels.

    `track_samples.bbox_json` ne déclare ni unités ni taille de référence : la
    seule taille disponible est celle du média. Une boîte du tracker est écrite
    dans l'espace **rectifié**, dont les dimensions sont celles de l'image
    d'entrée ; l'écart de cadrage entre brut et rectifié est donc, lui aussi,
    une cause possible.
    """
    media_by_id = _media_index(session)
    media_of_track = {
        t.id: t.media_id for t in session.scalars(select(Track))
    }
    rows: List[Dict[str, Any]] = []
    total = 0
    no_reference = 0
    by_origin_total: Counter = Counter()

    for sample in session.scalars(select(TrackSample)):
        total += 1
        by_origin_total[sample.origin] += 1
        media = media_by_id.get(media_of_track.get(sample.track_id, ""))
        width = float(media.width) if media is not None and media.width else 0.0
        height = float(media.height) if media is not None and media.height else 0.0
        if width <= 0 or height <= 0:
            no_reference += 1
            continue
        try:
            box = json.loads(sample.bbox_json)
            x1 = float(box["x_min"])
            y1 = float(box["y_min"])
            x2 = float(box["x_max"])
            y2 = float(box["y_max"])
        except (ValueError, TypeError, KeyError):
            continue
        over = _overflow(x1, y1, x2, y2, width, height)
        worst = max(over.values())
        if worst <= tolerance:
            continue
        rows.append({
            "sample_id": sample.id,
            "track_id": sample.track_id,
            "media_name": Path(media.rel_path).name,
            "frame_index": sample.frame_index,
            "origin": sample.origin,
            "edited_by": sample.edited_by,
            "bbox_px": [round(v, 2) for v in (x1, y1, x2, y2)],
            "media_size": [int(width), int(height)],
            "overflow_px": {k: round(v, 2) for k, v in over.items() if v > tolerance},
            "worst_overflow_px": round(worst, 2),
            "bucket": _bucket(worst),
            "sides": sorted(k for k, v in over.items() if v > tolerance),
        })

    return {
        "table": "track_samples",
        "samples_examined": total,
        "without_reference_size": no_reference,
        "out_of_bounds": len(rows),
        "ratio": round(len(rows) / total, 4) if total else 0.0,
        "samples_by_origin": dict(sorted(by_origin_total.items())),
        "by_origin": dict(sorted(Counter(r["origin"] for r in rows).items())),
        "by_media": dict(sorted(Counter(r["media_name"] for r in rows).items())),
        "by_bucket": dict(sorted(Counter(r["bucket"] for r in rows).items())),
        "by_side": dict(sorted(Counter(
            side for r in rows for side in r["sides"]
        ).items())),
        # Un échantillon par piste suffit à identifier le motif : la liste
        # complète peut compter des milliers de lignes quasi identiques.
        "rows_sample": rows[:200],
    }


def _verdict(spatial: Dict[str, Any], samples: Dict[str, Any]) -> List[str]:
    """Conclusions lisibles — ce que les chiffres désignent comme cause."""
    out: List[str] = []
    by_source = spatial["by_source"]
    if not spatial["out_of_bounds"]:
        out.append("Aucune annotation hors cadre : rien à corriger en amont.")
    for source, count in sorted(by_source.items(), key=lambda kv: -kv[1]):
        buckets = Counter(
            row["bucket"] for row in spatial["rows"] if row["source"] == source
        )
        out.append(
            f"spatial_annotations · source '{source}' : {count} boîte(s) hors "
            f"cadre — {', '.join(f'{b} ×{n}' for b, n in sorted(buckets.items()))}"
        )
    if samples["out_of_bounds"]:
        out.append(
            f"track_samples : {samples['out_of_bounds']} échantillon(s) hors "
            f"cadre sur {samples['samples_examined']} "
            f"({samples['ratio']:.1%}) — origines {samples['by_origin']}"
        )
    else:
        out.append("track_samples : aucun échantillon hors cadre.")
    mismatch = {
        k: v for k, v in spatial["reference_vs_media_size"].items()
        if k != "identique"
    }
    if mismatch:
        out.append(
            "Tailles de référence en désaccord avec le média enregistré : "
            f"{mismatch} — piste sérieuse pour un import mal dimensionné."
        )
    else:
        out.append(
            "Aucune taille de référence en désaccord avec le média : "
            "l'hypothèse « import normalisé × taille brute au lieu de "
            "rectifiée » est écartée."
        )

    rounding = sum(
        1 for row in spatial["rows"]
        if row["worst_overflow_px_exact"] < ROUNDING_PX
    )
    real = spatial["out_of_bounds"] - rounding
    out.append(
        f"Verdict : {rounding} boîte(s) hors cadre d'un arrondi de fichier "
        f"source (< {ROUNDING_PX} px, sans conséquence) et {real} "
        "débordement(s) réel(s) à corriger en amont."
    )
    return out


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Classe les boîtes hors cadre par source, auteur et média",
    )
    parser.add_argument(
        "--db", type=Path, required=True,
        help="Base à auditer — utilisez une COPIE, jamais la base de travail",
    )
    parser.add_argument(
        "--tolerance", type=float, default=DEFAULT_TOLERANCE_PX,
        help=f"Dépassement ignoré, en pixels (défaut {DEFAULT_TOLERANCE_PX})",
    )
    parser.add_argument(
        "--json", type=Path, default=None, help="Écrit le rapport complet en JSON",
    )
    parser.add_argument(
        "--top", type=int, default=10, help="Nombre d'exemples affichés (défaut 10)",
    )
    args = parser.parse_args()

    db_path = args.db
    if not db_path.is_file():
        print(f"[!] Base introuvable : {db_path}")
        return 2

    with session_scope(db_path) as session:
        spatial = audit_spatial(session, tolerance=args.tolerance)
        samples = audit_track_samples(session, tolerance=args.tolerance)

    report = {
        "database": str(db_path),
        "tolerance_px": args.tolerance,
        "spatial_annotations": spatial,
        "track_samples": samples,
        "verdict": _verdict(spatial, samples),
    }

    print(f"Base auditée : {db_path}")
    print(f"Tolérance : {args.tolerance} px\n")
    print("-- spatial_annotations --")
    print(f"  examinées            : {spatial['annotations_examined']}")
    print(f"  géométrie illisible  : {spatial['geometry_unreadable']}")
    print(f"  sans taille de réf.  : {spatial['without_reference_size']}")
    print(f"  HORS CADRE           : {spatial['out_of_bounds']} "
          f"({spatial['ratio']:.2%})")
    for label, key in (
        ("par source", "by_source"), ("par auteur", "by_author"),
        ("par ampleur", "by_bucket"), ("par côté", "by_side"),
        ("par unités", "by_units"), ("par espace", "by_space"),
    ):
        print(f"  {label:20s} : {spatial[key]}")
    print(f"  {'médias touchés':20s} : {spatial['media_touched']}")
    print(f"  {'médias les + touchés':20s} : {spatial['by_media_top']}")
    print(f"  ref vs média         : {spatial['reference_vs_media_size']}")

    if spatial["rows"]:
        print(f"\n  {args.top} premiers cas :")
        for row in spatial["rows"][:args.top]:
            print(
                f"    {row['annotation_id'][:8]} {row['source']:8s} "
                f"{str(row['media_name'])[:28]:28s} f{row['frame_index']:<8} "
                f"bbox={row['bbox_px']} ref={row['ref_width']}x{row['ref_height']} "
                f"dépassement={row['overflow_px']}"
            )

    print("\n── track_samples ──")
    print(f"  examinés             : {samples['samples_examined']}")
    print(f"  sans taille de réf.  : {samples['without_reference_size']}")
    print(f"  HORS CADRE           : {samples['out_of_bounds']} "
          f"({samples['ratio']:.2%})")
    print(f"  par origine          : {samples['by_origin']}")
    print(f"  par média            : {samples['by_media']}")
    print(f"  par ampleur          : {samples['by_bucket']}")
    print(f"  par côté             : {samples['by_side']}")
    if samples["rows_sample"]:
        print(f"\n  {args.top} premiers cas :")
        for row in samples["rows_sample"][:args.top]:
            print(
                f"    #{row['sample_id']:<8} {row['origin']:12s} "
                f"{row['media_name'][:28]:28s} f{row['frame_index']:<8} "
                f"bbox={row['bbox_px']} image={row['media_size']} "
                f"dépassement={row['overflow_px']}"
            )

    print("\n── conclusions ──")
    for line in report["verdict"]:
        print(f"  - {line}")

    if args.json:
        args.json.parent.mkdir(parents=True, exist_ok=True)
        args.json.write_text(
            json.dumps(report, indent=2, ensure_ascii=False), encoding="utf-8",
        )
        print(f"\nRapport complet : {args.json}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
