"""Galerie Fishial few-shot — embeddings custom par espèce."""

from __future__ import annotations

import sys
import tempfile
import uuid
import json
import re
import shutil
from collections import Counter
from datetime import datetime
from pathlib import Path
from typing import Any, Optional

import numpy as np

_APP_ROOT = Path(__file__).resolve().parent
_FV_ROOT = _APP_ROOT / "annotations"
if str(_FV_ROOT) not in sys.path:
    sys.path.insert(0, str(_FV_ROOT))

_centroids_cache: dict[str, np.ndarray] | None = None
_taxon_names_cache: dict[str, str] | None = None
_centroids_cache_min_refs: int | None = None


def invalidate_cache() -> None:
    """Oublie immédiatement les centroïdes après une mutation de référence."""
    global _centroids_cache, _taxon_names_cache, _centroids_cache_min_refs
    _centroids_cache = None
    _taxon_names_cache = None
    _centroids_cache_min_refs = None


def _ensure_db():
    from fish_annotate import _ensure_db as _edb
    return _edb()


def gallery_has_entries() -> bool:
    return bool(rebuild_centroids())


def _embedding_vector(blob: bytes | None) -> np.ndarray | None:
    """Décode uniquement un embedding utilisable par un centroïde cosine."""
    from src.annodb.embeddings import normalize_embedding

    return normalize_embedding(blob)


def _canonical_dimension(session) -> int | None:
    from sqlalchemy import select
    from src.annodb.embeddings import canonical_embedding_dimension
    from src.annodb.models import TaxonReferenceEmbedding

    blobs = session.scalars(select(TaxonReferenceEmbedding.embedding)).all()
    return canonical_embedding_dimension(blobs)


def _minimum_reference_count() -> int:
    from src.annodb.app_settings import get_setting

    return max(1, int(get_setting("fishial_min_refs", 5)))


def active_reference_vectors(session) -> list[tuple[Any, np.ndarray]]:
    """Références réellement utilisables, selon les règles des centroïdes."""
    from sqlalchemy import select
    from src.annodb.identification import is_authoritative_identification
    from src.annodb.models import (
        SpatialAnnotation,
        TaxonReferenceEmbedding,
    )
    from src.annodb.embeddings import canonical_embedding_dimension

    candidates: list[tuple[Any, np.ndarray]] = []
    rows = session.scalars(select(TaxonReferenceEmbedding)).all()
    for row in rows:
        if row.spatial_annotation_id:
            ann = session.get(SpatialAnnotation, row.spatial_annotation_id)
            if (
                ann is None
                or not is_authoritative_identification(ann)
                or ann.taxon_node_id != row.taxon_node_id
            ):
                continue
        elif row.source not in ("validated", "manual", "cvat"):
            # Référence réellement legacy sans annotation vérifiable :
            # seules les origines historiquement humaines sont sûres.
            continue
        vec = _embedding_vector(row.embedding)
        if vec is not None:
            candidates.append((row, vec))

    dimension = canonical_embedding_dimension(vec for _row, vec in candidates)
    return [
        (row, vector) for row, vector in candidates
        if dimension is None or vector.size == dimension
    ]


def active_reference_counts(session) -> Counter[str]:
    return Counter(
        row.taxon_node_id for row, _vector in active_reference_vectors(session)
    )


def rebuild_centroids() -> dict[str, np.ndarray]:
    global _centroids_cache, _taxon_names_cache, _centroids_cache_min_refs
    from src.annodb.connection import session_scope
    from src.annodb.models import TaxonNode

    _ensure_db()
    min_refs = _minimum_reference_count()
    names: dict[str, str] = {}
    with session_scope() as session:
        candidates = active_reference_vectors(session)
        for row, _vector in candidates:
            if row.taxon_node_id not in names:
                node = session.get(TaxonNode, row.taxon_node_id)
                if node:
                    names[row.taxon_node_id] = node.scientific_name

    by_taxon: dict[str, list[np.ndarray]] = {}
    for row, vector in candidates:
        by_taxon.setdefault(row.taxon_node_id, []).append(vector)

    centroids: dict[str, np.ndarray] = {}
    for tax_id, vectors in by_taxon.items():
        if len(vectors) < min_refs:
            continue
        mat = np.stack(vectors, axis=0)
        c = mat.mean(axis=0)
        norm = np.linalg.norm(c)
        if norm > 1e-8:
            c = c / norm
        centroids[tax_id] = c.astype(np.float32)

    _centroids_cache = centroids
    _taxon_names_cache = names
    _centroids_cache_min_refs = min_refs
    return centroids


