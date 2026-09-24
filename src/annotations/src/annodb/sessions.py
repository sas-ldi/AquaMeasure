"""Sessions de terrain - une session contient plusieurs prises stéréo.

Avant cette table, une « session » n'existait que comme quatre colonnes de
métadonnées posées sur un média (`media_assets.site`, `session_title`, `notes`,
`session_date`) - jamais remplies, sans rôle gauche/droite, sans lien de paire.
Impossible dans ces conditions de préparer une sortie terrain à l'avance ni de
rouvrir une session d'un clic.

Deux points structurants :

- une session **planifiée** n'a pas encore de prise : on prépare la sortie ;
- chaque prise garde sa paire, sa synchro et sa calibration propres ;
- les anciennes colonnes de `sessions` pointent vers la dernière paire pour
  préserver la compatibilité avec les versions précédentes.
"""

from __future__ import annotations

import uuid
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional

from sqlalchemy import func, or_, select
from sqlalchemy.orm import Session

from .identification import authoritative_identification_clause
from .models import (
    CaptureSession,
    FrameAbundance,
    MediaAsset,
    SessionMediaPair,
    SpatialAnnotation,
    SpatialBehaviorFlag,
    TemporalEvent,
    TaxonNode,
    Track,
)

STATUS_PLANNED = "planned"
STATUS_ACTIVE = "active"
STATUS_DONE = "done"
STATUS_EXPORTED = "exported"

STATUSES = (STATUS_PLANNED, STATUS_ACTIVE, STATUS_DONE, STATUS_EXPORTED)

STATUS_LABELS: Dict[str, str] = {
    STATUS_PLANNED: "À venir",
    STATUS_ACTIVE: "En cours",
    STATUS_DONE: "Terminée",
    STATUS_EXPORTED: "Exportée",
}

# Ordre d'affichage : ce qui reste à faire d'abord.
_STATUS_ORDER = {status: i for i, status in enumerate(STATUSES)}

_DATE_FORMATS = (
    "%Y-%m-%d %H:%M:%S",
    "%Y-%m-%dT%H:%M:%S",
    "%Y-%m-%d",
    "%d/%m/%Y",
)


def parse_date(value: Any) -> Optional[datetime]:
    """Date de session tolérante à la saisie (ISO, `JJ/MM/AAAA`, avec heure)."""
    if value is None:
        return None
    if isinstance(value, datetime):
        return value
    text = str(value).strip()
    if not text:
        return None
    for candidate in (text, text[:19].replace("T", " "), text[:10]):
        for fmt in _DATE_FORMATS:
            try:
                return datetime.strptime(candidate, fmt)
            except ValueError:
                continue
    try:
        return datetime.fromisoformat(text)
    except ValueError:
        return None


def date_iso(value: Optional[datetime]) -> str:
    if value is None:
        return ""
    return value.isoformat(sep=" ", timespec="seconds")


def status_label(status: Optional[str]) -> str:
    return STATUS_LABELS.get((status or "").strip(), STATUS_LABELS[STATUS_PLANNED])


def _require(text: Any, field_label: str) -> str:
    value = str(text or "").strip()
    if not value:
        raise ValueError(f"{field_label} est obligatoire")
    return value


def create_session(
    db: Session,
    *,
    name: str,
    site: str,
    session_date: Any,
    operator: Optional[str] = None,
    notes: Optional[str] = None,
    status: str = STATUS_PLANNED,
    left_media_id: Optional[str] = None,
    right_media_id: Optional[str] = None,
    session_id: Optional[str] = None,
) -> CaptureSession:
    """Crée une session. **Site et date sont obligatoires** (règle produit).

    Sans lieu ni date, une observation n'est pas exploitable en écologie et ne
    peut être ni retrouvée ni publiée : la contrainte est posée ici, à
    l'écriture, pas seulement dans le formulaire.
    """
    site = _require(site, "Le lieu (site)")
    when = parse_date(session_date)
    if when is None:
        raise ValueError("La date de la session est obligatoire (AAAA-MM-JJ)")
    if status not in STATUSES:
        raise ValueError(f"Statut inconnu : {status}")
    label = str(name or "").strip() or f"{site} - {when:%Y-%m-%d}"

    row = CaptureSession(
        id=session_id or str(uuid.uuid4()),
        name=label,
        site=site,
        session_date=when,
        operator=(operator or "").strip() or None,
        status=status,
        left_media_id=left_media_id,
        right_media_id=right_media_id,
        notes=(notes or "").strip() or None,
    )
    db.add(row)
    db.flush()
    return row


