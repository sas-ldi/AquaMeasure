"""API haut niveau — sessions, stats, espèces, export CSV."""

from __future__ import annotations

import json
import sys
from datetime import datetime
from pathlib import Path
from typing import Any, Optional

_APP_ROOT = Path(__file__).resolve().parent
_FV_ROOT = _APP_ROOT / "fish-vision"
if str(_FV_ROOT) not in sys.path:
    sys.path.insert(0, str(_FV_ROOT))

from fish_annotate import REGISTRY_PROJECT, _ensure_db, is_available  # noqa: E402

_FISHIAL_LABELS = _FV_ROOT / "models" / "fishial_labels.json"


def _db_ok() -> bool:
    return is_available()


def is_available() -> bool:
    from fish_annotate import is_available as _fa_avail
    return _fa_avail()


def _parse_date(s: Optional[str]) -> Optional[datetime]:
    if not s:
        return None
    for fmt in ("%Y-%m-%d", "%Y-%m-%d %H:%M:%S", "%Y-%m-%dT%H:%M:%S"):
        try:
            return datetime.strptime(s[:19].replace("T", " "), fmt)
        except ValueError:
            continue
    try:
        return datetime.fromisoformat(s)
    except ValueError:
        return None


def get_app_settings() -> dict[str, Any]:
    from src.annodb.app_settings import load_settings
    return load_settings()


def set_app_settings(**kwargs: Any) -> dict[str, Any]:
    from src.annodb.app_settings import save_settings
    return save_settings(kwargs)


def list_sites(project: str = REGISTRY_PROJECT) -> list[str]:
    from sqlalchemy import distinct, select
    from src.annodb.connection import session_scope
    from src.annodb.models import MediaAsset, Project

    if not _db_ok():
        return []
    _ensure_db()
    with session_scope() as session:
        proj = session.scalar(select(Project).where(Project.name == project))
        if not proj:
            return []
        rows = session.scalars(
            select(distinct(MediaAsset.site))
            .where(MediaAsset.project_id == proj.id, MediaAsset.site.isnot(None))
            .order_by(MediaAsset.site)
        )
        return [s for s in rows if s]


def list_sessions(
    *,
    project: str = REGISTRY_PROJECT,
    date_from: Optional[str] = None,
    date_to: Optional[str] = None,
    site: Optional[str] = None,
    species_id: Optional[str] = None,
) -> list[dict[str, Any]]:
    from sqlalchemy import select
    from src.annodb.connection import session_scope
    from src.annodb.models import MediaAsset, Project, SpatialAnnotation, Track
    from src.annodb.session_stats import compute_session_stats

    if not _db_ok():
        return []
    _ensure_db()
    dt_from = _parse_date(date_from)
    dt_to = _parse_date(date_to)

    with session_scope() as session:
        proj = session.scalar(select(Project).where(Project.name == project))
        if not proj:
            return []
        q = (
            select(MediaAsset)
            .where(MediaAsset.project_id == proj.id, MediaAsset.media_type == "video")
            .order_by(MediaAsset.session_date.desc().nullslast(), MediaAsset.created_at.desc())
        )
        if site:
            q = q.where(MediaAsset.site == site)
        media_list = list(session.scalars(q))

        if species_id:
            media_ids_ann = {
                m for m in session.scalars(
                    select(SpatialAnnotation.media_id).where(
                        SpatialAnnotation.taxon_node_id == species_id
                    )
                )
            }
            media_ids_track = {
                m for m in session.scalars(
                    select(Track.media_id).where(Track.taxon_node_id == species_id)
                )
            }
            allowed = media_ids_ann | media_ids_track
            media_list = [m for m in media_list if m.id in allowed]

        out: list[dict[str, Any]] = []
        for media in media_list:
            stats = compute_session_stats(session, media)
            sd = media.session_date or media.captured_at
            if dt_from and sd and sd < dt_from:
                continue
            if dt_to and sd and sd > dt_to:
                continue
            out.append(stats)
        return out