def _get_centroids() -> tuple[dict[str, np.ndarray], dict[str, str]]:
    global _centroids_cache, _taxon_names_cache, _centroids_cache_min_refs
    if (
        _centroids_cache is None
        or _centroids_cache_min_refs != _minimum_reference_count()
    ):
        rebuild_centroids()
    return _centroids_cache or {}, _taxon_names_cache or {}


def _cosine_best(query: np.ndarray, centroids: dict[str, np.ndarray]) -> tuple[Optional[str], float]:
    if not centroids:
        return None, 0.0
    q = query.astype(np.float32)
    norm = np.linalg.norm(q)
    if norm < 1e-8:
        return None, 0.0
    q = q / norm
    best_id: Optional[str] = None
    best_score = -1.0
    for tax_id, c in centroids.items():
        score = float(np.dot(q, c))
        if score > best_score:
            best_score = score
            best_id = tax_id
    return best_id, best_score


def classify_with_gallery(
    bgr,
    box: Optional[dict[str, Any]] = None,
) -> dict[str, Any]:
    from fishial_classify import classify_crop, embed_crop

    empty = {"species_name": None, "species_conf": 0.0, "species_top3": [], "gallery_match": False}
    vec = embed_crop(bgr, box)
    if vec is None:
        out = classify_crop(bgr, box)
        out["gallery_match"] = False
        return out

    centroids, names = _get_centroids()
    if not centroids:
        out = classify_crop(bgr, box)
        out["gallery_match"] = False
        return out

    from src.annodb.app_settings import get_setting

    threshold = float(get_setting("gallery_similarity_threshold", 0.55))
    tax_id, score = _cosine_best(vec, centroids)
    if tax_id and score >= threshold:
        name = names.get(tax_id, tax_id)
        return {
            "species_name": name,
            "species_conf": round(score, 3),
            "species_top3": [{"species_name": name, "species_conf": round(score, 3)}],
            "gallery_match": True,
            "taxon_node_id": tax_id,
        }

    out = classify_crop(bgr, box)
    out["gallery_match"] = False
    return out


def classify_boxes_with_gallery(bgr, boxes: list[dict[str, Any]]) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    for box in boxes:
        enriched = dict(box)
        try:
            enriched.update(classify_with_gallery(bgr, box))
        except Exception as exc:
            enriched["species_error"] = str(exc)
        out.append(enriched)
    return out


class FishialUnavailableError(RuntimeError):
    """L'embedder Fishial n'est pas installable/chargeable dans cet environnement."""


def _resolve_embedder():
    """Fonction d'embedding Fishial, ou erreur explicite.

    Un `ImportError` avalé donnait des boutons qui échouaient en silence :
    `taxon_reference_embeddings` est resté à 0 ligne depuis toujours.
    """
    try:
        from fishial_classify import embed_crop
    except ImportError as exc:
        raise FishialUnavailableError(
            "Classificateur Fishial indisponible dans cet environnement "
            f"({exc}) — installez-le pour construire la galerie de référence."
        ) from exc
    return embed_crop


def _export_frame_for(session, ann, media):
    """Unité exportable (index ABSOLU) correspondant à cette annotation.

    Les lignes historiques sont en index timeline : sans conversion, le crop
    de référence serait découpé dans une tout autre image du film.
    """
    from src.annodb import frame_ref as fref
    from src.annodb import sessions as sessions_mod
    from src.annodb.export_media import ExportFrame

    if media.media_type != "video":
        return ExportFrame(
            media_id=media.id, frame_index=0, media_type=media.media_type,
            frame_ref=fref.FRAME_REF_ABSOLUTE,
        )
    row_ref = fref.normalize(getattr(ann, "frame_ref", None))
    stored = int(ann.frame_index or 0)
    offset = sessions_mod.frame_offset_for_media(session, media.id)
    if offset is None:
        offset = fref.timeline_offset()
    absolute = fref.to_absolute(stored, row_ref, offset)
    if absolute is None:
        return None
    return ExportFrame(
        media_id=media.id, frame_index=absolute, media_type=media.media_type,
        frame_ref=row_ref, source_frame_index=stored,
    )