def update_session(db: Session, session_id: str, **fields: Any) -> Optional[CaptureSession]:
    """Met à jour une session. Un champ absent n'est pas touché."""
    row = db.get(CaptureSession, session_id)
    if row is None:
        return None
    if "name" in fields and str(fields["name"] or "").strip():
        row.name = str(fields["name"]).strip()
    if "site" in fields:
        row.site = _require(fields["site"], "Le lieu (site)")
    if "session_date" in fields:
        when = parse_date(fields["session_date"])
        if when is None:
            raise ValueError("La date de la session est obligatoire (AAAA-MM-JJ)")
        row.session_date = when
    if "operator" in fields:
        row.operator = str(fields["operator"] or "").strip() or None
    if "notes" in fields:
        row.notes = str(fields["notes"] or "").strip() or None
    if "status" in fields and fields["status"]:
        status = str(fields["status"])
        if status not in STATUSES:
            raise ValueError(f"Statut inconnu : {status}")
        row.status = status
    for key in ("calibration_profile", "calibration_sha256", "calibration_id",
                "frame_offset"):
        if key in fields:
            setattr(row, key, fields[key])
    # Rattacher un média passe par `attach_media_pair` : c'est là que vit la
    # garde d'unicité média↔session, on ne la contourne pas par un update.
    for key in ("left_media_id", "right_media_id"):
        if key in fields:
            assert_media_free(
                db, fields[key], session_id=row.id,
                role="vidéo gauche" if key.startswith("left") else "vidéo droite",
            )
            setattr(row, key, fields[key])
    row.updated_at = datetime.utcnow()
    db.flush()
    return row


class MediaAlreadyAttachedError(ValueError):
    """Un média est déjà rattaché à une autre session.

    Décision superviseur (2026-08-17) : un même média n'appartient qu'à **une
    seule** session. Sans cette règle, le même fichier pouvait être annoté sous
    deux sessions aux offsets de synchro différents - les annotations des deux
    univers désignant alors des images différentes du même film.
    """


def _media_label(db: Session, media_id: str) -> str:
    media = db.get(MediaAsset, media_id)
    return Path(media.rel_path).name if media is not None else media_id


def assert_media_free(
    db: Session,
    media_id: Optional[str],
    *,
    session_id: str,
    role: str = "vidéo",
) -> None:
    """Refuse d'attacher un média déjà pris par une autre session."""
    if not media_id:
        return
    owner = find_session_for_media(db, media_id)
    if owner is None or owner.id == session_id:
        return
    raise MediaAlreadyAttachedError(
        f"La {role} « {_media_label(db, media_id)} » appartient déjà à la "
        f"session « {owner.name} » ({owner.site}, {date_iso(owner.session_date)[:10]}). "
        "Un même média ne peut appartenir qu'à une seule session : ouvrez "
        "cette session-là, ou détachez-en la vidéo avant."
    )


def media_pairs(db: Session, session_id: str) -> List[SessionMediaPair]:
    """Prises d'une session dans leur ordre d'ajout."""
    return list(db.scalars(
        select(SessionMediaPair)
        .where(SessionMediaPair.session_id == session_id)
        .order_by(SessionMediaPair.position, SessionMediaPair.created_at)
    ))


def session_media_ids(db: Session, session_or_id: CaptureSession | str) -> List[str]:
    """Tous les médias d'une session, sans doublon et dans l'ordre des prises."""
    session_id = (
        session_or_id.id
        if isinstance(session_or_id, CaptureSession)
        else str(session_or_id)
    )
    out: List[str] = []
    seen: set[str] = set()
    for pair in media_pairs(db, session_id):
        for media_id in (pair.left_media_id, pair.right_media_id):
            if media_id and media_id not in seen:
                seen.add(media_id)
                out.append(media_id)
    # Compatibilité défensive pour une base partiellement migrée.
    row = db.get(CaptureSession, session_id)
    if row is not None:
        for media_id in (row.left_media_id, row.right_media_id):
            if media_id and media_id not in seen:
                seen.add(media_id)
                out.append(media_id)
    return out


