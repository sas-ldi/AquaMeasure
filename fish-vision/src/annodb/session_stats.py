"""Session-level statistics and taxon resolution helpers."""

from __future__ import annotations

import json
import logging
import uuid
from collections import Counter, defaultdict
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, Optional

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from . import frame_ref as fref
from . import sessions as sessions_mod
from .identification import (
    authoritative_identification_clause,
    is_authoritative_identification,
    resolve_track_taxa,
)
from .models import (
    MediaAsset,
    Project,
    SpatialAnnotation,
    TaxonNode,
    TemporalEvent,
    Track,
    TrackSample,
)
from .spatial import bbox_pixels_from_geometry, geometry_is_declared, geometry_space

log = logging.getLogger(__name__)


def _dt_iso(dt: Optional[datetime]) -> Optional[str]:
    if dt is None:
        return None
    if isinstance(dt, str):
        return dt
    return dt.isoformat(sep=" ", timespec="seconds")


def _session_datetime(media: MediaAsset) -> Optional[datetime]:
    if media.session_date:
        return media.session_date
    return media.captured_at


def _duration_s(media: MediaAsset) -> float:
    if media.fps and media.frame_count and media.fps > 0:
        return float(media.frame_count) / float(media.fps)
    return 0.0


def _resolve_hierarchy(session: Session, taxon_id: Optional[str]) -> dict[str, Optional[str]]:
    out: dict[str, Optional[str]] = {
        "family": None,
        "genus": None,
        "species": None,
        "common_name": None,
        "taxon_node_id": taxon_id,
    }
    if not taxon_id:
        return out
    chain: list[TaxonNode] = []
    current = session.get(TaxonNode, taxon_id)
    while current:
        chain.append(current)
        if not current.parent_id:
            break
        current = session.get(TaxonNode, current.parent_id)
    for node in chain:
        if node.rank == "family":
            out["family"] = node.scientific_name
        elif node.rank == "genus":
            out["genus"] = node.scientific_name
        elif node.rank == "species":
            out["species"] = node.scientific_name
            out["common_name"] = node.common_name
    # Les noeuds de rang « provisional » (Actinopterygii, fish) sont des
    # racines techniques : les recopier en espece ferait passer un poisson
    # non identifie pour une determination. Le rang reste vide -> NA.
    return out


TAXON_NA = "NA"


def _taxon_fields(hier: dict[str, Optional[str]]) -> dict[str, str]:
    """Colonnes taxonomiques du CSV - « NA » quand le rang n'est pas identifie.

    Une cellule vide se lit comme une donnee perdue ; NA dit que l'observateur
    n'a pas pu descendre a ce rang.
    """
    return {
        key: (hier.get(key) or TAXON_NA)
        for key in ("family", "genus", "species", "common_name")
    }


def _track_taxon_map(session: Session, media_id: str) -> dict[str, Optional[str]]:
    """track_id -> finest taxon_node_id."""
    tracks = session.scalars(select(Track).where(Track.media_id == media_id)).all()
    return resolve_track_taxa(session, tracks)


def _frame_offset(
    session: Optional[Session] = None, media_id: Optional[str] = None
) -> Optional[int]:
    """Décalage timeline → absolu, ou None s'il n'est pas déterminable.

    L'offset **figé** sur la session du média prime : c'est celui avec lequel
    les lignes ont été écrites. Le `sync_frames.npy` courant n'est qu'un repli
    - il aura changé dès qu'une nouvelle synchro aura été faite.
    """
    if session is not None and media_id:
        frozen = sessions_mod.frame_offset_for_media(session, media_id)
        if frozen is not None:
            return frozen
    return fref.timeline_offset()


def _to_absolute(
    frame_index: Optional[int],
    row_ref: Optional[str],
    offset: Optional[int],
    *,
    what: str = "ligne",
) -> Optional[int]:
    """Index absolu, avec avertissement explicite sur une ligne historique."""
    if not fref.is_legacy(row_ref):
        return None if frame_index is None else int(frame_index)
    converted = fref.to_absolute(frame_index, row_ref, offset)
    if converted is None:
        log.warning(
            "%s en index timeline historique et offset de sync indisponible - "
            "frame %s laissée telle quelle, comparaisons potentiellement fausses.",
            what, frame_index,
        )
        return None if frame_index is None else int(frame_index)
    return converted