def _annotation_transform(session, ann, media, transform=None):
    """Retourne le transform compatible avec la géométrie, ou son rejet."""
    from src.annodb.models import Calibration
    from src.annodb.rectify import (
        IMAGE_SPACE_RAW,
        IMAGE_SPACE_RECTIFIED_LEFT,
        load_left_rectifier,
    )
    from src.annodb.sessions import find_session_for_media, pair_for_media
    from src.annodb.spatial import geometry_space, parse_geometry

    space = geometry_space(parse_geometry(ann), default=IMAGE_SPACE_RAW)
    capture = find_session_for_media(session, media.id)
    pair = pair_for_media(session, media.id)
    if pair is not None and pair.right_media_id == media.id:
        return None, "media_droit_non_pris_en_charge"
    if media.media_type != "video":
        if space != IMAGE_SPACE_RAW:
            return None, "photo_espace_rectifie_incompatible"
        return None, ""

    if space == IMAGE_SPACE_RAW:
        return None, ""
    if space != IMAGE_SPACE_RECTIFIED_LEFT:
        return None, "espace_geometrie_incompatible"
    if capture is None or pair is None or pair.left_media_id != media.id:
        return None, "media_gauche_session_requise"

    calibration_id = media.calibration_id or pair.calibration_id or capture.calibration_id
    calibration = session.get(Calibration, calibration_id) if calibration_id else None
    if calibration is None:
        return None, "calibration_annotation_absente"
    rectifier = transform or load_left_rectifier(profile=calibration.profile_name)
    if rectifier is None:
        return None, "calibration_indisponible"
    actual_sha = str(getattr(rectifier, "calibration_sha256", "") or "")
    if not actual_sha or actual_sha != str(calibration.sha256 or ""):
        return None, "calibration_incompatible"
    return rectifier, ""


def _materialize_annotation_crop(
    session, ann, media, tmp_root: Path, transform=None,
):
    """Reconstruit exactement le crop utilisé par la classification en direct."""
    from src.annodb.export_media import crop_from_geometry, materialize_frame_image
    from src.annodb.spatial import parse_geometry

    # Chemin normal pour les nouvelles annotations : aucune dépendance à la
    # vidéo source. Les lignes historiques continuent avec la reconstruction
    # ci-dessous et profitent donc d'une migration progressive à l'usage.
    stored = Path(str(getattr(ann, "crop_path", "") or ""))
    if stored.is_file():
        import cv2

        crop = cv2.imread(str(stored))
        if crop is not None and crop.size:
            return crop, "", _export_frame_for(session, ann, media)

    selected_transform, incompatibility = _annotation_transform(
        session, ann, media, transform,
    )
    if incompatibility:
        return None, incompatibility, None

    export_frame = _export_frame_for(session, ann, media)
    if export_frame is None:
        return None, "frame_inaccessible", None
    dest = tmp_root / f"{ann.id}.jpg"
    size = materialize_frame_image(
        session,
        export_frame,
        dest,
        transform=selected_transform,
    )
    if size is None:
        return None, "media_inaccessible", export_frame
    import cv2

    frame_bgr = cv2.imread(str(dest))
    if frame_bgr is None:
        return None, "image_illisible", export_frame
    img_h, img_w = frame_bgr.shape[:2]
    crop = crop_from_geometry(
        frame_bgr, parse_geometry(ann), ann.geom_type, img_w, img_h,
    )
    if crop is None or crop.size == 0:
        return None, "crop_invalide", export_frame
    return crop, "", export_frame


def _persist_materialized_crop(ann, crop) -> Path:
    """Conserve localement un crop historique reconstruit depuis son média."""
    import cv2
    from src.annodb.storage_config import media_dir

    ok, encoded = cv2.imencode(".jpg", crop, [cv2.IMWRITE_JPEG_QUALITY, 94])
    if not ok:
        raise OSError("Encodage JPEG du crop impossible")
    root = media_dir() / "fishial_crops" / str(ann.id)[:2]
    root.mkdir(parents=True, exist_ok=True)
    dest = root / f"{ann.id}.jpg"
    # Deux ajouts simultanés peuvent reconstruire le même cliché historique.
    # Chacun doit posséder son fichier temporaire avant le remplacement atomique.
    tmp = root / f".{ann.id}.{uuid.uuid4().hex}.tmp"
    try:
        tmp.write_bytes(encoded.tobytes())
        tmp.replace(dest)
    finally:
        tmp.unlink(missing_ok=True)
    ann.crop_path = str(dest)
    return dest