def pair_for_media(
    db: Session, media_id: str,
) -> Optional[SessionMediaPair | CaptureSession]:
    """Retourne la prise contenant le média.

    Une ``CaptureSession`` historique est renvoyée en repli lorsque la ligne
    ``session_media_pairs`` n'existe pas encore. Les deux modèles exposent les
    champs de paire utilisés par les appelants ; ce repli garde donc les bases
    et les imports de tests antérieurs à la migration pleinement lisibles.
    """
    if not media_id:
        return None
    pair = db.scalars(
        select(SessionMediaPair)
        .where(or_(
            SessionMediaPair.left_media_id == media_id,
            SessionMediaPair.right_media_id == media_id,
        ))
        .order_by(SessionMediaPair.updated_at.desc())
        .limit(1)
    ).first()
    if pair is not None:
        return pair
    return db.scalars(
        select(CaptureSession)
        .where(or_(
            CaptureSession.left_media_id == media_id,
            CaptureSession.right_media_id == media_id,
        ))
        .order_by(CaptureSession.updated_at.desc())
        .limit(1)
    ).first()


def attach_media_pair(
    db: Session,
    session_id: str,
    *,
    left_media_id: Optional[str],
    right_media_id: Optional[str] = None,
    frame_offset: Optional[int] = None,
    calibration_profile: Optional[str] = None,
    calibration_sha256: Optional[str] = None,
    calibration_id: Optional[str] = None,
    activate: bool = True,
    refreeze_offset: bool = False,
) -> Optional[CaptureSession]:
    """Ajoute une prise à la session et **fige** sa synchro.

    C'est le moment où une session planifiée devient active : les deux médias
    sont connus, la synchro courante est celle qui a servi à les aligner. On la
    grave ici - plus tard, `sync_frames.npy` aura peut-être changé. La
    calibration active est enregistrée au même instant, pour la même raison.

    Lève `MediaAlreadyAttachedError` si l'une des vidéos appartient déjà à une
    autre session : la vérification a lieu **avant** toute écriture, la session
    reste donc intacte en cas de refus.
    """
    row = db.get(CaptureSession, session_id)
    if row is None:
        return None

    assert_media_free(db, left_media_id, session_id=session_id, role="vidéo gauche")
    assert_media_free(db, right_media_id, session_id=session_id, role="vidéo droite")
    if left_media_id and right_media_id and left_media_id == right_media_id:
        raise MediaAlreadyAttachedError(
            "La même vidéo est proposée comme gauche et droite : vérifiez les "
            "deux fichiers chargés."
        )

    existing = None
    for candidate in media_pairs(db, session_id):
        if (
            candidate.left_media_id == left_media_id
            and candidate.right_media_id == right_media_id
        ):
            existing = candidate
            break
    if existing is None:
        current_max = db.scalar(
            select(func.max(SessionMediaPair.position)).where(
                SessionMediaPair.session_id == session_id
            )
        )
        next_position = (int(current_max) + 1) if current_max is not None else 0
        existing = SessionMediaPair(
            id=str(uuid.uuid4()),
            session_id=session_id,
            position=next_position,
            left_media_id=left_media_id,
            right_media_id=right_media_id,
        )
        db.add(existing)
    # Une prise déjà attachée garde la synchro et la calibration figées à sa
    # première attache : rouvrir la paire ne les réécrit pas. Seul un offset
    # donné explicitement (`refreeze_offset`) remplace l'offset figé.
    if frame_offset is not None and (refreeze_offset or existing.frame_offset is None):
        existing.frame_offset = int(frame_offset)
    calibration_written = calibration_id is not None and existing.calibration_id is None
    if calibration_profile is not None and existing.calibration_profile is None:
        existing.calibration_profile = calibration_profile
    if calibration_sha256 is not None and existing.calibration_sha256 is None:
        existing.calibration_sha256 = calibration_sha256
    if calibration_written:
        existing.calibration_id = calibration_id
    existing.updated_at = datetime.utcnow()

    # Pointeur compatible vers la prise courante.
    _point_legacy_to(row, existing)
    if calibration_written:
        for media_id in (left_media_id, right_media_id):
            media = db.get(MediaAsset, media_id) if media_id else None
            if media is not None:
                media.calibration_id = calibration_id
    if activate and left_media_id and row.status == STATUS_PLANNED:
        row.status = STATUS_ACTIVE
    row.updated_at = datetime.utcnow()
    db.flush()
    return row