def _events_by_track(
    session: Session, media_id: str, offset: Optional[int] = None,
) -> dict[str, list[tuple[int, int, str, str]]]:
    """Intervalles d'événements par piste, ramenés en index absolu.

    Tous les types du catalogue `event_types` sont pris, pas seulement la
    broute : le CSV doit dire *quel* comportement couvre la frame.

    Les pistes (`track_samples`) sont en index absolu : comparer des
    événements restés en index timeline ferait pointer les deux sur des images
    différentes - l'incohérence relevée à l'audit.
    """
    rows = session.execute(
        select(TemporalEvent, Track)
        .join(Track, TemporalEvent.track_id == Track.id)
        .where(Track.media_id == media_id)
    ).all()
    out: dict[str, list[tuple[int, int, str, str]]] = defaultdict(list)
    for event, _track in rows:
        row_ref = getattr(event, "frame_ref", None)
        what = "Broute" if event.event_type == "grazing" else "Événement"
        start = _to_absolute(event.frame_start, row_ref, offset, what=what)
        end = _to_absolute(event.frame_end, row_ref, offset, what=what)
        out[event.track_id].append((start, end, event.id, event.event_type or "grazing"))
    return out


def _event_at_frame(
    track_id: str,
    frame_index: int,
    events_map: dict[str, list[tuple[int, int, str, str]]],
) -> tuple[int, Optional[str], str]:
    """(is_grazing, event_id, event_type) de l'événement couvrant cette frame."""
    for start, end, event_id, event_type in events_map.get(track_id, []):
        if start <= frame_index <= end:
            return (1 if event_type == "grazing" else 0), event_id, event_type
    return 0, None, ""