def _session_reference_context(
    session,
    selected_annotation_id: str,
    *,
    capture_session_id: str | None = None,
    fallback_media_id: str | None = None,
) -> dict[str, Any]:
    """Résout l'espèce et le périmètre CaptureSession, avec repli média explicite."""
    from sqlalchemy import select
    from src.annodb.identification import (
        authoritative_identification_clause,
        is_authoritative_identification,
    )
    from src.annodb.models import (
        CaptureSession,
        MediaAsset,
        SpatialAnnotation,
        TaxonNode,
        TaxonReferenceEmbedding,
    )
    from src.annodb.sessions import find_session_for_media, session_media_ids

    ann = session.get(SpatialAnnotation, selected_annotation_id)
    if ann is None:
        return {"ok": False, "error": "Observation sélectionnée introuvable"}
    node = session.get(TaxonNode, ann.taxon_node_id) if ann.taxon_node_id else None
    if not is_authoritative_identification(ann):
        return {
            "ok": False,
            "error": "Observation non relue : validez humainement une espèce avant l'enrichissement Fishial",
            "taxon_rank": node.rank if node else "",
        }
    if node is None or node.rank != "species":
        return {
            "ok": False,
            "error": "Taxon partiel : seule une identification validée au rang espèce est promouvable",
            "taxon_rank": node.rank if node else "",
        }

    capture = session.get(CaptureSession, capture_session_id) if capture_session_id else None
    if capture is None:
        capture = find_session_for_media(session, ann.media_id)
    if capture is not None:
        media_ids = session_media_ids(session, capture)
        scope_kind = "session"
        scope_id = capture.id
        scope_label = f"Session « {capture.name} »"
    else:
        media_id = fallback_media_id or ann.media_id
        media_ids = [media_id]
        media = session.get(MediaAsset, media_id)
        scope_kind = "media"
        scope_id = media_id
        scope_label = f"Média courant « {Path(media.rel_path).name} »" if media else "Média courant"

    anns = session.scalars(
        select(SpatialAnnotation).where(
            SpatialAnnotation.media_id.in_(media_ids),
            SpatialAnnotation.taxon_node_id == node.id,
            authoritative_identification_clause(SpatialAnnotation),
        ).order_by(SpatialAnnotation.id)
    ).all()
    ann_ids = [row.id for row in anns]
    all_existing_rows = session.scalars(
        select(TaxonReferenceEmbedding).where(
            TaxonReferenceEmbedding.spatial_annotation_id.in_(ann_ids),
            TaxonReferenceEmbedding.taxon_node_id == node.id,
        )
    ).all() if ann_ids else []
    canonical_dimension = _canonical_dimension(session)
    from src.annodb.embeddings import normalize_embedding

    existing_rows = [
        row for row in all_existing_rows
        if normalize_embedding(
            row.embedding, expected_dimension=canonical_dimension,
        ) is not None
    ]
    invalid_existing_rows = [
        row for row in all_existing_rows
        if normalize_embedding(
            row.embedding, expected_dimension=canonical_dimension,
        ) is None
    ]
    existing_annotation_ids = {row.spatial_annotation_id for row in existing_rows}
    return {
        "ok": True,
        "selected_annotation": ann,
        "taxon_id": node.id,
        "taxon_name": node.scientific_name,
        "taxon_rank": node.rank,
        "is_provisional": bool(node.is_provisional),
        "scope_kind": scope_kind,
        "scope_id": scope_id,
        "scope_label": scope_label,
        "media_ids": media_ids,
        "annotations": anns,
        "existing_rows": existing_rows,
        "invalid_existing_rows": invalid_existing_rows,
        "existing_annotation_ids": existing_annotation_ids,
        "existing": len(existing_annotation_ids),
    }


def inspect_session_references(
    selected_annotation_id: str,
    *,
    capture_session_id: str | None = None,
    fallback_media_id: str | None = None,
) -> dict[str, Any]:
    """Compte candidats, crops réels, références actives et motifs de rejet."""
    from src.annodb.connection import session_scope
    from src.annodb.models import MediaAsset

    _ensure_db()
    rejected: Counter[str] = Counter()
    with session_scope() as session, tempfile.TemporaryDirectory(
        prefix="fishial_session_preview_"
    ) as tmp_dir:
        context = _session_reference_context(
            session,
            selected_annotation_id,
            capture_session_id=capture_session_id,
            fallback_media_id=fallback_media_id,
        )
        if not context.get("ok"):
            return context
        exploitable = 0
        for ann in context["annotations"]:
            if ann.id in context["existing_annotation_ids"]:
                continue
            media = session.get(MediaAsset, ann.media_id)
            if media is None:
                rejected["media_absent"] += 1
                continue
            crop, reason, _export_frame = _materialize_annotation_crop(
                session, ann, media, Path(tmp_dir),
            )
            if crop is None:
                rejected[reason] += 1
            else:
                exploitable += 1
        eligible = exploitable + context["existing"] > 0
        return {
            "ok": True,
            "eligible": eligible,
            "selected_annotation_id": selected_annotation_id,
            "taxon_id": context["taxon_id"],
            "taxon_name": context["taxon_name"],
            "taxon_rank": context["taxon_rank"],
            "is_provisional": context["is_provisional"],
            "scope_kind": context["scope_kind"],
            "scope_id": context["scope_id"],
            "scope_label": context["scope_label"],
            "candidates": len(context["annotations"]),
            "exploitable": exploitable,
            "existing": context["existing"],
            "rejected": sum(rejected.values()),
            "rejection_reasons": dict(rejected),
        }


