"""Helpers for exporting images and video frames from media_assets."""

from __future__ import annotations

import logging
import shutil
from collections import Counter
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Tuple

from sqlalchemy import select
from sqlalchemy.orm import Session

from . import frame_ref as fref
from . import sessions as sessions_mod
from .models import MediaAsset, SpatialAnnotation
from .projects import resolve_media_path
from .rectify import IMAGE_SPACE_RAW, transform_metadata

log = logging.getLogger(__name__)

# Raisons d'exclusion normalisées. Une annotation qui ne sort pas d'un export
# doit toujours porter l'une d'elles : rien ne doit disparaître en silence.
REASON_MEDIA_INTROUVABLE = "media_introuvable_en_base"
REASON_MEDIA_TYPE = "type_de_media_non_exportable"
REASON_FICHIER_INTROUVABLE = "fichier_media_introuvable"
REASON_FRAME_ILLISIBLE = "frame_non_extractible"
REASON_OFFSET_INDETERMINABLE = fref.REASON_OFFSET_INDETERMINABLE
REASON_GEOMETRIE_ILLISIBLE = "geometrie_illisible"
REASON_HORS_RANG = "taxon_absent_du_rang_demande"
REASON_FRAME_NON_IDENTIFIEE = "frame_contient_un_poisson_non_identifie"


class ExportExclusions:
    """Collecte les annotations laissées de côté par un export, avec la raison.

    L'export YOLO faisait un `continue` muet dès qu'une frame n'était pas
    résolvable : les ~20 annotations humaines de la base disparaissaient sans
    trace. Toute exclusion passe désormais par ici et ressort dans le retour de
    la fonction d'export et dans les métadonnées écrites sur disque.
    """

    def __init__(self) -> None:
        self._rows: List[Dict[str, Any]] = []

    def add(
        self,
        ann_id: Optional[str],
        reason: str,
        *,
        media_id: Optional[str] = None,
        frame_index: Optional[int] = None,
        detail: Optional[str] = None,
    ) -> None:
        self._rows.append({
            "annotation_id": ann_id,
            "media_id": media_id,
            "frame_index": frame_index,
            "reason": reason,
            "detail": detail,
        })

    def add_frame(self, export_frame: "ExportFrame", reason: str, *, detail: Optional[str] = None) -> None:
        """Exclut toutes les annotations d'une frame (image non produite)."""
        for ann in export_frame.annotations:
            self.add(
                ann.id,
                reason,
                media_id=export_frame.media_id,
                frame_index=export_frame.frame_index,
                detail=detail,
            )

    def rows(self) -> List[Dict[str, Any]]:
        return list(self._rows)

    def by_reason(self) -> Dict[str, int]:
        return dict(Counter(row["reason"] for row in self._rows))

    def annotation_ids(self) -> List[str]:
        return [row["annotation_id"] for row in self._rows if row["annotation_id"]]

    def summary(self) -> str:
        if not self._rows:
            return "0 annotation exclue"
        detail = ", ".join(
            f"{reason}: {n}" for reason, n in sorted(self.by_reason().items())
        )
        return f"{len(self._rows)} annotations exclues ({detail})"

    def as_dict(self) -> Dict[str, Any]:
        return {
            "count": len(self._rows),
            "by_reason": self.by_reason(),
            "summary": self.summary(),
            "rows": self.rows(),
        }

    def __len__(self) -> int:
        return len(self._rows)

    def __bool__(self) -> bool:
        return bool(self._rows)


@dataclass
class ExportFrame:
    """One exportable image: still photo or single video frame.

    `frame_index` est toujours un index **absolu** dans le fichier source :
    les lignes `timeline_legacy` sont converties à la collecte (ou exclues si
    l'offset de synchro n'est pas déterminable).
    """

    media_id: str
    frame_index: int
    media_type: str
    annotations: List[SpatialAnnotation] = field(default_factory=list)
    # Référentiel des lignes qui ont produit cette frame, avant conversion.
    frame_ref: str = fref.FRAME_REF_ABSOLUTE
    # Valeur telle qu'elle est stockée en base (utile pour retrouver la ligne).
    source_frame_index: Optional[int] = None
    # Renseignés par materialize_frame_image.
    image_space: Optional[str] = None
    calibration: Optional[Dict[str, Any]] = None

    @property
    def key(self) -> str:
        if self.media_type == "video":
            return f"{self.media_id}_{self.frame_index}"
        return self.media_id

    @property
    def file_name(self) -> str:
        if self.media_type == "video":
            return f"{self.media_id}_{self.frame_index}.jpg"
        return f"{self.media_id}.jpg"

    def meta(self) -> Dict[str, Any]:
        """Provenance de l'image, à recopier dans les métadonnées d'export."""
        out: Dict[str, Any] = {
            "media_id": self.media_id,
            "frame_index": self.frame_index,
            "frame_ref": self.frame_ref,
            "image_space": self.image_space or IMAGE_SPACE_RAW,
        }
        if self.source_frame_index is not None and self.source_frame_index != self.frame_index:
            out["source_frame_index"] = self.source_frame_index
        if self.calibration:
            out["calibration"] = self.calibration
        return out


