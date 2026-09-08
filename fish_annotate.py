"""Annotation poissons — taxonomie hiérarchique et persistance DB."""

from __future__ import annotations

import json
import os
import sys
from datetime import datetime
from pathlib import Path
from typing import Any, Optional

_APP_ROOT = Path(__file__).resolve().parent
_FV_ROOT = _APP_ROOT / "fish-vision"
if str(_FV_ROOT) not in sys.path:
    sys.path.insert(0, str(_FV_ROOT))

_LABEL_MAP_CACHE: dict[str, str] | None = None

# Rang laisse « non identifie » par l'observateur : le terrain ne permet pas
# toujours de descendre jusqu'a l'espece, et un champ vide ne dit pas si le
# rang est inconnu ou simplement pas encore saisi.
NA_LABEL = "NA"
NA_TOKENS = frozenset({
    "na", "n/a", "n.a.", "nd", "n.d.",
    "non identifie", "non identifié",
    "indetermine", "indéterminé",
    "inconnu",
})
UNIDENTIFIED_TAXON_ID = "taxon-fish-generic"


def is_na_label(text: str) -> bool:
    """Vrai si la saisie UI signifie « non identifie a ce rang »."""
    return (text or "").strip().casefold() in NA_TOKENS


def unidentified_taxon_id() -> Optional[str]:
    """Noeud « poisson generique » — rattachement des observations tout en NA."""
    from src.annodb.connection import session_scope
    from src.annodb.models import TaxonNode

    with session_scope(_ensure_db()) as session:
        node = session.get(TaxonNode, UNIDENTIFIED_TAXON_ID)
        return node.id if node else None


def _db_available() -> bool:
    try:
        from src.annodb.connection import get_db_path  # noqa: WPS433
        return get_db_path().exists()
    except Exception:
        return False


def is_available() -> bool:
    return _db_available()


def ensure_database() -> bool:
    """Cree la base si elle n'existe pas encore ; vrai si elle est utilisable.

    `is_available()` se contente de constater : au tout premier lancement, le
    fichier n'existe pas et tout appelant qui s'y fie se croit sans base. Cette
    fonction, elle, la cree — c'est ce que fait deja `_ensure_db()` en interne
    avant chaque ecriture.
    """
    try:
        return _ensure_db().exists()
    except Exception:
        return False


def _load_label_map() -> dict[str, str]:
    global _LABEL_MAP_CACHE
    if _LABEL_MAP_CACHE is not None:
        return _LABEL_MAP_CACHE
    import yaml

    for rel in (
        "configs/public_family_label_map.yaml",
        "configs/cvat_label_map.yaml",
    ):
        path = _FV_ROOT / rel
        if path.is_file():
            data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
            mapping = data.get("mapping", data)
            _LABEL_MAP_CACHE = {str(k): str(v) for k, v in mapping.items()}
            return _LABEL_MAP_CACHE
    _LABEL_MAP_CACHE = {}
    return _LABEL_MAP_CACHE


def get_taxonomy_tree() -> list[dict[str, Any]]:
    """Arbre taxonomique pour le picker UI."""
    from src.annodb.connection import get_db_path, init_db, session_scope
    from src.annodb.taxonomy import taxonomy_tree_dict

    db_path = get_db_path()
    if not db_path.exists():
        init_db(db_path)
    with session_scope(db_path) as session:
        return taxonomy_tree_dict(session)


def list_taxon_nodes(flat: bool = True) -> list[dict[str, Any]]:
    """Liste aplatie des noeuds taxonomiques."""
    from src.annodb.connection import get_db_path, init_db, session_scope
    from src.annodb.models import TaxonNode
    from sqlalchemy import select

    db_path = get_db_path()
    if not db_path.exists():
        init_db(db_path)
    with session_scope(db_path) as session:
        nodes = session.scalars(select(TaxonNode).order_by(TaxonNode.rank, TaxonNode.scientific_name))
        return [
            {
                "id": n.id,
                "parent_id": n.parent_id,
                "rank": n.rank,
                "scientific_name": n.scientific_name,
                "common_name": n.common_name,
                "is_provisional": bool(n.is_provisional),
            }
            for n in nodes
        ]


def propose_taxon_from_detection(cls_name: str) -> Optional[str]:
    """Map nom classe YOLO -> taxon_node_id."""
    name = (cls_name or "").strip()
    if not name or name in ("fish", "poisson"):
        return None
    mapping = _load_label_map()
    if name in mapping:
        return mapping[name]
    from src.annodb.connection import get_db_path, init_db, session_scope
    from src.annodb.taxonomy import get_node_by_name

    db_path = get_db_path()
    if not db_path.exists():
        init_db(db_path)
    with session_scope(db_path) as session:
        node = get_node_by_name(session, name)
        return node.id if node else None


def parse_taxon_label(text: str) -> str:
    """Extrait le nom scientifique d'un libelle combo « Vernaculaire (Scientifique) »."""
    from src.annodb.seed_fishial import normalize_scientific_name

    text = (text or "").strip()
    if not text or text == "—" or is_na_label(text):
        return ""
    if " (" in text and text.endswith(")"):
        sci = text.rsplit(" (", 1)[1][:-1].strip()
        if sci:
            return normalize_scientific_name(sci)
    return normalize_scientific_name(text)


def ensure_species_known(
    sci_name: str,
    *,
    genus_name: Optional[str] = None,
    family_name: Optional[str] = None,
) -> Optional[str]:
    """Ajoute l'espece en taxonomie si absente ; retourne taxon_node_id."""
    sci_name = (sci_name or "").strip()
    if not sci_name:
        return None
    from src.annodb.connection import session_scope
    from src.annodb.seed_fishial import ensure_binomial_species, sync_supplemental_species

    with session_scope(_ensure_db()) as session:
        sync_supplemental_species(session)
        try:
            return ensure_binomial_species(
                session,
                sci_name,
                genus_name=genus_name,
                family_name=family_name,
                source="user",
            )
        except ValueError:
            return None


def _hierarchy_ids(session, node_id: str) -> dict[str, Optional[str]]:
    out: dict[str, Optional[str]] = {
        "family_id": None,
        "genus_id": None,
        "species_id": None,
    }
    for node in _ancestors_chain(session, node_id):
        if node.rank == "family":
            out["family_id"] = node.id
        elif node.rank == "genus":
            out["genus_id"] = node.id
        elif node.rank == "species":
            out["species_id"] = node.id
    return out


def ensure_taxon_from_ui(
    *,
    family_id: Optional[str] = None,
    family_text: str = "",
    genus_id: Optional[str] = None,
    genus_text: str = "",
    species_id: Optional[str] = None,
    species_text: str = "",
) -> dict[str, Optional[str]]:
    """Resout ou cree famille / genre / espece depuis la saisie UI."""
    from src.annodb.connection import session_scope
    from src.annodb.models import TaxonNode
    from src.annodb.seed_fishial import (
        ensure_binomial_species,
        ensure_family_name,
        ensure_genus_name,
        sync_supplemental_species,
    )

    family_name = parse_taxon_label(family_text)
    genus_name = parse_taxon_label(genus_text)
    species_name = parse_taxon_label(species_text)

    with session_scope(_ensure_db()) as session:
        sync_supplemental_species(session)

        if species_id and session.get(TaxonNode, species_id):
            sp_id = species_id
        elif species_name:
            parts = species_name.split()
            if len(parts) >= 2 and not genus_name:
                genus_name = parts[0]
            sp_id = ensure_binomial_species(
                session,
                species_name,
                genus_name=genus_name or None,
                family_name=family_name or None,
                source="user",
            )
        else:
            sp_id = None

        if sp_id:
            hier = _hierarchy_ids(session, sp_id)
            return {
                **hier,
                "taxon_node_id": sp_id,
            }

        if genus_id and session.get(TaxonNode, genus_id):
            gen_id = genus_id
        elif genus_name:
            fam_id = family_id
            if not fam_id and family_name:
                fam_id = ensure_family_name(session, family_name)
            gen_id = ensure_genus_name(
                session,
                genus_name,
                family_name=family_name or None,
                family_id=fam_id,
            )
        else:
            gen_id = None

        if gen_id:
            hier = _hierarchy_ids(session, gen_id)
            return {
                **hier,
                "taxon_node_id": gen_id,
            }

        if family_id and session.get(TaxonNode, family_id):
            fam_id = family_id
        elif family_name:
            fam_id = ensure_family_name(session, family_name)
        else:
            fam_id = family_id

        return {
            "family_id": fam_id,
            "genus_id": None,
            "species_id": None,
            "taxon_node_id": fam_id,
        }


def propose_taxon_from_box(box: dict) -> Optional[str]:
    """Propose le taxon le plus fin depuis une détection (Fishial espèce ou YOLO famille)."""
    ensure_fishial_taxonomy_if_needed()
    species = (box.get("species_name") or "").strip()
    cls_name = (box.get("cls_name") or "").strip()

    if species:
        sp_id = ensure_species_known(species)
        if sp_id:
            return sp_id
        tid = find_taxon_id(species, "species") or propose_taxon_from_detection(species)
        if tid:
            return tid
        parts = species.split()
        if len(parts) >= 2:
            tid = find_taxon_id(parts[0], "genus") or propose_taxon_from_detection(parts[0])
            if tid:
                return tid

    if cls_name and cls_name not in ("fish", "poisson"):
        tid = propose_taxon_from_detection(cls_name)
        if tid:
            return tid

    return propose_taxon_from_detection("fish")