def get_session_detail(media_id: str) -> Optional[dict[str, Any]]:
    from src.annodb.connection import session_scope
    from src.annodb.models import MediaAsset
    from src.annodb.session_stats import compute_session_stats

    if not _db_ok():
        return None
    _ensure_db()
    with session_scope() as session:
        media = session.get(MediaAsset, media_id)
        if not media:
            return None
        return compute_session_stats(session, media)


def get_session_metadata(media_id: str) -> Optional[dict[str, Any]]:
    from src.annodb.connection import session_scope
    from src.annodb.models import MediaAsset
    from src.annodb.session_stats import _dt_iso, _session_datetime

    if not _db_ok():
        return None
    _ensure_db()
    with session_scope() as session:
        media = session.get(MediaAsset, media_id)
        if not media:
            return None
        return {
            "media_id": media.id,
            "rel_path": media.rel_path,
            "video_name": Path(media.rel_path).name,
            "site": media.site or "",
            "session_title": media.session_title or "",
            "notes": media.notes or "",
            "session_date": _dt_iso(_session_datetime(media)),
        }


def update_session_metadata(
    media_id: str,
    *,
    site: Optional[str] = None,
    session_title: Optional[str] = None,
    notes: Optional[str] = None,
    session_date: Optional[str] = None,
) -> bool:
    from src.annodb.connection import session_scope
    from src.annodb.models import MediaAsset

    if not _db_ok():
        return False
    _ensure_db()
    with session_scope() as session:
        media = session.get(MediaAsset, media_id)
        if not media:
            return False
        if site is not None:
            media.site = site.strip() or None
        if session_title is not None:
            media.session_title = session_title.strip() or None
        if notes is not None:
            media.notes = notes.strip() or None
        if session_date is not None:
            media.session_date = _parse_date(session_date) if session_date.strip() else None
        return True


def update_session_metadata_by_path(
    media_path: str,
    *,
    project: str = REGISTRY_PROJECT,
    **fields: Any,
) -> bool:
    from fish_annotate import resolve_media_id

    mid = resolve_media_id(media_path, project=project, create=True)
    if not mid:
        return False
    return update_session_metadata(mid, **fields)


def assign_track_taxon(track_id: str, taxon_node_id: Optional[str]) -> bool:
    """Recalcule l'autorité de piste depuis ses observations humaines.

    ``taxon_node_id`` est conservé dans la signature pour compatibilité avec
    les anciens appelants, mais ne peut jamais départager deux observations
    autoritaires contradictoires.
    """
    from src.annodb.connection import session_scope
    from src.annodb.models import Track
    from src.annodb.tracks import recompute_track_taxonomy

    if not _db_ok() or not track_id:
        return False
    _ensure_db()
    with session_scope() as session:
        if session.get(Track, track_id) is None:
            return False
        _ = taxon_node_id
        recompute_track_taxonomy(session, track_id)
        return True