def build_session_references(
    selected_annotation_id: str,
    *,
    capture_session_id: str | None = None,
    fallback_media_id: str | None = None,
    embedder: Any = None,
) -> dict[str, Any]:
    """Ajoute en lot les références de l'espèce dans la session courante.

    L'unicité active est imposée par un index SQL partiel. L'insertion atomique
    transforme un conflit concurrent en « déjà présente », jamais en doublon.
    """
    from sqlalchemy import delete
    from sqlalchemy.dialects.sqlite import insert as sqlite_insert
    from src.annodb.connection import session_scope
    from src.annodb.models import MediaAsset, TaxonReferenceEmbedding

    embed = embedder
    _ensure_db()
    rejected: Counter[str] = Counter()
    added = 0
    already_present = 0
    candidates = 0
    exploitable = 0
    result_context: dict[str, Any] = {}
    with session_scope() as session, tempfile.TemporaryDirectory(
        prefix="fishial_session_build_"
    ) as tmp_dir:
        context = _session_reference_context(
            session,
            selected_annotation_id,
            capture_session_id=capture_session_id,
            fallback_media_id=fallback_media_id,
        )
        if not context.get("ok"):
            return context
        result_context = context
        candidates = len(context["annotations"])
        existing_ids = set(context["existing_annotation_ids"])
        for ref in context["invalid_existing_rows"]:
            session.delete(ref)
        session.flush()
        canonical_dimension = _canonical_dimension(session)
        from src.annodb.embeddings import normalize_embedding

        for ann in context["annotations"]:
            if ann.id in existing_ids:
                already_present += 1
                continue
            media = session.get(MediaAsset, ann.media_id)
            if media is None:
                rejected["media_absent"] += 1
                continue
            crop, reason, export_frame = _materialize_annotation_crop(
                session, ann, media, Path(tmp_dir),
            )
            if crop is None:
                rejected[reason] += 1
                continue
            if not Path(str(getattr(ann, "crop_path", "") or "")).is_file():
                _persist_materialized_crop(ann, crop)
            exploitable += 1
            if embed is None:
                embed = _resolve_embedder()
            vec = normalize_embedding(
                embed(crop), expected_dimension=canonical_dimension,
            )
            if vec is None:
                rejected["embedding_invalide"] += 1
                continue
            if canonical_dimension is None:
                canonical_dimension = int(vec.size)
            session.execute(
                delete(TaxonReferenceEmbedding).where(
                    TaxonReferenceEmbedding.spatial_annotation_id == ann.id,
                    TaxonReferenceEmbedding.taxon_node_id != context["taxon_id"],
                )
            )
            session.flush()
            embedding = vec.tobytes()
            inserted = session.execute(
                sqlite_insert(TaxonReferenceEmbedding)
                .values(
                    id=str(uuid.uuid4()),
                    taxon_node_id=context["taxon_id"],
                    spatial_annotation_id=ann.id,
                    media_id=ann.media_id,
                    frame_index=export_frame.frame_index,
                    embedding=embedding,
                    source="validated",
                )
                .on_conflict_do_nothing(
                    index_elements=["spatial_annotation_id"],
                    index_where=TaxonReferenceEmbedding.spatial_annotation_id.is_not(None),
                )
            ).rowcount
            if not inserted:
                already_present += 1
                continue
            existing_ids.add(ann.id)
            added += 1
    rebuild_centroids()
    active = added + already_present
    return {
        "ok": active > 0,
        "eligible": active > 0,
        "error": "Aucune référence Fishial active : tous les crops ou embeddings ont été rejetés" if active == 0 else "",
        "selected_annotation_id": selected_annotation_id,
        "taxon_id": result_context["taxon_id"],
        "taxon_name": result_context["taxon_name"],
        "taxon_rank": result_context["taxon_rank"],
        "is_provisional": result_context["is_provisional"],
        "scope_kind": result_context["scope_kind"],
        "scope_id": result_context["scope_id"],
        "scope_label": result_context["scope_label"],
        "candidates": candidates,
        "exploitable": exploitable,
        "added": added,
        "existing": already_present,
        "rejected": sum(rejected.values()),
        "rejection_reasons": dict(rejected),
    }