def _resolve_taxon_for_annotation(session, taxon_node_id: Optional[str], bbox: dict) -> Optional[str]:
    """Garantit un taxon_node_id présent en base (évite FOREIGN KEY failed)."""
    from src.annodb.models import TaxonNode
    from src.annodb.seed_fishial import ensure_binomial_species, sync_supplemental_species
    from src.annodb.taxonomy import get_node_by_name

    candidates: list[Optional[str]] = [
        taxon_node_id,
        bbox.get("taxon_node_id"),
    ]
    for cand in candidates:
        if cand and session.get(TaxonNode, cand):
            return cand

    species = (bbox.get("species_name") or "").strip()
    if species:
        sync_supplemental_species(session)
        if len(species.split()) >= 2:
            try:
                return ensure_binomial_species(session, species, source="fishial")
            except ValueError:
                pass
        node = get_node_by_name(session, species)
        if node:
            return node.id

    cls_name = (bbox.get("cls_name") or "").strip()
    if cls_name and cls_name not in ("fish", "poisson"):
        mapped = propose_taxon_from_detection(cls_name)
        if mapped and session.get(TaxonNode, mapped):
            return mapped

    generic = session.get(TaxonNode, "taxon-fish-generic")
    return generic.id if generic else None


def _resolve_track_for_annotation(session, track_id: Optional[str]) -> Optional[str]:
    if not track_id:
        return None
    tid = str(track_id).strip()
    if not tid:
        return None
    from src.annodb.models import Track

    return tid if session.get(Track, tid) else None


REGISTRY_PROJECT = "madagascar_measure"


def _ensure_db():
    from src.annodb.connection import get_db_path, init_db
    db_path = get_db_path()
    if not db_path.exists():
        init_db(db_path)
    return db_path


def _heal_media_path(media, path: Path) -> None:
    """Repare un `rel_path` perime quand le fichier est retrouve par son sha256.

    Le chemin stocke peut pointer vers un fichier disparu (video archivee,
    renommee, dossier deplace) : la session ne sait alors plus rouvrir sa paire
    et les annotations deviennent inexportables. Le sha256 garantit qu'il
    s'agit du meme contenu, et on ne reecrit QUE si le chemin enregistre n'est
    plus lisible — jamais par-dessus un chemin qui marche.
    """
    from src.annodb.projects import resolve_media_path

    try:
        if resolve_media_path(media).is_file():
            return
    except OSError:
        pass
    media.rel_path = str(path)


def resolve_media_id(
    media_path: str,
    *,
    project: str = REGISTRY_PROJECT,
    create: bool = False,
) -> Optional[str]:
    """Resolve stable media_assets.id from a file path (sha256 / rel_path)."""
    from sqlalchemy import select
    from src.annodb.connection import session_scope
    from src.annodb.models import MediaAsset, Project
    from src.annodb.projects import get_or_create_project, register_media

    path = Path(media_path).resolve()
    if not path.is_file():
        return None

    _ensure_db()
    with session_scope() as session:
        proj = session.scalar(select(Project).where(Project.name == project))
        if not proj:
            if not create:
                return None
            proj = get_or_create_project(session, project)

        rel_candidates = {str(path), path.name}
        try:
            from src.annodb.projects import media_root
            rel_candidates.add(str(path.relative_to(media_root())))
        except ValueError:
            pass

        for rel in rel_candidates:
            row = session.scalar(
                select(MediaAsset).where(
                    MediaAsset.project_id == proj.id,
                    MediaAsset.rel_path == rel,
                )
            )
            if row:
                return row.id

        # Empreinte mise en cache : sans cela, plusieurs Go etaient relus a
        # chaque annotation et a chaque enregistrement de session.
        from src.annodb.projects import sha256_for_path

        sha = sha256_for_path(session, path, project_id=proj.id)
        by_hash = session.scalar(
            select(MediaAsset).where(
                MediaAsset.project_id == proj.id,
                MediaAsset.sha256 == sha,
            )
        )
        if by_hash:
            if create:
                _heal_media_path(by_hash, path)
            return by_hash.id

        if create:
            media_type = (
                "video"
                if path.suffix.lower() in {".mp4", ".avi", ".mov", ".mkv"}
                else "image"
            )
            media = register_media(
                session,
                project_id=proj.id,
                file_path=path,
                media_type=media_type,
                copy_into_store=False,
            )
            return media.id
    return None


# ────────────────────────── Annotateurs (identite) ───────────────────────
# `author` valait 'operator' en dur partout : impossible de savoir qui avait
# identifie quoi. L'identite courante est choisie une fois et memorisee.


def list_annotators() -> list[dict[str, Any]]:
    from src.annodb.annotators import list_annotators as _list
    from src.annodb.connection import session_scope

    _ensure_db()
    with session_scope() as session:
        return _list(session)


def list_known_annotator_names() -> list[str]:
    """Identités connues, complétées avec les opérateurs des sessions."""
    from src.annodb.annotators import list_known_annotator_names as _list
    from src.annodb.connection import session_scope

    _ensure_db()
    with session_scope() as session:
        return _list(session)


def create_annotator(display_name: str, orcid: str = "") -> dict[str, Any]:
    """Cree une identite et la retient comme annotateur courant."""
    from src.annodb.annotators import annotator_as_dict, create_annotator as _create
    from src.annodb.annotators import set_current_annotator as _set
    from src.annodb.connection import session_scope

    _ensure_db()
    with session_scope() as session:
        row = _create(session, display_name=display_name, orcid=orcid)
        out = annotator_as_dict(row)
        annotator_id = row.id
    # Hors du scope : le reglage n'est ecrit qu'une fois la ligne commitee.
    with session_scope() as session:
        _set(session, annotator_id)
    return out


def current_annotator() -> dict[str, Any]:
    """Annotateur courant, ou dictionnaire vide si personne ne s'est identifie."""
    from src.annodb.annotators import annotator_as_dict, current_annotator as _current
    from src.annodb.connection import session_scope

    _ensure_db()
    with session_scope() as session:
        return annotator_as_dict(_current(session))


def set_current_annotator(annotator_id: str) -> dict[str, Any]:
    from src.annodb.annotators import annotator_as_dict, set_current_annotator as _set
    from src.annodb.connection import session_scope

    _ensure_db()
    with session_scope() as session:
        return annotator_as_dict(_set(session, annotator_id))


def annotator_needed() -> bool:
    """Vrai au premier lancement : aucune identite d'annotateur connue."""
    from src.annodb.annotators import needs_identity
    from src.annodb.connection import session_scope

    _ensure_db()
    with session_scope() as session:
        return needs_identity(session)


def current_author() -> Optional[str]:
    """Valeur a ecrire dans `author`, ou None si personne ne s'est identifie."""
    from src.annodb.annotators import current_author_name
    from src.annodb.connection import session_scope

    _ensure_db()
    with session_scope() as session:
        return current_author_name(session)


# ───────────────────────── Sessions (paire G/D) ──────────────────────────
# Une session = UNE paire de videos gauche/droite (decision client). Elle peut
# etre creee avant la sortie terrain, donc avant d'avoir la moindre video.


def list_sessions_v2(
    *,
    status: Optional[str] = None,
    site: Optional[str] = None,
    with_stats: bool = True,
) -> list[dict[str, Any]]:
    """Sessions de la table `sessions`, groupables par statut.

    Le `_v2` distingue de `fish_db_stats.list_sessions`, qui liste des **medias**
    et sera retire quand la page Donnees basculera sur ce modele.
    """
    from src.annodb.connection import session_scope
    from src.annodb.sessions import list_sessions as _list

    _ensure_db()
    with session_scope() as session:
        return _list(session, status=status, site=site, with_stats=with_stats)


def create_session(
    *,
    name: str = "",
    site: str,
    session_date: str,
    notes: str = "",
    operator: str = "",
    status: str = "planned",
    left_media_id: Optional[str] = None,
    right_media_id: Optional[str] = None,
) -> dict[str, Any]:
    """Cree une session. Leve ValueError si le site ou la date manquent.

    `operator` non fourni = l'annotateur courant (table `annotators`) : c'est la
    decision superviseur, pas de champ libre dans le formulaire.
    """
    from src.annodb.annotators import current_author_name
    from src.annodb.connection import session_scope
    from src.annodb.sessions import create_session as _create
    from src.annodb.sessions import session_as_dict

    _ensure_db()
    with session_scope() as session:
        row = _create(
            session,
            name=name,
            site=site,
            session_date=session_date,
            notes=notes,
            operator=(operator or "").strip() or current_author_name(session),
            status=status,
            left_media_id=left_media_id,
            right_media_id=right_media_id,
        )
        return session_as_dict(session, row)


def update_session(session_id: str, **fields: Any) -> Optional[dict[str, Any]]:
    from src.annodb.connection import session_scope
    from src.annodb.sessions import session_as_dict
    from src.annodb.sessions import update_session as _update

    _ensure_db()
    with session_scope() as session:
        row = _update(session, session_id, **fields)
        return session_as_dict(session, row) if row else None


def delete_session(session_id: str) -> bool:
    from src.annodb.connection import session_scope
    from src.annodb.sessions import delete_session as _delete

    _ensure_db()
    with session_scope() as session:
        return _delete(session, session_id)