def _fishial_catalog_names() -> set[str]:
    if not _FISHIAL_LABELS.is_file():
        return set()
    try:
        data = json.loads(_FISHIAL_LABELS.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return set()
    if isinstance(data, dict):
        return {str(v).strip().lower() for v in data.values()}
    return set()


def count_validated_crops(taxon_id: str, project: str = REGISTRY_PROJECT) -> int:
    from sqlalchemy import func, select
    from src.annodb.connection import session_scope
    from src.annodb.identification import authoritative_identification_clause
    from src.annodb.models import MediaAsset, Project, SpatialAnnotation

    if not _db_ok():
        return 0
    _ensure_db()
    with session_scope() as session:
        proj = session.scalar(select(Project).where(Project.name == project))
        if not proj:
            return 0
        return session.scalar(
            select(func.count(SpatialAnnotation.id))
            .join(MediaAsset, SpatialAnnotation.media_id == MediaAsset.id)
            .where(
                MediaAsset.project_id == proj.id,
                SpatialAnnotation.taxon_node_id == taxon_id,
                authoritative_identification_clause(SpatialAnnotation),
            )
        ) or 0


def _promotion_eligibility(
    node: Any,
    crop_count: int,
    min_refs: int,
    catalog: set[str],
) -> dict[str, Any]:
    in_catalog = bool(
        node
        and node.scientific_name
        and node.scientific_name.strip().lower() in catalog
    )
    if not node or node.rank != "species":
        reason = "Seuls les taxons de rang espèce peuvent enrichir Fishial."
        eligible = False
    elif crop_count <= 0:
        reason = "Aucune image disponible pour cette espèce."
        eligible = False
    elif crop_count < min_refs:
        reason = (
            f"Seuil insuffisant : {crop_count}/{min_refs} image(s) validée(s)."
        )
        eligible = False
    else:
        reason = (
            "Prête à être ajoutée à la bibliothèque Fishial locale."
            if not in_catalog
            else "Prête pour des références Fishial locales supplémentaires."
        )
        eligible = True
    return {
        "eligible": eligible,
        "reason": reason,
        "crop_count": int(crop_count),
        "min_refs": int(min_refs),
        "in_fishial_catalog": in_catalog,
    }


def list_species_summary(
    *,
    project: str = REGISTRY_PROJECT,
    site: Optional[str] = None,
) -> list[dict[str, Any]]:
    from sqlalchemy import func, select
    from src.annodb.connection import session_scope
    from src.annodb.identification import authoritative_identification_clause
    from src.annodb.models import (
        MediaAsset,
        Project,
        SpatialAnnotation,
        TaxonNode,
    )

    if not _db_ok():
        return []
    _ensure_db()
    catalog = _fishial_catalog_names()
    min_refs = int(get_app_settings().get("fishial_min_refs", 5))

    with session_scope() as session:
        proj = session.scalar(select(Project).where(Project.name == project))
        media_filter = [MediaAsset.project_id == (proj.id if proj else "")]
        if site:
            media_filter.append(MediaAsset.site == site)

        ann_rows = session.execute(
            select(
                SpatialAnnotation.taxon_node_id,
                func.count(SpatialAnnotation.id),
                func.count(SpatialAnnotation.crop_path).filter(
                    SpatialAnnotation.crop_path != ""
                ),
                func.count(func.distinct(SpatialAnnotation.media_id)),
            )
            .join(MediaAsset, SpatialAnnotation.media_id == MediaAsset.id)
            .where(
                *media_filter,
                authoritative_identification_clause(SpatialAnnotation),
            )
            .group_by(SpatialAnnotation.taxon_node_id)
        ).all()

        from collections import Counter
        from fishial_gallery import active_reference_vectors

        references = active_reference_vectors(session)
        gallery_counts = Counter(ref.taxon_node_id for ref, _vector in references)
        standalone_counts = Counter(ref.taxon_node_id for ref, _vector in references
                                    if not ref.spatial_annotation_id)
        referenced_annotations = {
            ref.spatial_annotation_id for ref, _vector in references
            if ref.spatial_annotation_id
        }
        # Compter les observations précises restant à convertir. Une référence
        # historique sans observation ne doit pas masquer une nouvelle image.
        pending_counts = Counter(
            taxon_id for annotation_id, taxon_id in session.execute(
                select(SpatialAnnotation.id, SpatialAnnotation.taxon_node_id)
                .join(MediaAsset, SpatialAnnotation.media_id == MediaAsset.id)
                .where(*media_filter, authoritative_identification_clause(SpatialAnnotation))
            )
            if annotation_id not in referenced_annotations
        )
        project_crop_counts = dict(
            session.execute(
                select(
                    SpatialAnnotation.taxon_node_id,
                    func.count(SpatialAnnotation.id),
                )
                .join(MediaAsset, SpatialAnnotation.media_id == MediaAsset.id)
                .where(
                    MediaAsset.project_id == (proj.id if proj else ""),
                    authoritative_identification_clause(SpatialAnnotation),
                )
                .group_by(SpatialAnnotation.taxon_node_id)
            ).all()
        )

        # Les références transférées sont utilisables sans créer de fausses
        # observations. Elles doivent aussi être visibles sur un poste neuf.
        from src.annodb.fishial_transfer import reference_crop_path

        imported_crops = Counter(
            ref.taxon_node_id for ref, _vector in references
            if not ref.spatial_annotation_id and reference_crop_path(session, ref)
        )
        present = {row[0] for row in ann_rows}
        ann_rows = list(ann_rows) + [
            (taxon_id, 0, 0, 0) for taxon_id in gallery_counts if taxon_id not in present
        ]
        out: list[dict[str, Any]] = []
        for tax_id, annotation_count, stored_crop_count, session_count in ann_rows:
            if not tax_id:
                continue
            node = session.get(TaxonNode, tax_id)
            if not node or node.rank != "species":
                continue
            sci = node.scientific_name
            in_catalog = sci.strip().lower() in catalog if catalog else False
            promotion = _promotion_eligibility(
                node,
                int(project_crop_counts.get(tax_id, 0)) + standalone_counts[tax_id],
                min_refs,
                catalog,
            )
            out.append({
                "taxon_node_id": tax_id,
                "scientific_name": sci,
                "common_name": node.common_name or "",
                "rank": node.rank,
                "is_provisional": bool(node.is_provisional),
                "crop_count": int(stored_crop_count) + imported_crops[tax_id],
                "annotation_count": int(annotation_count),
                "session_count": int(session_count),
                "in_fishial_catalog": in_catalog,
                "gallery_ref_count": int(gallery_counts.get(tax_id, 0)),
                "pending_reference_count": int(pending_counts.get(tax_id, 0)),
                "promotion_eligible": promotion["eligible"],
                "promotion_reason": promotion["reason"],
                "promotion_min_refs": promotion["min_refs"],
            })
        out.sort(key=lambda r: (-r["crop_count"], -r["annotation_count"], r["scientific_name"]))
        return out


def species_promotion_eligibility(
    taxon_id: str,
    min_refs: Optional[int] = None,
) -> dict[str, Any]:
    if min_refs is None:
        min_refs = int(get_app_settings().get("fishial_min_refs", 5))
    from sqlalchemy import select
    from src.annodb.connection import session_scope
    from src.annodb.models import TaxonNode

    _ensure_db()
    with session_scope() as session:
        node = session.get(TaxonNode, taxon_id)
        crop_count = count_validated_crops(taxon_id)
        from fishial_gallery import active_reference_vectors

        crop_count += sum(1 for ref, _ in active_reference_vectors(session)
                          if ref.taxon_node_id == taxon_id and not ref.spatial_annotation_id)
        catalog = _fishial_catalog_names()
        return _promotion_eligibility(node, crop_count, int(min_refs), catalog)


def species_eligible_for_promotion(taxon_id: str, min_refs: Optional[int] = None) -> bool:
    return bool(species_promotion_eligibility(taxon_id, min_refs)["eligible"])


def list_annotations(
    *,
    project: str = REGISTRY_PROJECT,
    media_id: Optional[str] = None,
    species_id: Optional[str] = None,
    site: Optional[str] = None,
    limit: int = 2000,
) -> list[dict[str, Any]]:
    """Observations détaillées (bbox, mesure, position 3D) pour l'UI base de données."""
    from fish_annotate import list_observations

    if not _db_ok():
        return []
    rows = list_observations(project=project, media_id=media_id, limit=limit)
    if species_id:
        key = "species_id"
        rows = [r for r in rows if r.get(key) == species_id or r.get("taxon_node_id") == species_id]
    if site:
        sessions = {s["media_id"] for s in list_sessions(project=project, site=site)}
        rows = [r for r in rows if r.get("media_id") in sessions]
    for r in rows:
        g = r.get("geometry") or {}
        if "x_min" in g:
            r["bbox_text"] = (
                f"{int(g['x_min'])},{int(g['y_min'])}–"
                f"{int(g['x_max'])},{int(g['y_max'])}"
            )
        elif "cx" in g:
            r["bbox_text"] = f"cx={g['cx']:.3f} cy={g['cy']:.3f}"
        else:
            r["bbox_text"] = "—"
        if r.get("position_z_mm") is not None:
            r["position_text"] = (
                f"{r['position_x_mm']:.0f}, "
                f"{r['position_y_mm']:.0f}, "
                f"{r['position_z_mm']:.0f} mm"
            )
        else:
            r["position_text"] = "—"
        r["measure_text"] = (
            f"{r['measurement_mm']:.1f} mm" if r.get("measurement_mm") else "—"
        )
        # « NA » une fois la ligne validee : le rang a ete declare non
        # identifie, contre « ? » tant que personne ne l'a revue.
        status = r.get("identification_status")
        fallback = "NA" if status == "unidentifiable" else "?"
        r["species_label"] = (
            r.get("species") or r.get("genus") or r.get("family") or fallback
        )
    return rows


def export_session_timeline_csv(media_id: str, output_path: str | Path) -> dict[str, Any]:
    from src.annodb.export_session_csv import export_session_timeline_csv as _export

    if not _db_ok():
        raise RuntimeError("Base de données indisponible")
    _ensure_db()
    return _export(media_id, Path(output_path))


def export_session_timeline_auto_path(media_id: str, output_dir: str | Path) -> dict[str, Any]:
    from src.annodb.export_session_csv import export_session_timeline_auto_path as _export

    if not _db_ok():
        raise RuntimeError("Base de données indisponible")
    _ensure_db()
    return _export(media_id, Path(output_dir))


def export_abundance_csv(media_id: str, output_path: str | Path) -> dict[str, Any]:
    from src.annodb.export_abundance_csv import export_abundance_csv as _export

    if not _db_ok():
        raise RuntimeError("Base de données indisponible")
    _ensure_db()
    return _export(media_id, Path(output_path))


def export_abundance_auto_path(media_id: str, output_dir: str | Path) -> dict[str, Any]:
    """Comptages image par image (MaxN) — le CSV que le bouton « Abondance »
    promettait et ne produisait pas."""
    from src.annodb.export_abundance_csv import export_abundance_auto_path as _export

    if not _db_ok():
        raise RuntimeError("Base de données indisponible")
    _ensure_db()
    return _export(media_id, Path(output_dir))


def iter_session_timeline_rows(media_id: str):
    from src.annodb.connection import session_scope
    from src.annodb.models import MediaAsset
    from src.annodb.session_stats import iter_session_timeline_rows as _iter

    if not _db_ok():
        return
    _ensure_db()
    with session_scope() as session:
        media = session.get(MediaAsset, media_id)
        if not media:
            return
        yield from _iter(session, media)


def _json_safe(value: Any) -> Any:
    if value is None:
        return None
    if isinstance(value, (str, int, float, bool)):
        return value
    if isinstance(value, datetime):
        return value.isoformat(sep=" ", timespec="seconds")
    if isinstance(value, dict):
        return {k: _json_safe(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [_json_safe(v) for v in value]
    return str(value)


def list_grazing_for_media(
    media_id: str, *, event_type: Optional[str] = "grazing"
) -> list[dict[str, Any]]:
    """Evenements d'un media. `frame_*_abs` = index absolu, None si non convertible.

    `event_type='grazing'` par defaut (compatibilite des exports broute) ;
    None renvoie tous les types du catalogue.
    """
    from sqlalchemy import select
    from src.annodb import frame_ref as fref
    from src.annodb.connection import session_scope
    from src.annodb.models import TemporalEvent, Track
    from src.annodb.sessions import frame_offset_for_media

    if not _db_ok():
        return []
    _ensure_db()
    with session_scope() as session:
        # Offset fige de la session d'abord ; sync_frames.npy courant a defaut.
        offset = frame_offset_for_media(session, media_id)
        if offset is None:
            offset = fref.timeline_offset()
        stmt = (
            select(TemporalEvent, Track)
            .join(Track, TemporalEvent.track_id == Track.id)
            .where(Track.media_id == media_id)
            .order_by(TemporalEvent.frame_start)
        )
        if event_type:
            stmt = stmt.where(TemporalEvent.event_type == event_type)
        rows = session.execute(stmt).all()
        out: list[dict[str, Any]] = []
        for ev, track in rows:
            row_ref = fref.normalize(getattr(ev, "frame_ref", None))
            out.append({
                "event_id": ev.id,
                "track_id": track.id,
                "external_track_id": track.external_track_id,
                "frame_start": ev.frame_start,
                "frame_end": ev.frame_end,
                "frame_ref": row_ref,
                "frame_start_abs": fref.to_absolute(ev.frame_start, row_ref, offset),
                "frame_end_abs": fref.to_absolute(ev.frame_end, row_ref, offset),
                "event_type": ev.event_type or "grazing",
                "source": ev.source,
            })
        return out


def update_taxon_scientific_name(taxon_id: str, scientific_name: str) -> bool:
    from src.annodb.connection import session_scope
    from src.annodb.models import TaxonNode

    if not _db_ok() or not taxon_id or not scientific_name.strip():
        return False
    _ensure_db()
    with session_scope() as session:
        node = session.get(TaxonNode, taxon_id)
        if not node:
            return False
        node.scientific_name = scientific_name.strip()
        return True


def resolve_video_path(rel_path: str, repo: Path | None = None) -> Optional[str]:
    root = repo or _APP_ROOT
    name = Path(rel_path).name
    for p in (
        root / rel_path,
        root / name,
        root / "fish-vision" / "data" / "media" / name,
    ):
        if p.is_file():
            return str(p.resolve())
    return None


def dump_session_json(media_id: str) -> dict[str, Any]:
    if not _db_ok():
        return {}
    _ensure_db()
    detail = get_session_detail(media_id) or {}
    metadata = get_session_metadata(media_id) or {}
    annotations = list_annotations(media_id=media_id, limit=10000)
    grazing = list_grazing_for_media(media_id)
    return _json_safe({
        "media_id": media_id,
        "metadata": metadata,
        "stats": detail,
        "annotations": annotations,
        "grazing_intervals": grazing,
    })


def dump_filtered_export(
    *,
    project: str = REGISTRY_PROJECT,
    site: Optional[str] = None,
    species_id: Optional[str] = None,
    date_from: Optional[str] = None,
    date_to: Optional[str] = None,
) -> dict[str, Any]:
    sessions = list_sessions(
        project=project,
        site=site,
        species_id=species_id,
        date_from=date_from,
        date_to=date_to,
    )
    media_ids = [s["media_id"] for s in sessions if s.get("media_id")]
    observations = list_annotations(
        project=project,
        species_id=species_id,
        site=site,
        limit=10000,
    )
    if media_ids:
        allowed = set(media_ids)
        observations = [o for o in observations if o.get("media_id") in allowed]
    species = list_species_summary(project=project, site=site)
    if species_id:
        species = [s for s in species if s.get("taxon_node_id") == species_id]
    return _json_safe({
        "filters": {
            "project": project,
            "site": site,
            "species_id": species_id,
            "date_from": date_from,
            "date_to": date_to,
        },
        "session_count": len(sessions),
        "observation_count": len(observations),
        "sessions": sessions,
        "species_summary": species,
        "observations": observations,
    })


def get_frame_abundance(media_id: str, frame_index: int) -> dict[str, Any]:
    from sqlalchemy import select
    from src.annodb.connection import session_scope
    from src.annodb.models import FrameAbundance

    if not _db_ok() or not media_id:
        return {
            "exists": False,
            "ai_count": 0,
            "manual_count": None,
            "validated": False,
            "effective_count": 0,
        }
    _ensure_db()
    with session_scope() as session:
        row = session.scalar(
            select(FrameAbundance).where(
                FrameAbundance.media_id == media_id,
                FrameAbundance.frame_index == frame_index,
            )
        )
        if not row:
            return {
                "exists": False,
                "ai_count": 0,
                "manual_count": None,
                "validated": False,
                "effective_count": 0,
            }
        effective = row.manual_count if row.manual_count is not None else row.ai_count
        return {
            "exists": True,
            "ai_count": int(row.ai_count or 0),
            "manual_count": row.manual_count,
            "validated": bool(row.validated),
            "effective_count": int(effective or 0),
            "frame_ref": getattr(row, "frame_ref", None),
        }


def upsert_frame_abundance_ai(
    media_id: str,
    frame_index: int,
    ai_count: int,
    *,
    frame_ref: str | None = None,
) -> dict[str, Any]:
    """Comptage IA d'une frame. `frame_index` = index ABSOLU du fichier source."""
    from datetime import datetime

    from sqlalchemy import select
    from src.annodb.connection import session_scope
    from src.annodb.frame_ref import FRAME_REF_ABSOLUTE
    from src.annodb.models import FrameAbundance

    if not _db_ok() or not media_id:
        return get_frame_abundance(media_id, frame_index)
    _ensure_db()
    with session_scope() as session:
        row = session.scalar(
            select(FrameAbundance).where(
                FrameAbundance.media_id == media_id,
                FrameAbundance.frame_index == frame_index,
            )
        )
        if row is None:
            row = FrameAbundance(
                media_id=media_id,
                frame_index=frame_index,
                frame_ref=frame_ref or FRAME_REF_ABSOLUTE,
                ai_count=max(0, int(ai_count)),
                validated=False,
            )
            session.add(row)
        else:
            row.ai_count = max(0, int(ai_count))
            row.updated_at = datetime.utcnow()
        session.flush()
        effective = row.manual_count if row.manual_count is not None else row.ai_count
        return {
            "ai_count": int(row.ai_count or 0),
            "manual_count": row.manual_count,
            "validated": bool(row.validated),
            "effective_count": int(effective or 0),
            "frame_ref": getattr(row, "frame_ref", None),
        }


def validate_frame_abundance(
    media_id: str,
    frame_index: int,
    manual_count: int,
    *,
    ai_count: int | None = None,
    frame_ref: str | None = None,
) -> dict[str, Any]:
    """Comptage valide par l'operateur. `frame_index` = index ABSOLU."""
    from datetime import datetime

    from sqlalchemy import select
    from src.annodb.connection import session_scope
    from src.annodb.frame_ref import FRAME_REF_ABSOLUTE
    from src.annodb.models import FrameAbundance

    if not _db_ok() or not media_id:
        return get_frame_abundance(media_id, frame_index)
    _ensure_db()
    with session_scope() as session:
        row = session.scalar(
            select(FrameAbundance).where(
                FrameAbundance.media_id == media_id,
                FrameAbundance.frame_index == frame_index,
            )
        )
        if row is None:
            row = FrameAbundance(
                media_id=media_id,
                frame_index=frame_index,
                frame_ref=frame_ref or FRAME_REF_ABSOLUTE,
                ai_count=max(0, int(ai_count or manual_count)),
                manual_count=max(0, int(manual_count)),
                validated=True,
            )
            session.add(row)
        else:
            if ai_count is not None:
                row.ai_count = max(0, int(ai_count))
            row.manual_count = max(0, int(manual_count))
            row.validated = True
            row.updated_at = datetime.utcnow()
        session.flush()
        effective = row.manual_count if row.manual_count is not None else row.ai_count
        return {
            "ai_count": int(row.ai_count or 0),
            "manual_count": row.manual_count,
            "validated": True,
            "effective_count": int(effective or 0),
            "frame_ref": getattr(row, "frame_ref", None),
        }


def count_validated_frames(media_id: str) -> int:
    from sqlalchemy import func, select
    from src.annodb.connection import session_scope
    from src.annodb.models import FrameAbundance

    if not _db_ok() or not media_id:
        return 0
    _ensure_db()
    with session_scope() as session:
        return session.scalar(
            select(func.count(FrameAbundance.id)).where(
                FrameAbundance.media_id == media_id,
                FrameAbundance.validated.is_(True),
            )
        ) or 0


def session_max_visible_fish(media_id: str) -> int | None:
    from sqlalchemy import func, select
    from src.annodb.connection import session_scope
    from src.annodb.models import FrameAbundance

    if not _db_ok() or not media_id:
        return None
    _ensure_db()
    with session_scope() as session:
        validated_max = session.scalar(
            select(
                func.max(
                    func.coalesce(FrameAbundance.manual_count, FrameAbundance.ai_count)
                )
            ).where(
                FrameAbundance.media_id == media_id,
                FrameAbundance.validated.is_(True),
            )
        )
        if validated_max is not None:
            return int(validated_max)
    # MaxN n'est jamais déduit des observations ou des pistes : ces lignes
    # peuvent revoir plusieurs fois le même individu. Sans frame explicitement
    # validée, l'indicateur écologique reste indisponible.
    return None