def _point_legacy_to(row: CaptureSession, pair: Optional[SessionMediaPair]) -> None:
    """Recopie une prise dans les anciennes colonnes de `sessions`."""
    row.left_media_id = pair.left_media_id if pair else None
    row.right_media_id = pair.right_media_id if pair else None
    row.frame_offset = pair.frame_offset if pair else None
    row.calibration_profile = pair.calibration_profile if pair else None
    row.calibration_sha256 = pair.calibration_sha256 if pair else None
    row.calibration_id = pair.calibration_id if pair else None
    row.updated_at = datetime.utcnow()


def replace_pair_media(
    db: Session,
    pair: SessionMediaPair,
    *,
    left_media_id: Optional[str] = None,
    right_media_id: Optional[str] = None,
    frame_offset: Optional[int] = None,
) -> CaptureSession:
    """Change une vidéo d'une prise et pose son offset (None si inconnu).

    L'ancien offset décrivait l'ancienne paire : il n'est jamais gardé.
    """
    if left_media_id:
        pair.left_media_id = left_media_id
    if right_media_id:
        pair.right_media_id = right_media_id
    pair.frame_offset = None if frame_offset is None else int(frame_offset)
    pair.updated_at = datetime.utcnow()
    row = db.get(CaptureSession, pair.session_id)
    _point_legacy_to(row, pair)
    db.flush()
    return row


def _stamp_media(db: Session, row: CaptureSession, media_ids: tuple) -> None:
    """Recopie lieu, titre, notes et date de la session sur ses médias."""
    for media_id in media_ids:
        media = db.get(MediaAsset, media_id) if media_id else None
        if media is not None:
            media.site = row.site
            media.session_title = row.name
            media.notes = row.notes
            media.session_date = row.session_date


def move_media_pair(
    db: Session, pair_id: str, target_session_id: str,
) -> Optional[CaptureSession]:
    """Déplace une prise, avec sa synchro figée, vers une autre session.

    Les annotations suivent d'elles-mêmes : elles sont liées aux médias. Les
    pointeurs de compatibilité des deux sessions sont recalés sur leur
    dernière prise, sinon la migration recréerait la prise partie.
    """
    pair = db.get(SessionMediaPair, pair_id)
    target = db.get(CaptureSession, target_session_id)
    if pair is None or target is None:
        return None
    if pair.session_id == target.id:
        return target
    source = db.get(CaptureSession, pair.session_id)
    current_max = db.scalar(
        select(func.max(SessionMediaPair.position)).where(
            SessionMediaPair.session_id == target.id
        )
    )
    pair.session_id = target.id
    pair.position = (int(current_max) + 1) if current_max is not None else 0
    pair.updated_at = datetime.utcnow()
    db.flush()
    for row in (source, target):
        if row is not None:
            remaining = media_pairs(db, row.id)
            _point_legacy_to(row, remaining[-1] if remaining else None)
    if pair.left_media_id and target.status == STATUS_PLANNED:
        target.status = STATUS_ACTIVE
    # L'export d'abondance lit lieu et date sur les médias.
    _stamp_media(db, target, (pair.left_media_id, pair.right_media_id))
    db.flush()
    return target


def find_session_for_media(db: Session, media_id: str) -> Optional[CaptureSession]:
    """Session portant ce média dans l'une de ses prises."""
    if not media_id:
        return None
    pair = pair_for_media(db, media_id)
    if pair is not None:
        if isinstance(pair, CaptureSession):
            return pair
        return db.get(CaptureSession, pair.session_id)
    # Base ancienne ou migration interrompue : ne rend pas les données
    # inaccessibles.
    return db.scalars(
        select(CaptureSession).where(or_(
            CaptureSession.left_media_id == media_id,
            CaptureSession.right_media_id == media_id,
        )).order_by(CaptureSession.updated_at.desc()).limit(1)
    ).first()