def collect_export_frames(
    session: Session,
    project_ids: Optional[List[str]] = None,
    *,
    media_ids: Optional[List[str]] = None,
    exclusions: Optional[ExportExclusions] = None,
    timeline_offset: Optional[int] = None,
) -> List[ExportFrame]:
    """Group spatial annotations into export units (image or video frame).

    `timeline_offset` sert à convertir les lignes `timeline_legacy` en index
    absolu. Laissé à None, il est cherché d'abord sur la **session** du média
    (offset figé à l'activation), puis à défaut dans
    `camera_parameters/sync_frames.npy`. Quand il reste indéterminable, les
    lignes concernées sont **exclues avec leur raison** plutôt qu'exportées sur
    la mauvaise frame.
    """
    forced_offset = timeline_offset
    current_sync_offset = fref.timeline_offset() if forced_offset is None else None
    offset_by_media: Dict[str, Optional[int]] = {}

    def _offset_for(media_id: str) -> Optional[int]:
        if forced_offset is not None:
            return forced_offset
        if media_id not in offset_by_media:
            frozen = sessions_mod.frame_offset_for_media(session, media_id)
            offset_by_media[media_id] = (
                frozen if frozen is not None else current_sync_offset
            )
        return offset_by_media[media_id]

    q = select(SpatialAnnotation).where(
        SpatialAnnotation.geom_type.in_(["bbox", "point"])
    )
    wanted_media = set(media_ids or [])
    anns = list(session.scalars(q))
    buckets: Dict[Tuple[str, int], ExportFrame] = {}
    legacy_converted = 0

    for ann in anns:
        media = session.get(MediaAsset, ann.media_id)
        if not media:
            if exclusions is not None:
                exclusions.add(ann.id, REASON_MEDIA_INTROUVABLE, media_id=ann.media_id)
            continue
        if project_ids and media.project_id not in project_ids:
            # Filtre demandé par l'appelant : ce n'est pas une perte.
            continue
        if wanted_media and media.id not in wanted_media:
            continue
        if media.media_type not in ("image", "video"):
            if exclusions is not None:
                exclusions.add(
                    ann.id, REASON_MEDIA_TYPE, media_id=media.id,
                    detail=str(media.media_type),
                )
            continue

        if media.media_type != "video":
            # Photo : l'index de frame n'a pas de sens, aucune conversion.
            frame_idx = 0
            row_ref = fref.FRAME_REF_ABSOLUTE
            source_idx = None
        else:
            row_ref = fref.normalize(getattr(ann, "frame_ref", None))
            source_idx = int(ann.frame_index or 0)
            media_offset = _offset_for(media.id)
            converted = fref.to_absolute(source_idx, row_ref, media_offset)
            if converted is None:
                if exclusions is not None:
                    exclusions.add(
                        ann.id, REASON_OFFSET_INDETERMINABLE,
                        media_id=media.id, frame_index=source_idx,
                        detail="sync_frames.npy absent - offset timeline inconnu",
                    )
                continue
            if row_ref == fref.FRAME_REF_TIMELINE_LEGACY:
                legacy_converted += 1
            frame_idx = converted

        key = (media.id, frame_idx)
        if key not in buckets:
            buckets[key] = ExportFrame(
                media_id=media.id,
                frame_index=frame_idx,
                media_type=media.media_type,
                frame_ref=row_ref,
                source_frame_index=source_idx,
            )
        elif buckets[key].frame_ref != row_ref:
            # Deux référentiels sur la même frame : on garde la trace du plus
            # ancien pour que le rapport ne prétende pas à de l'absolu pur.
            buckets[key].frame_ref = fref.FRAME_REF_TIMELINE_LEGACY
        buckets[key].annotations.append(ann)

    if legacy_converted:
        used = sorted({
            off for off in offset_by_media.values() if off is not None
        }) if forced_offset is None else [forced_offset]
        log.warning(
            "%d annotation(s) en index timeline historique converties en absolu "
            "(offset(s) %s) - offset figé de la session quand il existe, sinon "
            "sync_frames.npy courant.",
            legacy_converted, used or [0],
        )

    return list(buckets.values())