def attach_media_pair(
    session_id: str,
    left_path: str,
    right_path: str = "",
    *,
    project: str = REGISTRY_PROJECT,
    frame_offset: Optional[int] = None,
    calibration_profile: Optional[str] = None,
    calibration_sha256: Optional[str] = None,
    stereo_rmse: Optional[float] = None,
) -> Optional[dict[str, Any]]:
    """Attache la paire de videos a une session et **fige** l'offset de synchro.

    Les DEUX videos sont enregistrees en base : jusqu'ici seule la gauche
    l'etait, ce qui rendait la paire irrecuperable a la reouverture.

    La calibration active est enregistree (ou retrouvee par son sha256) au meme
    moment : sans elle, rien ne dit dans quel espace image les boites de cette
    session ont ete tracees.

    Leve `MediaAlreadyAttachedError` (une `ValueError`) si l'une des videos
    appartient deja a une autre session — un media n'appartient qu'a une seule.
    Cette fonction lit integralement les deux fichiers si leur empreinte n'est
    pas encore en cache : **a appeler hors du thread UI**.
    """
    from src.annodb.calibrations import register_calibration
    from src.annodb.connection import session_scope
    from src.annodb.frame_ref import timeline_offset
    from src.annodb.models import MediaAsset
    from src.annodb.sessions import attach_media_pair as _attach
    from src.annodb.sessions import session_as_dict

    left_id = resolve_media_id(left_path, project=project, create=True) if left_path else None
    right_id = resolve_media_id(right_path, project=project, create=True) if right_path else None
    if frame_offset is None:
        frame_offset = timeline_offset()
    profile = active_calibration()
    calibration_profile = calibration_profile or profile.get("profile_name")
    calibration_sha256 = calibration_sha256 or profile.get("calibration_sha256")

    _ensure_db()
    with session_scope() as session:
        calibration_id = None
        if calibration_sha256:
            media = session.get(MediaAsset, left_id) if left_id else None
            calib_row = register_calibration(
                session,
                profile_name=calibration_profile or "classic",
                sha256=calibration_sha256,
                alpha=profile.get("alpha"),
                image_width=(media.width if media is not None else None),
                image_height=(media.height if media is not None else None),
                baseline_mm=profile.get("baseline_mm"),
                stereo_rmse=(
                    stereo_rmse if stereo_rmse is not None else profile.get("stereo_rmse")
                ),
            )
            calibration_id = calib_row.id if calib_row is not None else None
        row = _attach(
            session,
            session_id,
            left_media_id=left_id,
            right_media_id=right_id,
            frame_offset=frame_offset,
            calibration_profile=calibration_profile,
            calibration_sha256=calibration_sha256,
            calibration_id=calibration_id,
        )
        if row is not None and not (row.operator or "").strip():
            # La session devient active : c'est le moment ou quelqu'un y
            # travaille vraiment. Jamais ecrase si elle porte deja un nom.
            from src.annodb.annotators import current_author_name

            row.operator = current_author_name(session)
        return session_as_dict(session, row) if row else None


def find_session_for_media(media_id: str) -> Optional[dict[str, Any]]:
    """Session portant ce media (gauche ou droite), ou None."""
    from src.annodb.connection import session_scope
    from src.annodb.sessions import find_session_for_media as _find
    from src.annodb.sessions import session_as_dict

    if not media_id:
        return None
    _ensure_db()
    with session_scope() as session:
        row = _find(session, media_id)
        return session_as_dict(session, row) if row else None


def session_export_preview(session_id: str) -> dict[str, Any]:
    """Résumé lisible et liste exacte des fichiers absents avant export."""
    from src.annodb.connection import session_scope
    from src.annodb.models import CaptureSession
    from src.annodb.sessions import session_as_dict

    _ensure_db()
    with session_scope() as session:
        row = session.get(CaptureSession, session_id)
        if row is None:
            raise ValueError("Session introuvable")
        data = session_as_dict(session, row, with_stats=True)
        media = []
        for pair in data.get("pairs") or []:
            for role in ("left", "right"):
                item = dict(pair.get(role) or {})
                if not item.get("media_id"):
                    continue
                item["role"] = "gauche" if role == "left" else "droite"
                item["pair_number"] = int(pair.get("position", 0)) + 1
                media.append(item)
        data["media"] = media
        data["missing_media"] = [item for item in media if not item.get("available")]
        data["available_media"] = [item for item in media if item.get("available")]
        return data


def repoint_media(media_id: str, replacement_path: str) -> dict[str, Any]:
    """Répare le chemin d'une vidéo déplacée après vérification SHA-256."""
    from src.annodb.connection import session_scope
    from src.annodb.models import MediaAsset
    from src.annodb.projects import sha256_for_path

    path = Path(replacement_path).resolve()
    if not path.is_file():
        raise FileNotFoundError(str(path))
    _ensure_db()
    with session_scope() as session:
        media = session.get(MediaAsset, media_id)
        if media is None:
            raise ValueError("Vidéo inconnue dans la base")
        digest = sha256_for_path(session, path, project_id=media.project_id)
        if media.sha256 and digest != media.sha256:
            raise ValueError(
                f"Le fichier choisi n'est pas la vidéo attendue « {Path(media.rel_path).name} » "
                "(empreinte SHA-256 différente)."
            )
        media.rel_path = str(path)
        media.sha256 = digest
        try:
            stat = path.stat()
            media.sha256_size = stat.st_size
            media.sha256_mtime = stat.st_mtime
        except OSError:
            pass
        return {"media_id": media.id, "path": str(path), "sha256": digest}


def active_calibration() -> dict[str, Any]:
    """Profil de calibration actif : nom, sha256, alpha, baseline, RMSE.

    Alimente la table `calibrations`. Renvoie des None quand aucune calibration
    n'est exploitable — l'appelant ne doit alors rien inventer.
    """
    from src.annodb.rectify import load_calibration_profile

    calib = load_calibration_profile()
    if calib is None:
        return {
            "profile_name": None,
            "calibration_sha256": None,
            "alpha": None,
            "baseline_mm": None,
            "stereo_rmse": None,
        }
    return calib.summary()


def _ancestors_chain(session, node_id: str) -> list:
    from src.annodb.models import TaxonNode
    chain = []
    current = session.get(TaxonNode, node_id)
    while current:
        chain.append(current)
        if not current.parent_id:
            break
        current = session.get(TaxonNode, current.parent_id)
    return chain


def resolve_hierarchy(taxon_node_id: Optional[str]) -> dict[str, Any]:
    """Retourne family/genus/species (noms + ids) depuis un noeud taxon."""
    out = {
        "family": "", "genus": "", "species": "",
        "family_id": None, "genus_id": None, "species_id": None,
        "taxon_node_id": taxon_node_id,
    }
    if not taxon_node_id:
        return out
    from src.annodb.connection import session_scope
    with session_scope(_ensure_db()) as session:
        for node in _ancestors_chain(session, taxon_node_id):
            if node.rank == "family":
                out["family"] = node.scientific_name
                out["family_id"] = node.id
            elif node.rank == "genus":
                out["genus"] = node.scientific_name
                out["genus_id"] = node.id
            elif node.rank == "species":
                out["species"] = node.scientific_name
                out["species_id"] = node.id
            elif node.rank == "provisional" and node.scientific_name not in ("fish", "Actinopterygii"):
                if not out["family"]:
                    out["family"] = node.scientific_name
                    out["family_id"] = node.id
    return out


def _finest_taxon_id(family_id, genus_id, species_id) -> Optional[str]:
    return species_id or genus_id or family_id


def _normalize_frame_ref(value) -> str:
    """Referentiel d'index de frame d'une ligne (absolute | timeline_legacy)."""
    from src.annodb.frame_ref import normalize

    return normalize(value)


def timeline_frame_offset(media_id: Optional[str] = None) -> Optional[int]:
    """Decalage timeline -> absolu, ou None s'il n'est pas determinable.

    Avec un `media_id`, l'offset **fige** par la session du media prime : c'est
    celui avec lequel ses lignes ont ete ecrites. Sans session (ou sans offset
    fige), repli sur le `sync_frames.npy` courant — l'ancien comportement.
    """
    from src.annodb.frame_ref import timeline_offset

    if media_id:
        from src.annodb.connection import session_scope
        from src.annodb.sessions import frame_offset_for_media

        _ensure_db()
        with session_scope() as session:
            frozen = frame_offset_for_media(session, media_id)
        if frozen is not None:
            return frozen
    return timeline_offset()


def to_absolute_frame(
    frame_index: int,
    frame_ref: Optional[str],
    media_id: Optional[str] = None,
) -> Optional[int]:
    """Index absolu d'une ligne de la base, ou None si non convertible."""
    from src.annodb.frame_ref import to_absolute

    return to_absolute(frame_index, frame_ref, timeline_frame_offset(media_id))