def compute_session_stats(session: Session, media: MediaAsset) -> dict[str, Any]:
    media_id = media.id
    duration_s = _duration_s(media)
    duration_min = duration_s / 60.0 if duration_s > 0 else 0.0

    grazing_rows = session.execute(
        select(TemporalEvent, Track)
        .join(Track, TemporalEvent.track_id == Track.id)
        .where(Track.media_id == media_id, TemporalEvent.event_type == "grazing")
    ).all()
    grazing_count = len(grazing_rows)
    grazing_frames = sum(max(0, e.frame_end - e.frame_start + 1) for e, _ in grazing_rows)
    grazing_duration_s = grazing_frames / media.fps if media.fps and media.fps > 0 else 0.0
    grazing_freq_per_min = (grazing_count / duration_min) if duration_min > 0 else None

    max_on_screen = None
    frame_counts = session.execute(
        select(TrackSample.frame_index, func.count(func.distinct(TrackSample.track_id)))
        .join(Track, TrackSample.track_id == Track.id)
        .where(Track.media_id == media_id)
        .group_by(TrackSample.frame_index)
    ).all()
    if frame_counts:
        max_on_screen = max(n for _, n in frame_counts)

    track_taxon = _track_taxon_map(session, media_id)
    species_ids = {
        node.id for node in session.scalars(
            select(TaxonNode).where(TaxonNode.rank == "species")
        )
    }
    species_in_session: set[str] = set()
    for tid in track_taxon.values():
        if tid in species_ids:
            species_in_session.add(tid)
    for ann in session.scalars(
        select(SpatialAnnotation).where(
            SpatialAnnotation.media_id == media_id,
            authoritative_identification_clause(SpatialAnnotation),
        )
    ):
        # Une annotation liée contribue par l'autorité unique de sa piste,
        # jamais comme une seconde observation taxonomique indépendante.
        if ann.track_id:
            continue
        if ann.taxon_node_id in species_ids:
            species_in_session.add(ann.taxon_node_id)

    max_per_species: dict[str, int] = defaultdict(int)
    samples = session.execute(
        select(TrackSample.frame_index, TrackSample.track_id)
        .join(Track, TrackSample.track_id == Track.id)
        .where(Track.media_id == media_id)
    ).all()
    by_frame: dict[int, list[str]] = defaultdict(list)
    for frame_index, track_id in samples:
        by_frame[frame_index].append(track_id)
    for frame_index, track_ids in by_frame.items():
        per_taxon: Counter[str] = Counter()
        for tid in track_ids:
            tax = track_taxon.get(tid)
            if tax in species_ids:
                per_taxon[tax] += 1
        for tax, n in per_taxon.items():
            max_per_species[tax] = max(max_per_species[tax], n)

    max_per_species_named: list[dict[str, Any]] = []
    for tax_id, n in sorted(max_per_species.items(), key=lambda x: -x[1]):
        hier = _resolve_hierarchy(session, tax_id)
        max_per_species_named.append({
            "taxon_node_id": tax_id,
            "max_concurrent": n,
            **hier,
        })

    has_tracking = session.scalar(
        select(func.count(TrackSample.id))
        .join(Track, TrackSample.track_id == Track.id)
        .where(Track.media_id == media_id)
    ) or 0

    legacy = _legacy_frame_counts(session, media_id)
    offset = _frame_offset(session, media_id)
    frame_ref_warning = ""
    if legacy["total"]:
        frame_ref_warning = (
            f"{legacy['total']} ligne(s) en index timeline historique - "
            + (
                f"converties (+{offset}) à la lecture."
                if offset is not None
                else "offset de sync indisponible, conversion impossible."
            )
        )
        log.warning("Session %s : %s", media_id, frame_ref_warning)

    return {
        "media_id": media_id,
        "video_name": Path(media.rel_path).name,
        "rel_path": media.rel_path,
        "site": media.site or "",
        "session_title": media.session_title or "",
        "notes": media.notes or "",
        "session_date": _dt_iso(_session_datetime(media)),
        "duration_s": round(duration_s, 2),
        "fps": media.fps,
        "frame_count": media.frame_count,
        "grazing_count": grazing_count,
        "grazing_duration_s": round(grazing_duration_s, 2),
        "grazing_freq_per_min": round(grazing_freq_per_min, 3) if grazing_freq_per_min is not None else None,
        "max_fish_on_screen": max_on_screen,
        "species_count": len(species_in_session),
        "max_per_species": max_per_species_named,
        "has_tracking": has_tracking > 0,
        "frame_index_convention": fref.FRAME_REF_ABSOLUTE,
        "legacy_frame_ref_counts": legacy,
        "frame_ref_warning": frame_ref_warning,
    }


def _legacy_frame_counts(session: Session, media_id: str) -> dict[str, int]:
    """Combien de lignes portent encore un index timeline historique."""
    anns = 0
    for ann in session.scalars(
        select(SpatialAnnotation).where(SpatialAnnotation.media_id == media_id)
    ):
        if fref.is_legacy(getattr(ann, "frame_ref", None)):
            anns += 1
    events = 0
    for event, _track in session.execute(
        select(TemporalEvent, Track)
        .join(Track, TemporalEvent.track_id == Track.id)
        .where(Track.media_id == media_id)
    ).all():
        if fref.is_legacy(getattr(event, "frame_ref", None)):
            events += 1
    return {
        "spatial_annotations": anns,
        "temporal_events": events,
        "total": anns + events,
    }


def _bbox_csv_fields(geom: dict, media: MediaAsset) -> dict[str, Any]:
    """Colonnes bbox du CSV, en pixels de l'image de référence.

    L'ancienne version lisait des clés (`x1`, `left`) qu'aucune écriture ne
    produit - les colonnes sortaient vides. On passe par la géométrie
    déclarée, avec repli sur le reniflage de magnitude pour l'historique.
    """
    empty = {
        "bbox_x1": "", "bbox_y1": "", "bbox_x2": "", "bbox_y2": "",
        "cx": "", "cy": "",
    }
    if not geom:
        return empty
    img_w = int(media.width or 0)
    img_h = int(media.height or 0)
    if img_w <= 0 or img_h <= 0:
        if not geometry_is_declared(geom):
            return empty
        ref_w = geom.get("ref_width")
        ref_h = geom.get("ref_height")
        img_w, img_h = int(ref_w or 0), int(ref_h or 0)
        if img_w <= 0 or img_h <= 0:
            return empty
    try:
        x1, y1, x2, y2 = bbox_pixels_from_geometry(geom, img_w, img_h)
    except (ValueError, TypeError, KeyError):
        return empty
    return {
        "bbox_x1": round(x1, 2),
        "bbox_y1": round(y1, 2),
        "bbox_x2": round(x2, 2),
        "bbox_y2": round(y2, 2),
        "cx": round((x1 + x2) / 2, 2),
        "cy": round((y1 + y2) / 2, 2),
    }