def promote_session_references(*args, **kwargs) -> dict[str, Any]:
    """Façade UI : transforme l'absence d'embedder en état d'erreur explicite."""
    try:
        return build_session_references(*args, **kwargs)
    except FishialUnavailableError as exc:
        return {"ok": False, "error": str(exc)}


def build_references_for_taxon(
    taxon_id: str,
    *,
    project: str = "madagascar_measure",
    embedder: Any = None,
) -> int:
    """Construit les embeddings de référence d'un taxon depuis ses clichés validés.

    Les trois appels de cette fonction étaient faits avec de **mauvaises
    signatures** (`materialize_frame_image(session, media, frame_index)`,
    `crop_from_geometry(bgr, geom)`) : la chaîne levait un TypeError avalé plus
    haut, et la galerie n'a jamais contenu la moindre ligne.

    Chaque crop respecte l'espace déclaré par sa géométrie : une boîte `raw`
    est découpée dans la frame brute, tandis qu'une boîte `rectified-left`
    exige le média gauche et la calibration SHA compatible. Les médias droits
    et les combinaisons incompatibles sont rejetés explicitement.

    `embedder` permet d'injecter la fonction d'embedding (tests) ; par défaut
    c'est celle de Fishial, dont l'absence lève `FishialUnavailableError`.
    """
    return int(_build_project_references_report(
        taxon_id, project=project, embedder=embedder,
    )["added"])


def _build_project_references_report(
    taxon_id: str,
    *,
    project: str = "madagascar_measure",
    embedder: Any = None,
) -> dict[str, Any]:
    """Version détaillée et atomique de la promotion globale du projet."""
    from sqlalchemy import delete, select
    from sqlalchemy.dialects.sqlite import insert as sqlite_insert
    from src.annodb.connection import session_scope
    from src.annodb.identification import authoritative_identification_clause
    from src.annodb.models import (
        MediaAsset,
        Project,
        SpatialAnnotation,
        TaxonNode,
        TaxonReferenceEmbedding,
    )

    _ensure_db()
    with session_scope() as session:
        node = session.get(TaxonNode, taxon_id)
        if node is None or node.rank != "species":
            return {
                "ok": False,
                "error": "Promotion Fishial refusée : un taxon de rang espèce est requis",
                "candidates": 0, "exploitable": 0,
                "added": 0, "existing": 0, "rejected": 0,
                "rejection_reasons": {},
            }
    embed = embedder

    added = 0
    existing = 0
    rejected: Counter[str] = Counter()
    candidates = 0
    exploitable = 0
    with session_scope() as session, tempfile.TemporaryDirectory(
        prefix="fishial_gallery_"
    ) as tmp_dir:
        tmp_root = Path(tmp_dir)
        proj = session.scalar(select(Project).where(Project.name == project))
        if not proj:
            return {
                "ok": False,
                "error": "Aucune référence Fishial active dans ce projet",
                "candidates": 0, "exploitable": 0,
                "added": 0, "existing": 0, "rejected": 0,
                "rejection_reasons": {},
            }
        anns = session.scalars(
            select(SpatialAnnotation)
            .join(MediaAsset, SpatialAnnotation.media_id == MediaAsset.id)
            .where(
                MediaAsset.project_id == proj.id,
                SpatialAnnotation.taxon_node_id == taxon_id,
                authoritative_identification_clause(SpatialAnnotation),
            )
        ).all()
        candidates = len(anns)
        canonical_dimension = _canonical_dimension(session)
        from src.annodb.embeddings import normalize_embedding

        for ann in anns:
            linked = session.scalars(
                select(TaxonReferenceEmbedding).where(
                    TaxonReferenceEmbedding.spatial_annotation_id == ann.id,
                )
            ).all()
            usable = next((
                row for row in linked
                if row.taxon_node_id == taxon_id
                and normalize_embedding(
                    row.embedding, expected_dimension=canonical_dimension,
                ) is not None
            ), None)
            if usable is not None:
                existing += 1
                continue
            media = session.get(MediaAsset, ann.media_id)
            if not media:
                rejected["media_absent"] += 1
                continue
            crop, reason, export_frame = _materialize_annotation_crop(
                session, ann, media, tmp_root,
            )
            if crop is None:
                rejected[reason] += 1
                continue
            if not Path(str(getattr(ann, "crop_path", "") or "")).is_file():
                _persist_materialized_crop(ann, crop)
            exploitable += 1
            session.execute(
                delete(TaxonReferenceEmbedding).where(
                    TaxonReferenceEmbedding.spatial_annotation_id == ann.id,
                )
            )
            session.flush()
            if embed is None:
                embed = _resolve_embedder()
            vec = normalize_embedding(
                embed(crop), expected_dimension=canonical_dimension,
            )
            if vec is None:
                rejected["embedding_invalide"] += 1
                continue
            if canonical_dimension is None:
                canonical_dimension = int(vec.size)
            inserted = session.execute(
                sqlite_insert(TaxonReferenceEmbedding)
                .values(
                    id=str(uuid.uuid4()),
                    taxon_node_id=taxon_id,
                    spatial_annotation_id=ann.id,
                    media_id=ann.media_id,
                    frame_index=export_frame.frame_index,
                    embedding=vec.tobytes(),
                    source="validated",
                )
                .on_conflict_do_nothing(
                    index_elements=["spatial_annotation_id"],
                    index_where=TaxonReferenceEmbedding.spatial_annotation_id.is_not(None),
                )
            ).rowcount
            if inserted:
                added += 1
            else:
                existing += 1
    rebuild_centroids()
    active = added + existing
    return {
        "ok": active > 0,
        "error": "Aucune référence Fishial active : tous les crops ou embeddings ont été rejetés" if active == 0 else "",
        "candidates": candidates,
        "exploitable": exploitable,
        "added": added,
        "existing": existing,
        "rejected": sum(rejected.values()),
        "rejection_reasons": dict(rejected),
    }