def list_observations(
    *,
    project: str = REGISTRY_PROJECT,
    media_path: Optional[str] = None,
    media_id: Optional[str] = None,
    ann_id: Optional[str] = None,
    capture_session_id: Optional[str] = None,
    limit: int = 500,
) -> list[dict[str, Any]]:
    from sqlalchemy import select
    from src.annodb.connection import session_scope
    from src.annodb.frame_ref import timeline_offset as _sync_offset
    from src.annodb.frame_ref import to_absolute as _to_absolute
    from src.annodb.models import CaptureSession, MediaAsset, Project, SpatialAnnotation
    from src.annodb.sessions import frame_offset_for_media, session_media_ids

    _ensure_db()
    if media_id is None and media_path:
        media_id = resolve_media_id(media_path, project=project)

    with session_scope() as session:
        proj = session.scalar(select(Project).where(Project.name == project))
        if not proj:
            return []
        q = (
            select(SpatialAnnotation, MediaAsset)
            .join(MediaAsset, SpatialAnnotation.media_id == MediaAsset.id)
            .where(MediaAsset.project_id == proj.id)
            .order_by(SpatialAnnotation.created_at.desc())
            .limit(limit)
        )
        if media_id:
            q = q.where(SpatialAnnotation.media_id == media_id)
        elif capture_session_id:
            capture = session.get(CaptureSession, capture_session_id)
            wanted = session_media_ids(session, capture) if capture is not None else []
            if not wanted:
                return []
            q = q.where(SpatialAnnotation.media_id.in_(wanted))
        if ann_id:
            q = q.where(SpatialAnnotation.id == ann_id)
        rows = session.execute(q).all()
        from src.annodb.behavior_flags import list_flags
        from src.annodb.models import EventType, TemporalEvent, Track
        from sqlalchemy import func

        flags_by_annotation = list_flags(session, (ann.id for ann, _media in rows))
        # Numero ByteTrack de la piste rattachee. L'interface parle de
        # « Piste #7 » partout ailleurs (liste des pistes, intervalles de
        # broutage) ; sans cette jointure la fiche du poisson n'avait que
        # l'UUID interne et ne pouvait donc pas nommer sa propre piste.
        wanted_tracks = {ann.track_id for ann, _media in rows if ann.track_id}
        external_by_track: dict[str, int] = {}
        if wanted_tracks:
            external_by_track = {
                str(tid): int(ext)
                for tid, ext in session.execute(
                    select(Track.id, Track.external_track_id)
                    .where(Track.id.in_(wanted_tracks))
                ).all()
                if ext is not None
            }
        # Une requête groupée pour toutes les pistes du registre, y compris
        # les types désactivés ou retirés du catalogue historique.
        events_by_track: dict[str, list[dict]] = {}
        if wanted_tracks:
            counts = session.execute(
                select(TemporalEvent.track_id, TemporalEvent.event_type,
                       func.count(TemporalEvent.id))
                .where(TemporalEvent.track_id.in_(wanted_tracks))
                .group_by(TemporalEvent.track_id, TemporalEvent.event_type)
                .order_by(TemporalEvent.event_type)
            ).all()
            catalog = {kind.key: kind for kind in session.scalars(select(EventType))}
            for tid, key, count in counts:
                kind = catalog.get(key)
                events_by_track.setdefault(tid, []).append({
                    "key": key,
                    "label": kind.label if kind else key or "Événement",
                    "count": count,
                })
        out = []
        # Offset par media, mis en cache : l'offset fige de la session prime,
        # le sync_frames.npy courant n'est qu'un repli (et se lit sur disque).
        sync_offset = _sync_offset()
        offsets: dict[str, Optional[int]] = {}

        def _offset_for(mid: str) -> Optional[int]:
            if mid not in offsets:
                frozen = frame_offset_for_media(session, mid)
                offsets[mid] = frozen if frozen is not None else sync_offset
            return offsets[mid]

        for ann, media in rows:
            if media_id is None and media_path:
                if Path(media_path).name not in media.rel_path:
                    continue
            hier = resolve_hierarchy(ann.taxon_node_id)
            geom = {}
            try:
                import json as _json
                geom = _json.loads(ann.geometry_json)
            except Exception:
                pass
            row_ref = _normalize_frame_ref(getattr(ann, "frame_ref", None))
            out.append({
                "ann_id": ann.id,
                # Valeur telle qu'elle est stockee.
                "frame_index": ann.frame_index,
                # absolute | timeline_legacy — l'appelant doit convertir avant
                # de rejouer la frame (cf. src/annodb/frame_ref.py).
                "frame_ref": row_ref,
                # Index absolu du fichier source, None si non convertible.
                "frame_index_abs": _to_absolute(
                    ann.frame_index, row_ref, _offset_for(media.id),
                ),
                "geometry_space": geom.get("space", ""),
                "media_id": media.id,
                "media_name": Path(media.rel_path).name,
                "media_path": media.rel_path,
                "track_id": ann.track_id,
                "external_track_id": external_by_track.get(
                    str(ann.track_id or ""),
                ),
                "confidence": ann.confidence,
                "measurement_mm": ann.measurement_mm,
                "position_x_mm": ann.position_x_mm,
                "position_y_mm": ann.position_y_mm,
                "position_z_mm": ann.position_z_mm,
                "geometry": geom,
                "source": ann.source,
                # Provenance et statut de relecture — immuables par ajout.
                # Préserver SQL NULL : lui seul autorise le repli legacy
                # centralisé. Le convertir en ``unreviewed`` changerait la
                # décision scientifique des consommateurs.
                "identification_status": getattr(
                    ann, "identification_status", None
                ),
                "family_is_na": getattr(ann, "family_is_na", None),
                "genus_is_na": getattr(ann, "genus_is_na", None),
                "species_is_na": getattr(ann, "species_is_na", None),
                "reviewed_by": getattr(ann, "reviewed_by", None) or "",
                "author": ann.author or "",
                "model_id": getattr(ann, "model_id", None) or "",
                "model_sha256": getattr(ann, "model_sha256", None) or "",
                "model_conf_threshold": getattr(ann, "model_conf_threshold", None),
                "crop_path": getattr(ann, "crop_path", None) or "",
                "behaviors": flags_by_annotation.get(ann.id, []),
                "track_events": events_by_track.get(ann.track_id, []),
                **hier,
            })
        return out


def count_observations(project: str = REGISTRY_PROJECT) -> int:
    from sqlalchemy import func, select
    from src.annodb.connection import session_scope
    from src.annodb.models import MediaAsset, Project, SpatialAnnotation

    _ensure_db()
    with session_scope() as session:
        proj = session.scalar(select(Project).where(Project.name == project))
        if not proj:
            return 0
        return session.scalar(
            select(func.count(SpatialAnnotation.id))
            .join(MediaAsset, SpatialAnnotation.media_id == MediaAsset.id)
            .where(MediaAsset.project_id == proj.id)
        ) or 0


def add_observation(
    media_path: str,
    frame_index: int,
    bbox: dict[str, float],
    *,
    cls_name: str = "fish",
    confidence: Optional[float] = None,
    measurement_mm: Optional[float] = None,
    track_id: Optional[str] = None,
    position_x_mm: Optional[float] = None,
    position_y_mm: Optional[float] = None,
    position_z_mm: Optional[float] = None,
    project: str = REGISTRY_PROJECT,
    source: str = "model",
    image_space: Optional[str] = None,
    ref_width: Optional[int] = None,
    ref_height: Optional[int] = None,
    frame_ref: Optional[str] = None,
    author: Optional[str] = None,
    model_id: Optional[str] = None,
    model_sha256: Optional[str] = None,
    model_conf_threshold: Optional[float] = None,
) -> dict[str, Any]:
    """Ajoute une observation. `frame_index` = index ABSOLU du fichier source.

    Quand `source='model'`, la provenance du detecteur (`model_id`,
    `model_sha256`, `model_conf_threshold`) est gravee ici. Elle ne sera plus
    jamais reecrite : la validation humaine ajoute `reviewed_by`/`reviewed_at`
    a cote, elle n'efface rien.

    Toute observation nait `identification_status='unreviewed'` : elle n'a, par
    construction, pas encore ete relue.
    """
    if track_id is None:
        track_id = bbox.get("db_track_id")
    if source == "model":
        model_id = model_id or (bbox.get("model_id") or None)
        model_sha256 = model_sha256 or (bbox.get("model_sha256") or None)
        if model_conf_threshold is None:
            model_conf_threshold = bbox.get("model_conf_threshold")
    else:
        # Une boite tracee a la main n'a pas de modele : ne rien inventer.
        model_id = model_sha256 = None
        model_conf_threshold = None
    px = position_x_mm if position_x_mm is not None else bbox.get("position_x_mm")
    py = position_y_mm if position_y_mm is not None else bbox.get("position_y_mm")
    pz = position_z_mm if position_z_mm is not None else bbox.get("position_z_mm")
    if source == "manual":
        taxon_id = propose_taxon_from_detection("fish")
    else:
        hint = (bbox.get("taxon_node_id") or "").strip() or None
        taxon_id = hint or propose_taxon_from_box(bbox) or propose_taxon_from_detection("fish")
    ann_id = save_bbox_annotation(
        media_path,
        frame_index,
        bbox,
        taxon_id,
        project=project,
        source=source,
        confidence=confidence,
        measurement_mm=measurement_mm,
        track_id=track_id,
        position_x_mm=px,
        position_y_mm=py,
        position_z_mm=pz,
        image_space=image_space,
        ref_width=ref_width,
        ref_height=ref_height,
        frame_ref=frame_ref,
        author=author,
        model_id=model_id,
        model_sha256=model_sha256,
        model_conf_threshold=model_conf_threshold,
    )
    hier = resolve_hierarchy(taxon_id)
    out = {
        "ann_id": ann_id,
        "frame_index": frame_index,
        "media_name": Path(media_path).name,
        "confidence": confidence,
        "identification_status": "unreviewed",
        "model_id": model_id,
        "measurement_mm": measurement_mm,
        "position_x_mm": px,
        "position_y_mm": py,
        "position_z_mm": pz,
        **hier,
    }
    species_ia = (bbox.get("species_name") or "").strip()
    if species_ia and not out.get("species_id"):
        out["suggested_species"] = species_ia
    return out


def update_observation(
    ann_id: str,
    *,
    family_id: Optional[str] = None,
    genus_id: Optional[str] = None,
    species_id: Optional[str] = None,
    taxon_node_id: Optional[str] = None,
    family_text: str = "",
    genus_text: str = "",
    species_text: str = "",
    family_is_na: Optional[bool] = None,
    genus_is_na: Optional[bool] = None,
    species_is_na: Optional[bool] = None,
    measurement_mm: Optional[float] = None,
    position_x_mm: Optional[float] = None,
    position_y_mm: Optional[float] = None,
    position_z_mm: Optional[float] = None,
    reviewed_by: Optional[str] = None,
) -> dict[str, Optional[str]]:
    """Applique une determination humaine a une observation.

    Poser un taxon = relire la ligne : `identification_status` passe a
    `identified`, ou a `unidentifiable` quand le taxon retenu est le noeud
    « poisson generique » (chemin « tout NA »). `reviewed_by` / `reviewed_at`
    sont ajoutes.

    REGLE D'OR : `model_id`, `model_sha256`, `model_conf_threshold` et
    `confidence` ne sont **jamais** touches ici. La provenance est immuable par
    ajout — c'est ce qui permet de mesurer plus tard si le modele avait raison.
    """
    from src.annodb.annotators import current_author_name
    from src.annodb.connection import session_scope
    from src.annodb.models import (
        STATUS_IDENTIFIED,
        STATUS_UNIDENTIFIABLE,
        SpatialAnnotation,
        TaxonReferenceEmbedding,
    )
    from src.annodb.spatial import mark_reviewed

    _ensure_db()
    resolved: dict[str, Optional[str]] = {}
    if taxon_node_id is None and any(
        x is not None or (t and t.strip())
        for x, t in (
            (family_id, family_text),
            (genus_id, genus_text),
            (species_id, species_text),
        )
    ):
        resolved = ensure_taxon_from_ui(
            family_id=family_id,
            family_text=family_text,
            genus_id=genus_id,
            genus_text=genus_text,
            species_id=species_id,
            species_text=species_text,
        )
        taxon_node_id = resolved.get("taxon_node_id")
    elif taxon_node_id is None and any(
        x is not None for x in (family_id, genus_id, species_id)
    ):
        taxon_node_id = _finest_taxon_id(family_id, genus_id, species_id)
        resolved = {
            "family_id": family_id,
            "genus_id": genus_id,
            "species_id": species_id,
            "taxon_node_id": taxon_node_id,
        }

    references_revoked = False
    with session_scope() as session:
        ann = session.get(SpatialAnnotation, ann_id)
        if not ann:
            raise ValueError(f"Annotation introuvable: {ann_id}")
        if taxon_node_id is not None:
            if ann.taxon_node_id != taxon_node_id:
                for ref in session.query(TaxonReferenceEmbedding).filter_by(
                    spatial_annotation_id=ann_id
                ):
                    session.delete(ref)
                    references_revoked = True
            ann.taxon_node_id = taxon_node_id
            ann.is_provisional = False
            # « Tout NA » : la ligne est bien relue, mais rien n'a pu etre
            # determine — c'est un resultat, pas un oubli.
            status = (
                STATUS_UNIDENTIFIABLE
                if taxon_node_id == UNIDENTIFIED_TAXON_ID
                else STATUS_IDENTIFIED
            )
            mark_reviewed(
                ann,
                status=status,
                reviewed_by=reviewed_by or current_author_name(session),
            )
            resolved["identification_status"] = status
            # Le taxon valide sur une bbox suivie vaut pour tout l'individu :
            # sans cette propagation, tracks.taxon_node_id restait vide et le
            # MaxN par espece ne pouvait rien produire.
            from src.annodb.tracks import recompute_track_taxonomy

            recompute_track_taxonomy(session, ann.track_id)
        # Les décisions NA sont indépendantes du taxon le plus fin et doivent
        # survivre à un redémarrage. ``None`` préserve les lignes historiques
        # et les appels qui ne font que corriger une mesure.
        for attr, value in (
            ("family_is_na", family_is_na),
            ("genus_is_na", genus_is_na),
            ("species_is_na", species_is_na),
        ):
            if value is not None:
                setattr(ann, attr, bool(value))
        if measurement_mm is not None:
            ann.measurement_mm = measurement_mm
        if position_x_mm is not None:
            ann.position_x_mm = position_x_mm
        if position_y_mm is not None:
            ann.position_y_mm = position_y_mm
        if position_z_mm is not None:
            ann.position_z_mm = position_z_mm
        if taxon_node_id is None:
            # Mesure ou position corrigee sans redetermination : on date la
            # modification sans pretendre a une relecture taxonomique.
            ann.updated_at = datetime.utcnow()

    if references_revoked:
        import fishial_gallery as fg

        fg.rebuild_centroids()
    return resolved