def frame_offset_for_media(db: Session, media_id: Optional[str]) -> Optional[int]:
    """Offset de synchro **figé** pour ce média, ou None s'il n'y en a pas.

    None ne veut pas dire « zéro » : l'appelant doit alors retomber sur
    `frame_ref.timeline_offset()` (le `sync_frames.npy` courant) et signaler
    l'approximation, comme avant l'existence des sessions.
    """
    if not media_id:
        return None
    pair = pair_for_media(db, media_id)
    if pair is not None and pair.frame_offset is not None:
        return int(pair.frame_offset)
    row = find_session_for_media(db, media_id)
    return None if row is None or row.frame_offset is None else int(row.frame_offset)


def _media_summary(db: Session, media_id: Optional[str]) -> Dict[str, Any]:
    """Chemin et disponibilité d'un média : « la vidéo est-elle accessible ? »."""
    empty = {
        "media_id": "",
        "name": "",
        "path": "",
        "sha256": "",
        "available": False,
    }
    if not media_id:
        return empty
    media = db.get(MediaAsset, media_id)
    if media is None:
        return empty
    from .projects import resolve_media_path

    try:
        path = resolve_media_path(media)
    except OSError:
        path = Path(media.rel_path)
    return {
        "media_id": media.id,
        "name": Path(media.rel_path).name,
        "path": str(path),
        "sha256": media.sha256 or "",
        "available": path.is_file(),
    }


def session_as_dict(db: Session, row: CaptureSession, *, with_stats: bool = True) -> Dict[str, Any]:
    """Ligne de la page Sessions : identité, fichiers, effectifs annotés."""
    pair_rows = media_pairs(db, row.id)
    pairs: List[Dict[str, Any]] = []
    for pair in pair_rows:
        pair_left = _media_summary(db, pair.left_media_id)
        pair_right = _media_summary(db, pair.right_media_id)
        pairs.append({
            "pair_id": pair.id,
            "position": int(pair.position),
            "left_media_id": pair.left_media_id or "",
            "right_media_id": pair.right_media_id or "",
            "left": pair_left,
            "right": pair_right,
            "left_path": pair_left["path"],
            "right_path": pair_right["path"],
            "left_available": pair_left["available"],
            "right_available": pair_right["available"],
            "frame_offset": pair.frame_offset,
            "calibration_profile": pair.calibration_profile or "",
            "calibration_sha256": pair.calibration_sha256 or "",
            "calibration_id": pair.calibration_id or "",
        })
    current = pairs[-1] if pairs else None
    left = current["left"] if current else _media_summary(db, row.left_media_id)
    right = current["right"] if current else _media_summary(db, row.right_media_id)
    out: Dict[str, Any] = {
        "session_id": row.id,
        "name": row.name,
        "site": row.site,
        "session_date": date_iso(row.session_date),
        "operator": row.operator or "",
        "status": row.status,
        "status_label": status_label(row.status),
        "status_order": _STATUS_ORDER.get(row.status, len(STATUSES)),
        "notes": row.notes or "",
        "left_media_id": (current or {}).get("left_media_id", row.left_media_id or ""),
        "right_media_id": (current or {}).get("right_media_id", row.right_media_id or ""),
        "left": left,
        "right": right,
        "left_path": left["path"],
        "right_path": right["path"],
        "left_available": left["available"],
        "right_available": right["available"],
        "has_pair": bool(pairs or row.left_media_id or row.right_media_id),
        "pair_count": len(pairs) if pairs else int(bool(row.left_media_id or row.right_media_id)),
        "media_count": len(session_media_ids(db, row)),
        "pairs": pairs,
        "calibration_profile": row.calibration_profile or "",
        "calibration_sha256": row.calibration_sha256 or "",
        "calibration_id": row.calibration_id or "",
        "frame_offset": row.frame_offset,
        "observation_count": 0,
        "measurement_count": 0,
        "event_count": 0,
    }
    if with_stats:
        out.update(session_counts(db, row))
    return out