def promote_taxon_to_gallery(taxon_id: str) -> dict[str, Any]:
    from fish_db_stats import species_promotion_eligibility

    eligibility = species_promotion_eligibility(taxon_id)
    if not eligibility["eligible"]:
        return {"ok": False, "error": eligibility["reason"]}
    try:
        result = _build_project_references_report(taxon_id)
    except FishialUnavailableError as exc:
        # Message clair plutôt qu'un « 0 embedding » qui laisse croire à un
        # manque de clichés.
        return {"ok": False, "error": str(exc)}
    if int(result.get("added") or 0) == 0 and int(result.get("existing") or 0) == 0:
        return {
            **result,
            "ok": False,
            "error": (
                "Aucun embedding calculé — vérifiez que les vidéos sources sont "
                "accessibles et que les clichés portent bien une boîte."
            ),
        }
    return {
        **result,
        "ok": True,
        "references_added": int(result.get("added") or 0),
    }


def promote_all_new_references(*, progress=None) -> dict[str, Any]:
    """Ajoute les images en attente de toutes les espèces éligibles du projet.

    Chaque espèce utilise la même promotion que son bouton individuel, avec
    contrôle du seuil et unicité par observation. Une erreur sur une espèce
    n'annule pas les ajouts déjà validés pour les autres.
    """
    from fish_db_stats import list_species_summary

    rows = list_species_summary()
    pending = [row for row in rows if int(row.get("pending_reference_count") or 0) > 0]
    eligible = [row for row in pending if row.get("promotion_eligible")]
    report = {
        "ok": True, "added": 0, "existing": 0, "rejected": 0,
        "species_total": len(eligible), "species_processed": 0,
        "species_below_threshold": len(pending) - len(eligible),
        "errors": [], "rejection_reasons": {},
    }
    rejected_reasons: Counter[str] = Counter()
    for index, row in enumerate(eligible):
        name = row["scientific_name"]
        if progress is not None:
            progress(index + 1, len(eligible), name)
        try:
            result = promote_taxon_to_gallery(row["taxon_node_id"])
        except Exception as exc:
            result = {"ok": False, "error": str(exc)}
        for key in ("added", "existing", "rejected"):
            report[key] += int(result.get(key) or 0)
        rejected_reasons.update(result.get("rejection_reasons") or {})
        report["species_processed"] += 1
        if not result.get("ok"):
            report["errors"].append({
                "taxon_id": row["taxon_node_id"], "species": name,
                "message": result.get("error") or "Ajout impossible",
            })
    report["rejection_reasons"] = dict(rejected_reasons)
    if report["errors"]:
        report["ok"] = False
        report["error"] = " ; ".join(
            f"{item['species']} : {item['message']}" for item in report["errors"]
        )
    return report