def delete_observation(ann_id: str) -> None:
    from src.annodb.connection import session_scope
    from src.annodb.models import SpatialAnnotation, TaxonReferenceEmbedding

    with session_scope(_ensure_db()) as session:
        ann = session.get(SpatialAnnotation, ann_id)
        if ann:
            track_id = ann.track_id
            for ref in session.query(TaxonReferenceEmbedding).filter_by(
                spatial_annotation_id=ann_id
            ):
                session.delete(ref)
            session.delete(ann)
            session.flush()
            from src.annodb.tracks import recompute_track_taxonomy

            recompute_track_taxonomy(session, track_id)
    # La transaction est commitée : aucun ancien vecteur ne doit rester dans
    # un centroïde déjà chargé en mémoire.
    import fishial_gallery as fg

    fg.rebuild_centroids()


def ensure_fishial_taxonomy_if_needed() -> int:
    """Synchronise le référentiel Madagascar et les espèces Fishial."""
    from src.annodb.connection import session_scope
    from src.annodb.seed_fishial import ensure_fishial_taxonomy

    labels = _FV_ROOT / "models" / "fishial_labels.json"
    if not labels.is_file():
        try:
            import urllib.request

            url = (
                "https://raw.githubusercontent.com/fishial/fish-identification/"
                "main/labels.json"
            )
            labels.parent.mkdir(parents=True, exist_ok=True)
            urllib.request.urlretrieve(url, labels)
        except OSError:
            pass

    with session_scope(_ensure_db()) as session:
        return ensure_fishial_taxonomy(
            session,
            labels if labels.is_file() else None,
        )


def build_taxon_index() -> dict[str, Any]:
    """Index noeuds + enfants pour combos cascade."""
    from collections import defaultdict

    nodes = list_taxon_nodes()
    by_id: dict[str, dict[str, Any]] = {n["id"]: n for n in nodes}
    children: dict[str, list[dict[str, Any]]] = defaultdict(list)
    by_name: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for n in nodes:
        if n["parent_id"]:
            children[n["parent_id"]].append(n)
        by_name[n["scientific_name"].lower()].append(n)
    for pid in children:
        children[pid].sort(key=lambda x: (x["rank"], x["scientific_name"].lower()))
    families = [n for n in nodes if n["rank"] == "family"]
    families.sort(key=lambda x: x["scientific_name"].lower())
    return {
        "nodes": by_id,
        "children": dict(children),
        "by_name": dict(by_name),
        "families": families,
    }


def find_taxon_id(scientific_name: str, rank: Optional[str] = None) -> Optional[str]:
    """Cherche un noeud par nom scientifique (optionnellement filtre par rang)."""
    name = (scientific_name or "").strip().lower()
    if not name:
        return None
    idx = build_taxon_index()
    for node in idx["by_name"].get(name, []):
        if rank is None or node["rank"] == rank:
            return node["id"]
    return None


def taxon_options_by_rank() -> dict[str, list[dict[str, Any]]]:
    ensure_fishial_taxonomy_if_needed()
    nodes = list_taxon_nodes()
    out: dict[str, list[dict[str, Any]]] = {"family": [], "genus": [], "species": []}
    for n in nodes:
        if n["rank"] in out:
            out[n["rank"]].append(n)
    return out


def save_bbox_annotation(
    media_path: str,
    frame_index: int,
    bbox: dict[str, float],
    taxon_node_id: str,
    *,
    project: str = "madagascar_measure",
    source: str = "manual",
    confidence: Optional[float] = None,
    author: Optional[str] = None,
    measurement_mm: Optional[float] = None,
    track_id: Optional[str] = None,
    position_x_mm: Optional[float] = None,
    position_y_mm: Optional[float] = None,
    position_z_mm: Optional[float] = None,
    image_space: Optional[str] = None,
    ref_width: Optional[int] = None,
    ref_height: Optional[int] = None,
    frame_ref: Optional[str] = None,
    model_id: Optional[str] = None,
    model_sha256: Optional[str] = None,
    model_conf_threshold: Optional[float] = None,
    identification_status: Optional[str] = None,
) -> str:
    """Enregistre une bbox dans spatial_annotations.

    `frame_index` est un index **absolu** du fichier vidéo source
    (`frame_ref='absolute'`), et la géométrie déclare ses unités, son espace
    image et sa taille de référence — sans quoi on ne peut plus savoir, à la
    relecture, sur quelle image la boîte a été tracée.

    `author=None` (defaut) resout l'annotateur courant : plus jamais
    `'operator'` en dur. Reste None tant que personne ne s'est identifie — une
    identite absente vaut mieux qu'une identite inventee.
    """
    from src.annodb.annotators import current_author_name
    from src.annodb.connection import get_db_path, init_db, session_scope
    from src.annodb.frame_ref import FRAME_REF_ABSOLUTE
    from src.annodb.models import STATUS_UNREVIEWED
    from src.annodb.projects import register_media
    from src.annodb.projects import get_or_create_project
    from src.annodb.rectify import IMAGE_SPACE_RAW
    from src.annodb.spatial import (
        UNITS_NORMALIZED,
        add_spatial_annotation,
        make_bbox_geometry,
    )

    path = Path(media_path)
    if not path.exists():
        raise FileNotFoundError(media_path)

    db_path = get_db_path()
    if not db_path.exists():
        init_db(db_path)

    x1, y1, x2, y2 = bbox["x1"], bbox["y1"], bbox["x2"], bbox["y2"]
    space = image_space or bbox.get("image_space") or IMAGE_SPACE_RAW
    ref_w = int(ref_width or bbox.get("ref_width") or 0) or None
    ref_h = int(ref_height or bbox.get("ref_height") or 0) or None

    with session_scope(db_path) as session:
        if author is None:
            author = current_author_name(session)
        proj = get_or_create_project(session, project)
        media_type = "video" if path.suffix.lower() in {".mp4", ".avi", ".mov", ".mkv"} else "image"
        media = register_media(
            session,
            project_id=proj.id,
            file_path=path,
            media_type=media_type,
            copy_into_store=False,
        )
        if not ref_w or not ref_h:
            # Repli : dimensions du média enregistrées à l'import.
            ref_w = ref_w or (int(media.width) if media.width else None)
            ref_h = ref_h or (int(media.height) if media.height else None)

        if max(x1, y1, x2, y2) <= 1.0:
            if ref_w and ref_h:
                # Entrée normalisée : on la ramène en pixels, l'unité de
                # travail de l'application.
                geometry = make_bbox_geometry(
                    x1 * ref_w, y1 * ref_h, x2 * ref_w, y2 * ref_h,
                    space=space, ref_width=ref_w, ref_height=ref_h,
                )
            else:
                geometry = make_bbox_geometry(
                    x1, y1, x2, y2, space=space, units=UNITS_NORMALIZED,
                )
        else:
            geometry = make_bbox_geometry(
                x1, y1, x2, y2, space=space, ref_width=ref_w, ref_height=ref_h,
            )

        taxon_node_id = _resolve_taxon_for_annotation(session, taxon_node_id, bbox)
        track_id = _resolve_track_for_annotation(session, track_id)
        ann = add_spatial_annotation(
            session,
            media_id=media.id,
            frame_index=frame_index,
            frame_ref=frame_ref or FRAME_REF_ABSOLUTE,
            geom_type="bbox",
            geometry=geometry,
            taxon_node_id=taxon_node_id,
            confidence=confidence,
            author=author,
            source=source,
            measurement_mm=measurement_mm,
            track_id=track_id,
            position_x_mm=position_x_mm,
            position_y_mm=position_y_mm,
            position_z_mm=position_z_mm,
            model_id=model_id,
            model_sha256=model_sha256,
            model_conf_threshold=model_conf_threshold,
            identification_status=identification_status or STATUS_UNREVIEWED,
        )
        return ann.id