def iter_session_timeline_rows(session: Session, media: MediaAsset):
    """Yield dict rows for CSV export (track_samples or spatial fallback).

    Tous les `frame_index` sortis ici sont des index **absolus** du fichier
    source : les lignes `timeline_legacy` sont converties, et la colonne
    `frame_ref` dit d'où vient chaque valeur.
    """
    media_id = media.id
    fps = media.fps or 0.0
    base_dt = _session_datetime(media)
    offset = _frame_offset(session, media_id)
    track_taxon = _track_taxon_map(session, media_id)
    events_map = _events_by_track(session, media_id, offset)

    measure_by_track_frame: dict[tuple[str, int], float] = {}
    ann_by_track_frame: dict[tuple[str, int], SpatialAnnotation] = {}
    for ann in session.scalars(
        select(SpatialAnnotation).where(SpatialAnnotation.media_id == media_id)
    ):
        if ann.track_id:
            abs_idx = _to_absolute(
                ann.frame_index, getattr(ann, "frame_ref", None), offset,
                what="Annotation",
            )
            ann_by_track_frame[(ann.track_id, abs_idx)] = ann
            if ann.measurement_mm is not None:
                measure_by_track_frame[(ann.track_id, abs_idx)] = ann.measurement_mm

    sample_count = session.scalar(
        select(func.count(TrackSample.id))
        .join(Track, TrackSample.track_id == Track.id)
        .where(Track.media_id == media_id)
    ) or 0

    def _base_row() -> dict[str, Any]:
        return {
            "session_date": _dt_iso(_session_datetime(media)) or "",
            "site": media.site or "",
            "session_title": media.session_title or "",
            "video_name": Path(media.rel_path).name,
            "media_id": media_id,
        }

    def _time_fields(frame_index: int, row_ref: str) -> dict[str, Any]:
        seconds = (frame_index / fps) if fps > 0 else 0.0
        wall = ""
        if base_dt and fps > 0:
            wall = (base_dt + timedelta(seconds=seconds)).isoformat(sep=" ", timespec="seconds")
        return {
            "frame_index": frame_index,
            "frame_ref": row_ref,
            "time_offset_s": round(seconds, 3),
            "wall_clock_time": wall,
        }

    if sample_count > 0:
        rows = session.execute(
            select(TrackSample, Track)
            .join(Track, TrackSample.track_id == Track.id)
            .where(Track.media_id == media_id)
            .order_by(TrackSample.frame_index, Track.external_track_id)
        ).all()
        for sample, track in rows:
            tax_id = track_taxon.get(track.id)
            hier = _resolve_hierarchy(session, tax_id)
            is_grazing, event_id, event_type = _event_at_frame(
                track.id, sample.frame_index, events_map,
            )
            try:
                bbox = json.loads(sample.bbox_json)
            except (json.JSONDecodeError, TypeError):
                bbox = {}
            mkey = (track.id, sample.frame_index)
            yield {
                **_base_row(),
                # track_samples est déjà en index absolu (tracking_worker).
                **_time_fields(sample.frame_index, fref.FRAME_REF_ABSOLUTE),
                "track_id": track.id,
                "external_track_id": track.external_track_id,
                **_taxon_fields(hier),
                "is_grazing": is_grazing,
                "grazing_event_id": event_id or "",
                "event_type": event_type,
                "bbox_x1": bbox.get("x1", bbox.get("x_min", "")),
                "bbox_y1": bbox.get("y1", bbox.get("y_min", "")),
                "bbox_x2": bbox.get("x2", bbox.get("x_max", "")),
                "bbox_y2": bbox.get("y2", bbox.get("y_max", "")),
                # L'espace des échantillons de piste n'est pas encore stocké
                # (colonne à ajouter en phase Provenance).
                "geometry_space": "",
                "cx": sample.cx,
                "cy": sample.cy,
                "measurement_mm": measure_by_track_frame.get(mkey, ""),
                "position_x_mm": "",
                "position_y_mm": "",
                "position_z_mm": "",
            }
        return

    for ann in session.scalars(
        select(SpatialAnnotation)
        .where(SpatialAnnotation.media_id == media_id)
        .order_by(SpatialAnnotation.frame_index)
    ):
        tax_id = (
            track_taxon.get(ann.track_id)
            if ann.track_id
            else (
                ann.taxon_node_id
                if is_authoritative_identification(ann)
                else None
            )
        )
        hier = _resolve_hierarchy(session, tax_id)
        row_ref = fref.normalize(getattr(ann, "frame_ref", None))
        abs_idx = _to_absolute(ann.frame_index, row_ref, offset, what="Annotation")
        is_grazing, event_id, event_type = (0, None, "")
        if ann.track_id:
            is_grazing, event_id, event_type = _event_at_frame(
                ann.track_id, abs_idx, events_map,
            )
        try:
            geom = json.loads(ann.geometry_json)
        except (json.JSONDecodeError, TypeError):
            geom = {}
        bbox = _bbox_csv_fields(geom, media)
        yield {
            **_base_row(),
            **_time_fields(abs_idx, row_ref),
            "track_id": ann.track_id or "",
            "external_track_id": "",
            **_taxon_fields(hier),
            "is_grazing": is_grazing,
            "grazing_event_id": event_id or "",
            "event_type": event_type,
            **bbox,
            "geometry_space": geometry_space(geom, "") or "",
            "measurement_mm": ann.measurement_mm if ann.measurement_mm is not None else "",
            "position_x_mm": ann.position_x_mm if ann.position_x_mm is not None else "",
            "position_y_mm": ann.position_y_mm if ann.position_y_mm is not None else "",
            "position_z_mm": ann.position_z_mm if ann.position_z_mm is not None else "",
        }