def session_counts(db: Session, row: CaptureSession) -> Dict[str, Any]:
    """Effectifs annotés cumulés sur toutes les prises de la session."""
    media_ids = session_media_ids(db, row)
    if not media_ids:
        return {
            "observation_count": 0, "measurement_count": 0,
            "event_count": 0, "track_count": 0, "max_n": 0,
            "species_summary": [],
        }

    observations = db.scalar(
        select(func.count(SpatialAnnotation.id))
        .where(SpatialAnnotation.media_id.in_(media_ids))
    ) or 0
    measurements = db.scalar(
        select(func.count(SpatialAnnotation.id))
        .where(
            SpatialAnnotation.media_id.in_(media_ids),
            SpatialAnnotation.measurement_mm.isnot(None),
        )
    ) or 0
    events = db.scalar(
        select(func.count(TemporalEvent.id))
        .join(Track, TemporalEvent.track_id == Track.id)
        .where(Track.media_id.in_(media_ids))
    ) or 0
    point_events = db.scalar(select(func.count()).select_from(SpatialBehaviorFlag)
        .join(SpatialAnnotation, SpatialBehaviorFlag.spatial_annotation_id == SpatialAnnotation.id)
        .where(SpatialAnnotation.media_id.in_(media_ids))) or 0
    tracks = db.scalar(
        select(func.count(Track.id)).where(Track.media_id.in_(media_ids))
    ) or 0
    # Même définition que la barre de comptage de Mesure : seules les images
    # validées contribuent. Le nombre de fiches ou de pistes n'est pas un MaxN.
    max_n = db.scalar(select(func.max(func.coalesce(
        FrameAbundance.manual_count, FrameAbundance.ai_count,
    ))).where(FrameAbundance.media_id.in_(media_ids), FrameAbundance.validated.is_(True)))
    species_rows = db.execute(
        select(
            TaxonNode.id,
            TaxonNode.scientific_name,
            func.count(SpatialAnnotation.id),
        )
        .join(SpatialAnnotation, SpatialAnnotation.taxon_node_id == TaxonNode.id)
        .where(
            SpatialAnnotation.media_id.in_(media_ids),
            TaxonNode.rank == "species",
            authoritative_identification_clause(SpatialAnnotation),
        )
        .group_by(TaxonNode.id, TaxonNode.scientific_name)
        .order_by(func.count(SpatialAnnotation.id).desc(), TaxonNode.scientific_name)
    ).all()
    return {
        "observation_count": int(observations),
        "measurement_count": int(measurements),
        "event_count": int(events + point_events),
        "track_count": int(tracks),
        "max_n": int(max_n) if max_n is not None else 0,
        "species_summary": [
            {"taxon_id": taxon_id, "name": name, "count": int(count)}
            for taxon_id, name, count in species_rows
        ],
    }


def list_sessions(
    db: Session,
    *,
    status: Optional[str] = None,
    site: Optional[str] = None,
    with_stats: bool = True,
) -> List[Dict[str, Any]]:
    """Sessions triées par statut (à venir, en cours, terminées) puis date."""
    stmt = select(CaptureSession)
    if status:
        stmt = stmt.where(CaptureSession.status == status)
    if site:
        stmt = stmt.where(CaptureSession.site == site)
    rows = list(db.scalars(stmt))
    out = [session_as_dict(db, row, with_stats=with_stats) for row in rows]
    out.sort(key=lambda r: (r["status_order"], r["session_date"] or "", r["name"]))
    return out


def delete_session(db: Session, session_id: str) -> bool:
    """Retire une session **planifiée** créée par erreur.

    Refuse dès qu'une vidéo est attachée : la session est alors le seul endroit
    où le lien de paire et l'offset de synchro sont enregistrés. Les médias et
    les annotations, eux, ne sont jamais supprimés.
    """
    row = db.get(CaptureSession, session_id)
    if row is None:
        return False
    if session_media_ids(db, row):
        raise ValueError(
            "Session avec vidéos attachées : marquez-la terminée plutôt que "
            "de la supprimer"
        )
    db.delete(row)
    db.flush()
    return True