def save_annotation_crop(annotation_id: str, jpeg_bytes: bytes) -> str:
    """Conserve la bbox en JPEG local et inscrit son chemin dans la base.

    Le fichier est écrit atomiquement sous la racine de données AquaMeasure.
    Cette copie est la matière première durable de la bibliothèque Fishial ;
    elle reste disponible après archivage de la vidéo source.
    """
    import os

    from src.annodb.connection import session_scope
    from src.annodb.models import SpatialAnnotation
    from src.annodb.storage_config import media_dir

    ann_id = str(annotation_id or "").strip()
    if not ann_id:
        raise ValueError("Identifiant d'observation manquant")
    if not jpeg_bytes:
        raise ValueError("Crop JPEG vide")
    root = media_dir() / "fishial_crops" / ann_id[:2]
    root.mkdir(parents=True, exist_ok=True)
    dest = root / f"{ann_id}.jpg"
    tmp = root / f".{ann_id}.tmp"
    tmp.write_bytes(bytes(jpeg_bytes))
    os.replace(tmp, dest)
    with session_scope() as session:
        ann = session.get(SpatialAnnotation, ann_id)
        if ann is None:
            dest.unlink(missing_ok=True)
            raise ValueError("Observation introuvable pour le crop local")
        ann.crop_path = str(dest)
    return str(dest)


def resolve_track_db_id(
    media_path: str,
    external_track_id: int,
    *,
    project: str = REGISTRY_PROJECT,
) -> Optional[str]:
    """UUID piste en base a partir du numero ByteTrack externe."""
    from sqlalchemy import select
    from src.annodb.connection import session_scope
    from src.annodb.models import MediaAsset, Project, Track

    media_id = resolve_media_id(media_path, project=project)
    if not media_id:
        return None
    _ensure_db()
    with session_scope() as session:
        proj = session.scalar(select(Project).where(Project.name == project))
        if not proj:
            return None
        media = session.get(MediaAsset, media_id)
        if not media or media.project_id != proj.id:
            return None
        track = session.scalar(
            select(Track).where(
                Track.media_id == media_id,
                Track.external_track_id == external_track_id,
                Track.source == "bytetrack",
            )
        )
        return track.id if track else None


# Résultats de attach_observation_to_track. Écraser ou non le rattachement
# d'une observation est une décision scientifique, pas un détail : un booléen
# ne permettrait pas de distinguer « c'était déjà cette piste » de « cette
# observation appartient à une autre piste, on n'a rien touché ».
LINK_LINKED = "linked"
LINK_ALREADY = "already"
LINK_CONFLICT = "conflict"
# Resultats de detach_observation_from_track. Meme raison qu'au-dessus : un
# booleen ne distinguerait pas « le lien a saute » de « il n'y en avait pas ».
LINK_DETACHED = "detached"
LINK_NOT_LINKED = "not_linked"


def attach_observation_to_track(ann_id: str, track_db_id: str) -> str:
    """Rattache une observation du registre à une piste, sans rien écraser.

    Le poisson mesuré et identifié vit dans `spatial_annotations` ; la broute
    vit dans `temporal_events`, accrochée à une piste. Sans ce rattachement les
    deux ne se rejoignent nulle part : ni à l'écran, ni dans le CSV de session
    qui joint justement les deux par `track_id`.

    Une observation qui porte déjà une AUTRE piste n'est jamais réécrite : le
    conflit remonte à l'appelant, à charge pour lui de le dire à l'utilisateur.

    Retourne LINK_LINKED, LINK_ALREADY ou LINK_CONFLICT.
    """
    from src.annodb.connection import session_scope
    from src.annodb.models import SpatialAnnotation, Track
    from src.annodb.tracks import recompute_track_taxonomy

    ann_id = str(ann_id or "").strip()
    track_db_id = str(track_db_id or "").strip()
    if not ann_id or not track_db_id:
        raise ValueError("Observation ou piste absente")
    _ensure_db()
    with session_scope() as session:
        ann = session.get(SpatialAnnotation, ann_id)
        if ann is None:
            raise ValueError(f"Observation introuvable: {ann_id}")
        track = session.get(Track, track_db_id)
        if track is None:
            raise ValueError(f"Piste introuvable: {track_db_id}")
        if track.media_id != ann.media_id:
            raise ValueError(
                "La piste et l'observation ne sont pas sur la même vidéo"
            )
        current = str(ann.track_id or "")
        if current == track_db_id:
            return LINK_ALREADY
        if current:
            return LINK_CONFLICT
        ann.track_id = track_db_id
        session.flush()
        # Le taxon déjà validé sur l'observation devient celui de la piste :
        # sinon la broute exportée resterait sans espèce alors que le poisson
        # qui l'a produite est identifié.
        recompute_track_taxonomy(session, track_db_id)
        return LINK_LINKED


def track_link_summary(ann_id: str) -> dict[str, Any]:
    """Ce qu'un detachement ferait perdre a cette observation.

    `attach_observation_to_track` refuse d'ecraser un rattachement existant.
    C'est juste, mais l'utilisateur se retrouvait prevenu sans issue : aucun
    geste ne defaisait le lien. Avant de le proposer, il faut pouvoir dire ce
    qu'il coute - les evenements de la piste (bouchees, intervalles) ne sont
    pas supprimes, mais ils cessent d'etre joints a ce poisson, a l'ecran
    comme dans le CSV de session qui joint les deux par `track_id`.

    Retourne les compteurs, jamais une exception : c'est une lecture d'aide a
    la decision, pas une ecriture.
    """
    from sqlalchemy import select
    from src.annodb.connection import session_scope
    from src.annodb.event_types import is_point_scope
    from src.annodb.models import SpatialAnnotation, TemporalEvent, Track

    empty = {
        "track_db_id": "",
        "external_track_id": None,
        "point_count": 0,
        "interval_count": 0,
        "event_count": 0,
    }
    ann_id = str(ann_id or "").strip()
    if not ann_id:
        return empty
    _ensure_db()
    scopes = {
        row["key"]: row.get("scope")
        for row in list_event_types(active_only=False)
    }
    with session_scope() as session:
        ann = session.get(SpatialAnnotation, ann_id)
        if ann is None or not ann.track_id:
            return empty
        track_db_id = str(ann.track_id)
        track = session.get(Track, track_db_id)
        events = session.scalars(
            select(TemporalEvent).where(TemporalEvent.track_id == track_db_id)
        ).all()
        points = sum(
            1 for ev in events if is_point_scope(scopes.get(ev.event_type or ""))
        )
        return {
            "track_db_id": track_db_id,
            "external_track_id": (
                track.external_track_id if track is not None else None
            ),
            "point_count": points,
            "interval_count": len(events) - points,
            "event_count": len(events),
        }


def detach_observation_from_track(ann_id: str) -> str:
    """Defait le rattachement d'une observation a sa piste, sans rien detruire.

    Geste inverse de `attach_observation_to_track`, et symetrique jusqu'au
    bout : la taxonomie de la piste est recalculee, sinon une piste garderait
    l'espece d'un poisson qui ne lui appartient plus et le MaxN par espece
    compterait un individu de trop.

    La piste et ses evenements survivent : seule la colonne `track_id` de
    l'observation est remise a NULL. Une bouchee mal attribuee se corrige en
    rattachant le bon poisson, pas en effacant le travail de pointage.

    Retourne LINK_DETACHED ou LINK_NOT_LINKED.
    """
    from src.annodb.connection import session_scope
    from src.annodb.models import SpatialAnnotation
    from src.annodb.tracks import recompute_track_taxonomy

    ann_id = str(ann_id or "").strip()
    if not ann_id:
        raise ValueError("Observation absente")
    _ensure_db()
    with session_scope() as session:
        ann = session.get(SpatialAnnotation, ann_id)
        if ann is None:
            raise ValueError(f"Observation introuvable: {ann_id}")
        previous = str(ann.track_id or "")
        if not previous:
            return LINK_NOT_LINKED
        ann.track_id = None
        session.flush()
        # Meme raison qu'au rattachement, dans l'autre sens : la piste ne doit
        # plus tirer son autorite d'une observation qui l'a quittee.
        recompute_track_taxonomy(session, previous)
        return LINK_DETACHED


DEFAULT_EVENT_TYPE = "grazing"


def list_event_types(
    active_only: bool = True, *, with_usage: bool = False
) -> list[dict[str, Any]]:
    """Catalogue des comportements annotables (broutage, fuite, ponte…)."""
    from src.annodb.connection import session_scope
    from src.annodb.event_types import list_event_types as _list

    _ensure_db()
    with session_scope() as session:
        return _list(session, active_only=active_only, with_usage=with_usage)


def create_behavior_type(
    label: str, scope: str, symbol: str = "●",
) -> dict[str, Any]:
    """Crée un type depuis le formulaire nom + portée + pictogramme."""
    from src.annodb.connection import session_scope
    from src.annodb.event_types import as_dict, create_event_type

    _ensure_db()
    with session_scope() as session:
        return as_dict(create_event_type(
            session, label=label, scope=scope, symbol=(symbol or "●").strip() or "●",
        ))


def set_behavior_shortcut(type_id: str, shortcut: str) -> dict[str, Any]:
    """Pose (ou retire) la lettre de raccourci d'un type du catalogue.

    Le champ `shortcut` existait dans `event_types` depuis le debut sans aucun
    formulaire pour le remplir : un type cree par l'utilisateur n'avait donc
    jamais de raccourci, et le clavier restait inutilisable pour annoter des
    dizaines de marqueurs.
    """
    from src.annodb.connection import session_scope
    from src.annodb.event_types import as_dict, update_event_type

    _ensure_db()
    with session_scope() as session:
        et = update_event_type(session, type_id, shortcut=shortcut)
        if et is None:
            raise ValueError(f"Comportement introuvable: {type_id}")
        return as_dict(et)