TIMELINE_CSV_FIELDS = [
    "session_date", "site", "session_title", "video_name", "media_id",
    # frame_index : index ABSOLU dans le fichier vidéo source.
    # frame_ref : référentiel d'origine de la ligne (absolute | timeline_legacy).
    "frame_index", "frame_ref", "time_offset_s", "wall_clock_time",
    "track_id", "external_track_id",
    "family", "genus", "species", "common_name",
    # event_type : clé du catalogue event_types (grazing, …) couvrant la frame.
    "is_grazing", "grazing_event_id", "event_type",
    "bbox_x1", "bbox_y1", "bbox_x2", "bbox_y2",
    "geometry_space",
    "cx", "cy", "measurement_mm",
    "position_x_mm", "position_y_mm", "position_z_mm",
]


def default_timeline_filename(media: MediaAsset) -> str:
    stem = Path(media.rel_path).stem
    site = (media.site or "site").replace(" ", "_")
    date_part = "session"
    dt = _session_datetime(media)
    if dt:
        date_part = dt.strftime("%Y%m%d_%H%M")
    return f"{date_part}_{site}_{stem}_timeline.csv"


def log_export_run(session: Session, media_id: str, output_path: Path, row_count: int) -> None:
    from .export_media import git_commit
    from .models import EXPORT_STATUS_COMPLETED, ExportRun

    path = Path(output_path)
    size = None
    try:
        size = path.stat().st_size if path.is_file() else None
    except OSError:
        size = None
    session.add(ExportRun(
        id=str(uuid.uuid4()),
        format="csv_timeline",
        taxonomy_rank="fish",
        filter_json=json.dumps({"media_id": media_id}),
        output_path=str(output_path),
        annotation_count=row_count,
        git_commit=git_commit(),
        total_bytes=size,
        status=EXPORT_STATUS_COMPLETED,
    ))