def materialize_frame_image(
    session: Session,
    export_frame: ExportFrame,
    dest_path: Path,
    *,
    transform: Optional[Callable[[Any], Any]] = None,
    exclusions: Optional[ExportExclusions] = None,
) -> Optional[Tuple[int, int]]:
    """Write JPEG for export; return (width, height) or None on failure.

    `transform` applique une transformation à la frame décodée - typiquement
    `rectify.load_left_rectifier()`, qui reproduit exactement l'image sur
    laquelle l'opérateur a tracé ses boîtes. Sans transformation, l'image
    exportée est la frame **brute** et `export_frame.image_space` vaut `raw` :
    l'export reste utilisable, mais il le dit.

    La transformation n'est jamais appliquée aux photos : une image importée
    d'un dataset public n'appartient pas au banc stéréo.
    """
    media = session.get(MediaAsset, export_frame.media_id)
    if not media:
        export_frame.image_space = None
        if exclusions is not None:
            exclusions.add_frame(export_frame, REASON_MEDIA_INTROUVABLE)
        return None

    src = resolve_media_path(media)
    dest_path.parent.mkdir(parents=True, exist_ok=True)

    if media.media_type == "image":
        if not src.is_file():
            if exclusions is not None:
                exclusions.add_frame(
                    export_frame, REASON_FICHIER_INTROUVABLE, detail=str(src),
                )
            return None
        shutil.copy2(src, dest_path)
        export_frame.image_space = IMAGE_SPACE_RAW
        export_frame.calibration = None
        w, h = media.width or 640, media.height or 480
        try:
            import cv2

            img = cv2.imread(str(dest_path))
            if img is not None:
                h, w = img.shape[:2]
        except ImportError:
            pass
        return w, h

    if not src.is_file():
        if exclusions is not None:
            exclusions.add_frame(
                export_frame, REASON_FICHIER_INTROUVABLE, detail=str(src),
            )
        return None
    try:
        import cv2
    except ImportError:
        if exclusions is not None:
            exclusions.add_frame(
                export_frame, REASON_FRAME_ILLISIBLE, detail="OpenCV indisponible",
            )
        return None

    cap = cv2.VideoCapture(str(src))
    if not cap.isOpened():
        if exclusions is not None:
            exclusions.add_frame(
                export_frame, REASON_FICHIER_INTROUVABLE, detail=f"video illisible : {src}",
            )
        return None
    cap.set(cv2.CAP_PROP_POS_FRAMES, export_frame.frame_index)
    ok, frame = cap.read()
    cap.release()
    if not ok or frame is None:
        if exclusions is not None:
            exclusions.add_frame(
                export_frame, REASON_FRAME_ILLISIBLE,
                detail=f"frame {export_frame.frame_index} absente de {src.name}",
            )
        return None

    if transform is not None:
        try:
            frame = transform(frame)
        except Exception as exc:  # rectification impossible : on ne ment pas
            log.warning("Rectification impossible (%s) - image brute exportée", exc)
            transform = None

    meta = transform_metadata(transform)
    export_frame.image_space = meta.get("image_space", IMAGE_SPACE_RAW)
    export_frame.calibration = {
        k: v for k, v in meta.items() if k != "image_space"
    } or None

    cv2.imwrite(str(dest_path), frame)
    h, w = frame.shape[:2]
    return w, h


def crop_from_geometry(
    bgr,
    geom: dict,
    geom_type: str,
    img_w: int,
    img_h: int,
    *,
    pad: float = 0.08,
):
    """Extract BGR crop from geometry dict (normalized or pixel bbox)."""
    from .spatial import bbox_from_geometry

    if geom_type == "point":
        px = geom.get("x", geom.get("cx", 0.5))
        py = geom.get("y", geom.get("cy", 0.5))
        if max(px, py) > 1.0:
            px, py = px / img_w, py / img_h
        bw, bh = geom.get("w", 0.05), geom.get("h", 0.05)
        cx, cy, bw, bh = px, py, bw, bh
    else:
        cx, cy, bw, bh = bbox_from_geometry(geom, img_w, img_h)

    x1 = int((cx - bw / 2) * img_w)
    y1 = int((cy - bh / 2) * img_h)
    x2 = int((cx + bw / 2) * img_w)
    y2 = int((cy + bh / 2) * img_h)
    bw_px, bh_px = max(1, x2 - x1), max(1, y2 - y1)
    x1 = max(0, int(x1 - bw_px * pad))
    y1 = max(0, int(y1 - bh_px * pad))
    x2 = min(img_w, int(x2 + bw_px * pad))
    y2 = min(img_h, int(y2 + bh_px * pad))
    if x2 <= x1 or y2 <= y1:
        return None
    return bgr[y1:y2, x1:x2].copy()


def git_commit() -> Optional[str]:
    """Commit courant du dépôt, ou None hors dépôt git / sans git installé."""
    import subprocess

    repo = Path(__file__).resolve().parents[3]
    if not (repo / ".git").exists():
        return None
    try:
        out = subprocess.run(
            ["git", "rev-parse", "HEAD"], cwd=str(repo), capture_output=True,
            text=True, timeout=10,
        )
    except (OSError, subprocess.SubprocessError):
        return None
    return out.stdout.strip() or None if out.returncode == 0 else None


def export_image_space_summary(frames: List[ExportFrame]) -> Dict[str, Any]:
    """Récapitulatif d'espace image + calibration pour un manifeste d'export."""
    spaces = Counter(f.image_space for f in frames if f.image_space)
    calib = next((f.calibration for f in frames if f.calibration), None)
    frame_refs = Counter(f.frame_ref for f in frames)
    return {
        "image_space_counts": dict(spaces),
        "image_space": (spaces.most_common(1)[0][0] if spaces else IMAGE_SPACE_RAW),
        "frame_ref_counts": dict(frame_refs),
        "frame_index_convention": "absolute",
        "calibration": calib,
    }