def remove_behavior_type(type_id: str) -> bool:
    """Supprime un type inutilisé, sinon le désactive sans perdre l'historique."""
    from src.annodb.connection import session_scope
    from src.annodb.event_types import delete_event_type

    _ensure_db()
    with session_scope() as session:
        return delete_event_type(session, type_id)


def toggle_observation_behavior(annotation_id: str, event_type: str) -> bool:
    """Inverse un comportement ponctuel sur une observation enregistrée."""
    from src.annodb.behavior_flags import toggle_flag
    from src.annodb.connection import session_scope

    _ensure_db()
    with session_scope() as session:
        return toggle_flag(
            session,
            annotation_id=annotation_id,
            event_type=event_type,
        )


def add_grazing_interval(
    track_db_id: str,
    frame_start: int,
    frame_end: int,
    *,
    author: Optional[str] = None,
    frame_ref: Optional[str] = None,
    event_type: str = DEFAULT_EVENT_TYPE,
) -> str:
    """Enregistre un intervalle d'evenement manuel (temporal_events).

    La broute reste le type par defaut — le catalogue `event_types` en accepte
    d'autres (fuite, ponte…), d'ou `event_type`.

    Les bornes sont des index **absolus** du fichier source, comme les
    echantillons de piste auxquels l'intervalle sera compare.
    """
    if frame_end < frame_start:
        frame_start, frame_end = frame_end, frame_start
    from src.annodb.annotators import current_author_name
    from src.annodb.connection import session_scope
    from src.annodb.event_types import get_by_key
    from src.annodb.events import add_temporal_event
    from src.annodb.frame_ref import FRAME_REF_ABSOLUTE

    _ensure_db()
    with session_scope() as session:
        key = (event_type or DEFAULT_EVENT_TYPE).strip() or DEFAULT_EVENT_TYPE
        behavior = get_by_key(session, key)
        if behavior is None:
            raise ValueError(f"Comportement indisponible: {key}")
        if behavior.scope != "interval":
            raise ValueError("Un comportement ponctuel ne peut pas créer un intervalle")
        ev = add_temporal_event(
            session,
            track_id=track_db_id,
            frame_start=frame_start,
            frame_end=frame_end,
            event_type=key,
            source="manual",
            # Plus jamais 'operator' en dur : l'annotateur courant, ou rien.
            author=author if author is not None else current_author_name(session),
            confidence=1.0,
            frame_ref=frame_ref or FRAME_REF_ABSOLUTE,
        )
        return ev.id


def list_point_event_types(active_only: bool = True) -> list[dict[str, Any]]:
    """Types de portee ponctuelle du catalogue — ceux qui posent un marqueur.

    Rien n'est code en dur : si le chercheur n'a pas encore cree « Bouchee »
    depuis les reglages, cette liste est vide et l'interface le dit.
    """
    from src.annodb.event_types import is_point_scope

    return [
        row for row in list_event_types(active_only=active_only)
        if is_point_scope(row.get("scope"))
    ]


def add_behavior_point(
    track_db_id: str,
    frame_abs: int,
    *,
    event_type: str,
    author: Optional[str] = None,
    frame_ref: Optional[str] = None,
) -> str:
    """Marque un instant unique sur une piste : frame_start == frame_end.

    Fonction soeur de `add_grazing_interval`, jamais un detournement : celle-la
    refuse explicitement les types ponctuels (`raise` des que
    `scope != "interval"`), et l'appeler pour une bouchee ecrirait l'evenement
    sans jamais verifier la portee du type. Ici la verification est l'inverse,
    et les deux bornes valent la meme frame ABSOLUE — la convention de
    `track_samples`, celle a laquelle l'evenement sera compare.

    `spatial_behavior_flags` ne conviendrait pas : ce drapeau est porte par une
    observation enregistree (`spatial_annotation_id`), pas par une frame de
    piste, donc il ne sait pas dire « ce poisson, a cette image ».
    """
    from src.annodb.annotators import current_author_name
    from src.annodb.connection import session_scope
    from src.annodb.event_types import get_by_key, is_point_scope
    from src.annodb.events import add_temporal_event
    from src.annodb.frame_ref import FRAME_REF_ABSOLUTE

    db_id = str(track_db_id or "").strip()
    if not db_id:
        raise ValueError("Aucune piste selectionnee pour ce marqueur")
    frame = max(0, int(frame_abs))
    key = (event_type or "").strip()
    if not key:
        raise ValueError("Aucun type d'evenement ponctuel choisi")

    _ensure_db()
    with session_scope() as session:
        behavior = get_by_key(session, key)
        if behavior is None:
            raise ValueError(f"Comportement indisponible: {key}")
        if not is_point_scope(behavior.scope):
            raise ValueError(
                "Un comportement d'intervalle ne peut pas poser un marqueur "
                "ponctuel"
            )
        ev = add_temporal_event(
            session,
            track_id=db_id,
            frame_start=frame,
            frame_end=frame,
            event_type=key,
            source="manual",
            # Meme regle que l'intervalle : l'annotateur courant, jamais
            # 'operator' en dur.
            author=author if author is not None else current_author_name(session),
            confidence=1.0,
            frame_ref=frame_ref or FRAME_REF_ABSOLUTE,
        )
        return ev.id


def list_behavior_points(
    media_path: str,
    *,
    project: str = REGISTRY_PROJECT,
    track_db_id: Optional[str] = None,
    event_type: Optional[str] = None,
) -> list[dict[str, Any]]:
    """Marqueurs ponctuels manuels d'une video, tries par piste puis par frame.

    Pendant symetrique de `list_grazing_intervals`, restreint aux types de
    portee ponctuelle : melanger les deux dans une meme liste ferait afficher
    une bouchee comme un intervalle « f120 -> f120 », ce qui ne veut rien dire
    pour le chercheur.
    """
    from sqlalchemy import select
    from src.annodb.connection import session_scope
    from src.annodb.event_types import is_point_scope
    from src.annodb.events import list_events_for_media
    from src.annodb.frame_ref import to_absolute as _to_abs
    from src.annodb.models import Track

    media_id = resolve_media_id(media_path, project=project)
    if not media_id:
        return []
    _ensure_db()
    type_rows = {row["key"]: row for row in list_event_types(active_only=False)}
    point_keys = {
        key for key, row in type_rows.items() if is_point_scope(row.get("scope"))
    }
    if not point_keys:
        return []
    wanted_track = str(track_db_id or "").strip()
    offset = timeline_frame_offset(media_id)
    with session_scope() as session:
        tracks = {
            t.id: t.external_track_id
            for t in session.scalars(select(Track).where(Track.media_id == media_id))
        }
        out: list[dict[str, Any]] = []
        for ev in list_events_for_media(session, media_id):
            if ev.source != "manual":
                continue
            ev_type = ev.event_type or ""
            if ev_type not in point_keys:
                continue
            if event_type and ev_type != event_type:
                continue
            if wanted_track and ev.track_id != wanted_track:
                continue
            row_ref = _normalize_frame_ref(getattr(ev, "frame_ref", None))
            row = type_rows.get(ev_type, {})
            out.append({
                "event_id": ev.id,
                "track_db_id": ev.track_id,
                "external_track_id": tracks.get(ev.track_id),
                "frame": ev.frame_start,
                "frame_ref": row_ref,
                # None quand une ligne historique n'est pas convertible : mieux
                # vaut ne rien afficher que la placer quelques milliers de
                # frames a cote.
                "frame_abs": _to_abs(ev.frame_start, row_ref, offset),
                "event_type": ev_type,
                "event_label": row.get("label", ev_type),
                "event_symbol": row.get("symbol", "●") or "●",
                "event_color": row.get("color", "#f59e0b") or "#f59e0b",
                "author": ev.author,
            })
        out.sort(key=lambda x: (x["external_track_id"] or 0, x["frame"]))
        return out


def delete_behavior_point(event_id: str) -> bool:
    """Retire un marqueur ponctuel — jamais un intervalle de broutage.

    Le garde-fou n'est pas cosmetique : les deux vivent dans `temporal_events`,
    et un identifiant colle au mauvais bouton effacerait une sequence de
    broutage entiere au lieu d'une bouchee mal placee.
    """
    from src.annodb.connection import session_scope
    from src.annodb.event_types import get_by_key, is_point_scope
    from src.annodb.events import delete_temporal_event
    from src.annodb.models import TemporalEvent

    eid = str(event_id or "").strip()
    if not eid:
        return False
    _ensure_db()
    with session_scope() as session:
        ev = session.get(TemporalEvent, eid)
        if ev is None:
            raise ValueError(f"Marqueur introuvable: {eid}")
        behavior = get_by_key(session, ev.event_type or "")
        scope_ok = (
            is_point_scope(behavior.scope) if behavior is not None
            else ev.frame_start == ev.frame_end
        )
        if not scope_ok:
            raise ValueError(
                "Cet evenement est un intervalle : retirez-le depuis la liste "
                "des intervalles"
            )
        return delete_temporal_event(session, eid)