def export_local_library(output_root: str | Path) -> dict[str, Any]:
    """Exporte tous les crops validés, rangés par espèce, avec manifeste.

    Les copies locales sont utilisées en priorité. Une ancienne annotation sans
    copie est reconstruite depuis sa vidéo quand celle-ci est encore présente.
    """
    from sqlalchemy import select
    from src.annodb.connection import session_scope
    from src.annodb.identification import authoritative_identification_clause
    from src.annodb.models import MediaAsset, SpatialAnnotation, TaxonNode

    _ensure_db()
    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    root = Path(output_root) / f"fishial_library_{stamp}_{uuid.uuid4().hex[:6]}"
    root.mkdir(parents=True, exist_ok=False)
    rows_out: list[dict[str, Any]] = []
    skipped: Counter[str] = Counter()

    with session_scope() as session, tempfile.TemporaryDirectory(
        prefix="fishial_library_export_"
    ) as tmp_dir:
        references = active_reference_counts(session)
        rows = session.execute(
            select(SpatialAnnotation, MediaAsset, TaxonNode)
            .join(MediaAsset, SpatialAnnotation.media_id == MediaAsset.id)
            .join(TaxonNode, SpatialAnnotation.taxon_node_id == TaxonNode.id)
            .where(
                TaxonNode.rank == "species",
                authoritative_identification_clause(SpatialAnnotation),
            )
            .order_by(TaxonNode.scientific_name, SpatialAnnotation.id)
        ).all()
        by_species: Counter[str] = Counter()
        for ann, media, node in rows:
            safe_name = re.sub(
                r"[^A-Za-z0-9._-]+", "_", node.scientific_name.strip()
            ).strip("_.-") or node.id
            species_dir = root / safe_name
            species_dir.mkdir(parents=True, exist_ok=True)
            source = Path(str(getattr(ann, "crop_path", "") or ""))
            if source.is_file():
                shutil.copy2(source, species_dir / f"{ann.id}.jpg")
            else:
                crop, reason, _frame = _materialize_annotation_crop(
                    session, ann, media, Path(tmp_dir),
                )
                if crop is None:
                    skipped[reason or "crop_indisponible"] += 1
                    continue
                import cv2

                if not cv2.imwrite(str(species_dir / f"{ann.id}.jpg"), crop):
                    skipped["ecriture_impossible"] += 1
                    continue
                _persist_materialized_crop(ann, crop)
            by_species[node.id] += 1
            rows_out.append({
                "annotation_id": ann.id,
                "taxon_id": node.id,
                "species": node.scientific_name,
                "file": f"{safe_name}/{ann.id}.jpg",
                "fishial_reference": int(references.get(node.id, 0)) > 0,
            })

        # Une bibliothèque reçue d'un autre PC possède des références et des
        # clichés, sans observations dans le registre de ce poste.
        from src.annodb.fishial_transfer import reference_crop_path

        for reference, _vector in active_reference_vectors(session):
            if reference.spatial_annotation_id:
                continue
            source = reference_crop_path(session, reference)
            node = session.get(TaxonNode, reference.taxon_node_id)
            if source is None or node is None or node.rank != "species":
                continue
            safe_name = re.sub(r"[^A-Za-z0-9._-]+", "_", node.scientific_name.strip()).strip("_.-") or node.id
            species_dir = root / safe_name
            species_dir.mkdir(parents=True, exist_ok=True)
            filename = f"reference_{uuid.uuid5(uuid.NAMESPACE_URL, reference.id).hex}.jpg"
            shutil.copy2(source, species_dir / filename)
            by_species[node.id] += 1
            rows_out.append({"annotation_id": None, "reference_id": reference.id,
                             "taxon_id": node.id, "species": node.scientific_name,
                             "file": f"{safe_name}/{filename}", "fishial_reference": True})

        species = []
        for taxon_id, count in by_species.most_common():
            node = session.get(TaxonNode, taxon_id)
            species.append({
                "taxon_id": taxon_id,
                "species": node.scientific_name if node else taxon_id,
                "crop_count": int(count),
                "reference_count": int(references.get(taxon_id, 0)),
            })

    manifest = {
        "format": "AquaMeasure Fishial local library v1",
        "created_at": datetime.now().isoformat(timespec="seconds"),
        "crop_count": len(rows_out),
        "species_count": len(species),
        "species": species,
        "crops": rows_out,
        "skipped": {"count": sum(skipped.values()), "by_reason": dict(skipped)},
    }
    (root / "fishial_library.json").write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8",
    )
    return {
        "output_path": str(root.resolve()),
        "crop_count": len(rows_out),
        "species_count": len(species),
        "skipped_count": sum(skipped.values()),
    }