def list_grazing_intervals(
    media_path: str,
    *,
    project: str = REGISTRY_PROJECT,
    event_type: Optional[str] = None,
) -> list[dict[str, Any]]:
    """Intervalles d'evenements manuels d'une video, tous types par defaut.

    `event_type=None` renvoie tout le catalogue (chaque ligne porte sa cle et
    son libelle) ; passer une cle restreint a ce type.

    Les types de portee ponctuelle en sont exclus : ils vivent dans la meme
    table, mais les afficher ici donnerait des lignes « f120 -> f120 » qui ne
    veulent rien dire. Voir `list_behavior_points`.
    """
    from sqlalchemy import select
    from src.annodb.connection import session_scope
    from src.annodb.event_types import is_point_scope
    from src.annodb.events import list_events_for_media
    from src.annodb.frame_ref import to_absolute as _to_abs
    from src.annodb.models import Track

    media_id = resolve_media_id(media_path, project=project)
    if not media_id:
        return []
    _ensure_db()
    type_rows = {
        row["key"]: row for row in list_event_types(active_only=False)
    }
    offset = timeline_frame_offset(media_id)
    with session_scope() as session:
        tracks = {
            t.id: t.external_track_id
            for t in session.scalars(select(Track).where(Track.media_id == media_id))
        }
        events = list_events_for_media(session, media_id)
        out = []
        for ev in events:
            if ev.source != "manual":
                continue
            ev_type = ev.event_type or DEFAULT_EVENT_TYPE
            if event_type and ev_type != event_type:
                continue
            if is_point_scope(type_rows.get(ev_type, {}).get("scope")):
                continue
            row_ref = _normalize_frame_ref(getattr(ev, "frame_ref", None))
            out.append({
                "event_id": ev.id,
                "track_db_id": ev.track_id,
                "external_track_id": tracks.get(ev.track_id),
                "frame_start": ev.frame_start,
                "frame_end": ev.frame_end,
                "frame_ref": row_ref,
                # None quand une ligne historique n'est pas convertible.
                "frame_start_abs": _to_abs(ev.frame_start, row_ref, offset),
                "frame_end_abs": _to_abs(ev.frame_end, row_ref, offset),
                "event_type": ev_type,
                "event_label": type_rows.get(ev_type, {}).get("label", ev_type),
                "event_symbol": type_rows.get(ev_type, {}).get("symbol", "●") or "●",
                "event_color": type_rows.get(ev_type, {}).get("color", "#f59e0b") or "#f59e0b",
                "author": ev.author,
            })
        out.sort(key=lambda x: (x["external_track_id"] or 0, x["frame_start"]))
        return out


def list_video_annotation_overlays(
    media_path: Optional[str] = None,
    *,
    media_id: Optional[str] = None,
    project: str = REGISTRY_PROJECT,
) -> dict[str, list[dict[str, Any]]]:
    """Données légères nécessaires pour rejouer pistes et pictogrammes.

    Les échantillons de piste sont déjà exprimés dans l'index absolu du fichier
    source. Les comportements ponctuels conservent leur géométrie déclarée ; le
    contrôleur d'affichage la convertit en pixels avec la taille réellement vue.
    Cette lecture groupée évite une requête SQLite à chaque image de la vidéo.
    """
    from sqlalchemy import select
    from src.annodb.connection import session_scope
    from src.annodb.frame_ref import to_absolute as _to_abs
    from src.annodb.models import (
        EventType,
        SpatialAnnotation,
        SpatialBehaviorFlag,
        Track,
        TrackSample,
        TaxonNode,
    )

    if media_id is None and media_path:
        media_id = resolve_media_id(media_path, project=project)
    if not media_id:
        return {"trackSamples": [], "instantAnnotations": []}

    _ensure_db()
    offset = timeline_frame_offset(media_id)
    with session_scope() as session:
        from src.annodb.identification import resolve_track_taxa
        tracks = session.scalars(select(Track).where(Track.media_id == media_id)).all()
        resolved = resolve_track_taxa(session, tracks)
        nodes = {
            node.id: node.scientific_name
            for node in session.scalars(select(TaxonNode).where(
                TaxonNode.id.in_([tid for tid in resolved.values() if tid])
            ))
        }
        identities = [{
            "trackDbId": track.id,
            "trackId": int(track.external_track_id),
            "name": nodes.get(resolved.get(track.id), ""),
            "status": track.identification_status or "",
        } for track in tracks]
        track_samples: list[dict[str, Any]] = []
        sample_rows = session.execute(
            select(TrackSample, Track)
            .join(Track, TrackSample.track_id == Track.id)
            .where(Track.media_id == media_id)
            .order_by(TrackSample.frame_index, Track.external_track_id)
        ).all()
        for sample, track in sample_rows:
            try:
                geometry = json.loads(sample.bbox_json or "{}")
            except (TypeError, ValueError):
                continue
            x1 = geometry.get("x_min", geometry.get("x1"))
            y1 = geometry.get("y_min", geometry.get("y1"))
            x2 = geometry.get("x_max", geometry.get("x2"))
            y2 = geometry.get("y_max", geometry.get("y2"))
            if None in (x1, y1, x2, y2):
                continue
            track_samples.append({
                "frameIndexAbs": int(sample.frame_index),
                "trackDbId": track.id,
                "trackId": int(track.external_track_id),
                "x1": float(x1),
                "y1": float(y1),
                "x2": float(x2),
                "y2": float(y2),
                "origin": sample.origin or "auto",
            })

        instant_annotations: list[dict[str, Any]] = []
        instant_rows = session.execute(
            select(SpatialAnnotation, SpatialBehaviorFlag, EventType, Track)
            .join(
                SpatialBehaviorFlag,
                SpatialBehaviorFlag.spatial_annotation_id == SpatialAnnotation.id,
            )
            .join(EventType, SpatialBehaviorFlag.event_type == EventType.key)
            .outerjoin(Track, SpatialAnnotation.track_id == Track.id)
            .where(
                SpatialAnnotation.media_id == media_id,
                EventType.scope == "instant",
            )
            .order_by(SpatialAnnotation.frame_index, EventType.sort_order, EventType.label)
        ).all()
        for annotation, flag, event_type, track in instant_rows:
            row_ref = _normalize_frame_ref(getattr(annotation, "frame_ref", None))
            frame_abs = _to_abs(annotation.frame_index, row_ref, offset)
            if frame_abs is None:
                continue
            try:
                geometry = json.loads(annotation.geometry_json or "{}")
            except (TypeError, ValueError):
                continue
            instant_annotations.append({
                "annotationId": annotation.id,
                "frameIndexAbs": int(frame_abs),
                "trackDbId": annotation.track_id or "",
                "trackId": int(track.external_track_id) if track is not None else -1,
                "geometry": geometry,
                "confidence": float(annotation.confidence or 0.0),
                "behavior": {
                    "key": flag.event_type,
                    "label": event_type.label,
                    "symbol": event_type.symbol or "●",
                    "color": event_type.color or "#f59e0b",
                    "scope": "instant",
                },
            })
        return {
            "trackIdentifications": identities,
            "trackSamples": track_samples,
            "instantAnnotations": instant_annotations,
        }


def delete_grazing_interval(event_id: str) -> None:
    from src.annodb.connection import session_scope
    from src.annodb.events import delete_temporal_event

    _ensure_db()
    with session_scope() as session:
        if not delete_temporal_event(session, event_id):
            raise ValueError(f"Intervalle introuvable: {event_id}")


def is_manual_grazing_frame(
    media_path: str,
    frame_index: int,
    track_db_id: Optional[str] = None,
) -> bool:
    """True si la frame ABSOLUE est dans un intervalle broute manuel.

    Les intervalles historiques sont convertis ; ceux qui ne le sont pas sont
    ignores (et signales) plutot que compares dans le mauvais referentiel.
    """
    import logging

    for row in list_grazing_intervals(media_path, event_type=DEFAULT_EVENT_TYPE):
        if track_db_id and row["track_db_id"] != track_db_id:
            continue
        start = row.get("frame_start_abs")
        end = row.get("frame_end_abs")
        if start is None or end is None:
            logging.getLogger(__name__).warning(
                "Broute %s en index timeline historique non convertible — ignoree",
                row.get("event_id"),
            )
            continue
        if start <= frame_index <= end:
            return True
    return False


def reclassify_annotation(ann_id: str, new_taxon_node_id: str) -> None:
    """Change le taxon d'une observation — c'est une relecture humaine."""
    from src.annodb.annotators import current_author_name
    from src.annodb.connection import get_db_path, session_scope
    from src.annodb.models import (
        STATUS_IDENTIFIED,
        STATUS_UNIDENTIFIABLE,
        SpatialAnnotation,
        TaxonReferenceEmbedding,
    )
    from src.annodb.spatial import mark_reviewed
    from src.annodb.tracks import recompute_track_taxonomy

    with session_scope(get_db_path()) as session:
        ann = session.get(SpatialAnnotation, ann_id)
        if not ann:
            raise ValueError(f"Annotation introuvable: {ann_id}")
        for ref in session.query(TaxonReferenceEmbedding).filter_by(
            spatial_annotation_id=ann_id
        ):
            session.delete(ref)
        ann.taxon_node_id = new_taxon_node_id
        ann.is_provisional = False
        mark_reviewed(
            ann,
            status=(
                STATUS_UNIDENTIFIABLE
                if new_taxon_node_id == UNIDENTIFIED_TAXON_ID
                else STATUS_IDENTIFIED
            ),
            reviewed_by=current_author_name(session),
        )
        recompute_track_taxonomy(session, ann.track_id)
    import fishial_gallery as fg

    fg.rebuild_centroids()


def remonter_taxon(taxon_node_id: str) -> Optional[str]:
    """Remonte d'un rang taxonomique (espece -> genre -> famille)."""
    from src.annodb.connection import get_db_path, session_scope
    from src.annodb.models import TaxonNode

    with session_scope(get_db_path()) as session:
        node = session.get(TaxonNode, taxon_node_id)
        if not node or not node.parent_id:
            return taxon_node_id
        parent = session.get(TaxonNode, node.parent_id)
        if not parent:
            return taxon_node_id
        if parent.rank == "provisional" and parent.parent_id:
            return parent.parent_id
        return parent.id


def export_and_retrain(
    rank: str = "family",
    epochs: int | None = None,
    skip_train: bool = False,
) -> dict[str, Any]:
    """Lance retrain_from_db.py et retourne un resume."""
    import subprocess

    script = _FV_ROOT / "scripts" / "retrain_from_db.py"
    cmd = [sys.executable, str(script), "--rank", rank]
    if epochs:
        cmd.extend(["--epochs", str(epochs)])
    if skip_train:
        cmd.append("--skip-train")
    proc = subprocess.run(cmd, capture_output=True, text=True, cwd=str(_FV_ROOT))
    return {
        "ok": proc.returncode == 0,
        "stdout": proc.stdout,
        "stderr": proc.stderr,
        "returncode": proc.returncode,
    }


def export_training_hint(rank: str = "family") -> str:
    """Commande suggeree pour re-entrainement."""
    return (
        f"cd fish-vision && .venv\\Scripts\\python scripts\\retrain_from_db.py --rank {rank}"
    )
