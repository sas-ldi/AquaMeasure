"""Noyau d'export : l'unique chemin base → dataset.

COCO et YOLO partageaient jusqu'ici deux implémentations parallèles qui
divergeaient (split aléatoire par frame d'un côté, aucun split de l'autre ;
`ignore` d'un côté, suppression pure de l'autre). Ce module est désormais le
**seul** chemin qui va de SQLite à un dossier de dataset :

1. collecte des frames annotées (`export_media.collect_export_frames`, index
   absolu, exclusions motivées) ;
2. décimation temporelle facultative (`frame_stride`) ;
3. **class-map construite depuis les données** (jamais depuis le rang complet),
   avec repli sur le rang parent sous les seuils, puis `ignore` ;
4. **split par groupe** (média / session / site) avec assertion anti-fuite
   bloquante, stratification gloutonne et règle NA (val et test intégralement
   identifiés) ;
5. écriture COCO (deux jeux de catégories, mêmes images et mêmes ids),
   `splits.json`, dérivé YOLO éventuel, `croissant.json` (descripteur MLCommons
   lisible par les catalogues de datasets), puis `manifest.json` scellant le
   tout.

Le dossier produit s'appelle `<nom>_v<semver>_<AAAAMMJJ>_<hash8>` où `hash8`
est le début de `content_sha256`, condensé du manifeste *avant* qu'il ne
connaisse son propre emplacement.

**Ce que la reproductibilité garantit - et ce qu'elle ne garantit pas.** Le
contrat porte sur `content_sha256` et sur les empreintes fichier par fichier
(`manifest.files[]`) : deux exports du **même fichier de base**, avec la même
graine, la même version et la même date figée, les donnent identiques. Il ne
porte **pas** sur `source.db_snapshot_sha256` : une base vivante change à
chaque annotation - et l'export y écrit lui-même sa ligne `export_runs`, si
bien que deux exports successifs de la même base *en cours d'usage* ont
légitimement deux empreintes de base différentes. Aucun chemin absolu n'entre
dans un fichier haché (calibration, `splits.json` rejoué) : le même contenu
donne le même condensé d'un poste à l'autre.
"""

from __future__ import annotations

import hashlib
import json
import logging
import os
import random
import re
import shutil
import uuid
from collections import Counter, defaultdict
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Sequence, Set, Tuple

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from . import croissant, datapackage, export_behavior, export_tracking
from . import sessions as sessions_mod
from .export_media import (
    REASON_FRAME_NON_IDENTIFIEE,
    REASON_GEOMETRIE_ILLISIBLE,
    REASON_HORS_RANG,
    ExportExclusions,
    ExportFrame,
    collect_export_frames,
    export_image_space_summary,
    git_commit,
    materialize_frame_image,
)
from .identification import is_authoritative_identification, resolve_track_taxa
from .models import (
    Calibration,
    EXPORT_STATUS_COMPLETED,
    EXPORT_STATUS_FAILED,
    EXPORT_STATUS_RUNNING,
    CaptureSession,
    ExportRun,
    MediaAsset,
    SpatialAnnotation,
    Track,
)
from .rectify import IMAGE_SPACE_RAW, load_left_rectifier
from .spatial import bbox_from_geometry, geometry_space, parse_geometry
from .taxonomy import resolve_to_rank, resolve_to_rank_name

log = logging.getLogger(__name__)


def _frame_rectifier(
    session: Session,
    frame: ExportFrame,
    *,
    enabled: bool,
    default_rectifier: Any,
    cache: Dict[tuple, Any],
    assume_rectified: bool = False,
):
    """Rectifieur de la prise qui a produit cette frame, jamais celui d'une autre.

    Une session peut maintenant contenir plusieurs paires et chaque paire peut
    avoir sa propre calibration. Les annotations récentes déclarent aussi leur
    espace : une boîte brute ne doit surtout pas être appliquée à une image
    rectifiée.
    """
    if not enabled:
        return None
    if not assume_rectified:
        spaces = set()
        for annotation in frame.annotations:
            try:
                spaces.add(geometry_space(parse_geometry(annotation)))
            except (TypeError, ValueError, KeyError, json.JSONDecodeError):
                continue
        if spaces and spaces == {IMAGE_SPACE_RAW}:
            return None

    media = session.get(MediaAsset, frame.media_id)
    pair = sessions_mod.pair_for_media(session, frame.media_id)
    if pair is not None and pair.right_media_id == frame.media_id:
        # Le transform disponible est celui de la vue gauche. Appliquer ses
        # cartes à droite fabriquerait une image plausible mais fausse.
        return None
    calibration_id = (
        (media.calibration_id if media is not None else None)
        or (pair.calibration_id if pair is not None else None)
    )
    calibration = session.get(Calibration, calibration_id) if calibration_id else None
    profile = (
        (calibration.profile_name if calibration is not None else None)
        or (pair.calibration_profile if pair is not None else None)
    )
    expected_sha = (
        (calibration.sha256 if calibration is not None else None)
        or (pair.calibration_sha256 if pair is not None else None)
        or ""
    )
    if not profile:
        return default_rectifier
    key = (str(profile), str(expected_sha))
    if key not in cache:
        candidate = load_left_rectifier(profile=str(profile))
        actual_sha = str(
            getattr(candidate, "calibration_sha256", "") or ""
        ) if candidate is not None else ""
        if expected_sha and actual_sha != str(expected_sha):
            log.warning(
                "Calibration %s indisponible ou modifiée pour le média %s ; "
                "la frame reste brute.", profile, frame.media_id,
            )
            candidate = None
        cache[key] = candidate
    return cache[key]

# ── Vocabulaire ────────────────────────────────────────────────────────────

SPLIT_TRAIN = "train"
SPLIT_VAL = "val"
SPLIT_TEST = "test"
SPLIT_NAMES: Tuple[str, ...] = (SPLIT_TRAIN, SPLIT_VAL, SPLIT_TEST)

SPLIT_BY_MEDIA = "media"
SPLIT_BY_SESSION = "session"
SPLIT_BY_SITE = "site"
SPLIT_BY_VALUES: Tuple[str, ...] = (SPLIT_BY_MEDIA, SPLIT_BY_SESSION, SPLIT_BY_SITE)

RANK_FISH = "fish"
FISH_CLASS = "fish"
TAXONOMY_RANKS: Tuple[str, ...] = (RANK_FISH, "family", "genus", "species")

# Repli d'un rang trop peu peuplé vers son parent. `family` n'a pas de parent
# exploitable : sous le seuil, il ne reste que l'`ignore`.
PARENT_RANK: Dict[str, Optional[str]] = {
    "species": "genus",
    "genus": "family",
    "family": None,
}

FORMAT_COCO = "coco"
FORMAT_YOLO = "yolo"
# Suivi (phase 3). COCO-VID est au suivi ce que COCO est à la détection : le
# pivot. MOTChallenge en est **dérivé**, jamais relu depuis la base - c'est la
# règle qui a évité à COCO et YOLO de diverger.
FORMAT_COCO_VID = "coco_vid"
FORMAT_MOT = "mot"
# Comportement (phase 3). `ava` produit aussi le JSONL d'intervalles : les deux
# décrivent les mêmes événements, les séparer ferait deux vérités.
FORMAT_AVA = "ava"
FORMAT_EVENTS_JSONL = "events_jsonl"

# Familles de formats - elles décident du pipeline, pas seulement du writer :
# la détection part des annotations humaines, le suivi des `track_samples`, le
# comportement des `temporal_events` (et n'a besoin d'aucune image).
IMAGE_FORMATS: Tuple[str, ...] = (FORMAT_COCO, FORMAT_YOLO)
TRACKING_FORMATS: Tuple[str, ...] = (FORMAT_COCO_VID, FORMAT_MOT)
BEHAVIOR_FORMATS: Tuple[str, ...] = (FORMAT_AVA, FORMAT_EVENTS_JSONL)
SUPPORTED_FORMATS: Tuple[str, ...] = IMAGE_FORMATS + TRACKING_FORMATS + BEHAVIOR_FORMATS

# Poisson vu mais non identifiable à ce rang. Convention CrowdHuman/TAO :
# ignore=1 + iscrowd=1, donc exclu de la perte et de l'évaluation.
UNIDENTIFIED_CATEGORY_NAME = "unidentified"

DEFAULT_RATIOS: Dict[str, float] = {SPLIT_TRAIN: 0.70, SPLIT_VAL: 0.15, SPLIT_TEST: 0.15}
DEFAULT_SEED = 42
DEFAULT_MIN_INSTANCES = 30
DEFAULT_MIN_MEDIA = 2
DEFAULT_DATASET_NAME = "fishvision"
DEFAULT_DATASET_VERSION = "1.0.0"

# Part maximale de médias auxquels la métadonnée de groupe demandée manque.
# Au-delà, `split_by='site'` ou `'session'` ne décrit plus le découpage réel :
# l'export s'arrête plutôt que d'annoncer un grain qu'il n'applique pas.
DEFAULT_STRICT_GROUPING = 0.5

# Un manifeste doit rester lisible : au-delà, on renvoie vers splits.json.
_GROUP_SAMPLE = 50

# Nom et version entrent dans le nom du dossier : un `/` ou un `..` y créerait
# un export ailleurs que sous la racine demandée.
_SAFE_TOKEN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._+-]*$")

MANIFEST_NAME = "manifest.json"
SPLITS_NAME = "splits.json"
REPORT_NAME = "export_report.json"
# Descripteur Frictionless des tables CSV d'un export scelle.
DATAPACKAGE_NAME = "datapackage.json"
# Descripteur Croissant (MLCommons) - ecrit a la racine de tout export scelle.
CROISSANT_NAME = croissant.CROISSANT_NAME
MANIFEST_SCHEMA = "fishvision/export-manifest/1"


class ExportIntegrityError(RuntimeError):
    """Le dataset produit ne respecterait pas une règle non négociable.

    Deux cas : un **groupe partagé entre deux splits** (fuite de données, la
    faute qui gonfle les métriques de 20 à 40 points), ou un `splits.json`
    rejoué incohérent avec la stratégie demandée.
    """


# ── Petits utilitaires ─────────────────────────────────────────────────────


def canonical_json(payload: Any) -> bytes:
    """Sérialisation stable : c'est elle qui rend les empreintes comparables."""
    return json.dumps(
        payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"),
    ).encode("utf-8")


def sha256_file(path: Path, *, chunk: int = 1 << 20) -> str:
    digest = hashlib.sha256()
    with Path(path).open("rb") as handle:
        for block in iter(lambda: handle.read(chunk), b""):
            digest.update(block)
    return digest.hexdigest()


def _write_json(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8",
    )


def _database_path(session: Session) -> Optional[Path]:
    """Fichier SQLite derrière la session, ou None (base en mémoire)."""
    bind = session.get_bind()
    url = getattr(bind, "url", None)
    database = getattr(url, "database", None)
    if not database:
        return None
    path = Path(database)
    return path if path.is_file() else None


def wal_checkpoint(db_path: Path) -> bool:
    """Rabat le journal WAL dans le fichier `.db`, avant de le hacher.

    La base tourne en `journal_mode=WAL` : tant qu'aucun checkpoint n'a eu
    lieu, les commits vivent dans `<base>.db-wal` et **pas** dans le `.db`.
    Hacher le `.db` seul revient alors à sceller un état antérieur aux
    annotations qu'on exporte - l'empreinte prétend décrire une base qu'elle ne
    décrit pas.

    Renvoie `True` si le journal a bien été rabattu. En cas d'échec (base
    verrouillée par un autre lecteur, disque protégé), on log et on renvoie
    `False` : l'export continue, mais le manifeste le dit
    (`db_snapshot_wal_checkpointed`).
    """
    import sqlite3

    try:
        conn = sqlite3.connect(str(db_path), timeout=5.0)
    except sqlite3.Error as exc:
        log.warning(
            "Checkpoint WAL impossible sur %s (%s) - l'empreinte de base peut "
            "ignorer des commits non rabattus.", db_path, exc,
        )
        return False
    try:
        row = conn.execute("PRAGMA wal_checkpoint(TRUNCATE)").fetchone()
        conn.commit()
    except sqlite3.Error as exc:
        log.warning(
            "Checkpoint WAL refusé sur %s (%s) - l'empreinte de base peut "
            "ignorer des commits non rabattus.", db_path, exc,
        )
        return False
    finally:
        conn.close()
    # (busy, log_frames, checkpointed_frames) : busy != 0 = journal partiellement
    # rabattu, donc empreinte incomplète. On ne l'annonce pas comme complète.
    if row is not None and row[0]:
        log.warning(
            "Checkpoint WAL partiel sur %s (base occupée) - l'empreinte de base "
            "peut ignorer des commits non rabattus.", db_path,
        )
        return False
    return True


def _link_or_copy(src: Path, dst: Path) -> None:
    """Lien matériel si le système le permet, copie sinon.

    Le dérivé YOLO partage les images de l'export COCO : les dupliquer
    doublerait la taille du dataset sans rien apporter.
    """
    dst.parent.mkdir(parents=True, exist_ok=True)
    if dst.exists():
        dst.unlink()
    try:
        os.link(src, dst)
    except (OSError, NotImplementedError, AttributeError):
        shutil.copy2(src, dst)


def _portable_space_summary(summary: Dict[str, Any]) -> Dict[str, Any]:
    """Récapitulatif d'espace image sans chemin absolu.

    `calibration_dir` désigne un dossier de la machine qui a produit l'export :
    l'inscrire dans un fichier haché rendrait deux exports identiques
    différents d'un poste à l'autre. Le nom de profil et le sha256 des `.npy`
    identifient la calibration sans rien révéler de l'arborescence locale.
    """
    out = dict(summary or {})
    calib = out.get("calibration")
    if isinstance(calib, dict):
        out["calibration"] = {k: v for k, v in calib.items() if k != "calibration_dir"}
    return out


def _portable_frame_meta(meta: Dict[str, Any]) -> Dict[str, Any]:
    """Provenance d'une image : profil et empreinte, sans chemin ni verbiage.

    Le détail fichier par fichier de la calibration figure **une fois** dans
    `coordinate_frame` ; le répéter sur chacune des 1600 images alourdirait le
    JSON sans rien apprendre.
    """
    out = dict(meta)
    calib = out.get("calibration")
    if isinstance(calib, dict):
        out["calibration"] = {
            key: calib[key]
            for key in ("profile_name", "calibration_sha256")
            if calib.get(key)
        } or None
    return out


def _normalized_ratios(ratios: Optional[Dict[str, float]]) -> Dict[str, float]:
    values = dict(DEFAULT_RATIOS) if not ratios else {
        name: float(ratios.get(name, 0.0)) for name in SPLIT_NAMES
    }
    total = sum(values.values())
    if total <= 0:
        raise ValueError("Les ratios de split doivent être strictement positifs")
    return {name: values[name] / total for name in SPLIT_NAMES}


# ── Classes : construites depuis les annotations réellement exportées ──────


def _has_authoritative_identification(
    ann: Any, session: Session | None = None,
) -> bool:
    """Vrai uniquement pour une identification taxonomique relue.

    Un statut explicite est autoritaire. Le repli par ``source`` ne concerne
    que les lignes legacy dont le statut est réellement NULL ; il reprend la
    sémantique historique de la migration (validation, saisie manuelle ou
    CVAT avec un taxon non générique), sans promouvoir une proposition moderne
    explicitement ``unreviewed``.
    """
    return is_authoritative_identification(ann)


def _class_at_rank(session: Session, ann: Any, rank: str) -> Optional[str]:
    """Nom du taxon au rang demandé, ou None si hors rang.

    `ann` est indifféremment une `SpatialAnnotation` ou une `Track` : seul
    `taxon_node_id` est lu. C'est ce qui permet au suivi de partager la
    class-map du noyau au lieu d'en inventer une deuxième.
    """
    if rank == RANK_FISH:
        return FISH_CLASS
    if not _has_authoritative_identification(ann, session):
        return None
    node_id = ann.taxon_node_id
    if not node_id:
        return None
    if resolve_to_rank(session, node_id, rank) is None:
        return None
    name = resolve_to_rank_name(session, node_id, rank)
    return name or None


def _class_up_the_ladder(
    session: Session, ann: Any, from_rank: str,
) -> Tuple[Optional[str], Optional[str]]:
    """Premier rang parent où le taxon de l'annotation existe encore.

    On ne s'arrête pas au parent immédiat : une espèce rattachée directement à
    sa famille (sans genre intermédiaire) doit se replier sur la famille, pas
    tomber en `ignore` parce que le rang genre lui manque.
    """
    rank = PARENT_RANK.get(from_rank)
    while rank:
        name = _class_at_rank(session, ann, rank)
        if name:
            return name, rank
        rank = PARENT_RANK.get(rank)
    return None, None


@dataclass
class ClassPlan:
    """Class-map issue des données, seuils appliqués, replis tracés."""

    rank: str
    min_instances: int
    min_media: int
    # Ce que les effectifs comptent : des boîtes (détection) ou des pistes
    # (suivi). Le manifeste doit le dire, sinon « 12 instances » est ambigu.
    source: str = "annotations_exportees"
    names: List[str] = field(default_factory=list)
    # annotation_id -> nom de classe, ou None quand elle sort en `ignore`.
    class_of_annotation: Dict[str, Optional[str]] = field(default_factory=dict)
    rank_of_annotation: Dict[str, Optional[str]] = field(default_factory=dict)
    stats: Dict[str, Dict[str, int]] = field(default_factory=dict)
    fallbacks: List[Dict[str, Any]] = field(default_factory=list)
    ignored_instances: int = 0

    def index(self, name: str) -> int:
        return self.names.index(name)

    def category_id(self, name: Optional[str]) -> int:
        """Identifiant COCO (1-based) ; la catégorie `ignore` ferme la liste."""
        if name is None:
            return len(self.names) + 1
        return self.names.index(name) + 1

    def as_dict(self) -> Dict[str, Any]:
        return {
            "rank": self.rank,
            "min_instances": self.min_instances,
            "min_media": self.min_media,
            "source": self.source,
            "names": list(self.names),
            "count": len(self.names),
            "stats": {name: dict(self.stats[name]) for name in self.names},
            "fallbacks": list(self.fallbacks),
            "ignored_instances": self.ignored_instances,
        }


def _class_counts(
    pairs: Sequence[Tuple[Any, Any]],
    class_of: Dict[str, Optional[str]],
) -> Dict[str, Dict[str, int]]:
    instances: Counter = Counter()
    images: Dict[str, Set[str]] = defaultdict(set)
    media: Dict[str, Set[str]] = defaultdict(set)
    for frame, ann in pairs:
        name = class_of.get(ann.id)
        if name is None:
            continue
        instances[name] += 1
        images[name].add(frame.key)
        media[name].add(frame.media_id)
    return {
        name: {
            "instances": instances[name],
            "images": len(images[name]),
            "media": len(media[name]),
        }
        for name in instances
    }


def build_class_plan(
    session: Session,
    frames: Sequence[ExportFrame],
    rank: str,
    *,
    min_instances: int = DEFAULT_MIN_INSTANCES,
    min_media: int = DEFAULT_MIN_MEDIA,
) -> ClassPlan:
    """Class-map construite **depuis les annotations exportées**.

    Le rang complet de la taxonomie donnait 780 classes espèces dont 765 sans
    une seule boîte : un modèle entraîné là-dessus apprend surtout à prédire du
    vide. On ne retient donc que ce qui existe dans le dataset, et seulement
    au-dessus des seuils ; en dessous, on remonte d'un rang (`species` → `genus`
    → `family`), et ce qui reste sous le seuil part en `ignore`.
    """
    pairs: List[Tuple[Any, Any]] = [
        (frame, ann) for frame in frames for ann in frame.annotations
    ]
    return _build_class_plan(
        session, pairs, rank, min_instances=min_instances, min_media=min_media,
    )


def build_class_plan_for_tracks(
    session: Session,
    tracks: Sequence[Track],
    rank: str,
    *,
    min_instances: int = DEFAULT_MIN_INSTANCES,
    min_media: int = DEFAULT_MIN_MEDIA,
    resolved_taxa: Optional[Dict[str, Optional[str]]] = None,
) -> ClassPlan:
    """Même class-map, appliquée aux **pistes** plutôt qu'aux boîtes.

    Un dataset de suivi classe des trajectoires : l'unité comptée est donc la
    piste, pas l'échantillon (une piste de 7000 frames ne vaut pas 7000
    exemples d'espèce). Les seuils, le repli sur le rang parent et l'`ignore`
    final sont exactement ceux de la détection - c'est la même règle, appliquée
    à la même taxonomie.

    Sur la base réelle, **aucune** piste ne porte de taxon (0 sur 1685) : au
    rang `fish` tout sort en `fish`, à tout autre rang tout sort en `ignore`.
    C'est la raison pour laquelle le rang `fish` est le défaut du suivi.
    """
    effective_taxa = (
        resolved_taxa
        if resolved_taxa is not None
        else resolve_track_taxa(session, tracks)
    )
    pairs: List[Tuple[Any, Any]] = [
        (
            _TrackAsFrame(key=track.id, media_id=track.media_id),
            _ResolvedTrack(
                id=track.id,
                media_id=track.media_id,
                taxon_node_id=effective_taxa.get(track.id),
                identification_status=(
                    "identified" if effective_taxa.get(track.id) else "ambiguous"
                ),
            ),
        )
        for track in tracks
    ]
    return _build_class_plan(
        session, pairs, rank, min_instances=min_instances, min_media=min_media,
        source="pistes_exportees",
    )


@dataclass(frozen=True)
class _TrackAsFrame:
    """Vue « frame » d'une piste, pour réutiliser `_class_counts` tel quel.

    Compter les images d'une classe n'a pas de sens sur une piste : `key` vaut
    l'identifiant de piste, si bien que `images` compte les pistes. C'est
    volontaire et dit dans le manifeste (`class_stats.source`).
    """

    key: str
    media_id: str


@dataclass(frozen=True)
class _ResolvedTrack:
    """Vue de piste dont le taxon vient uniquement de l'autorité résolue."""

    id: str
    media_id: str
    taxon_node_id: Optional[str]
    identification_status: str
    source: str = "resolved"


def _build_class_plan(
    session: Session,
    pairs: Sequence[Tuple[Any, Any]],
    rank: str,
    *,
    min_instances: int,
    min_media: int,
    source: str = "annotations_exportees",
) -> ClassPlan:
    """Cœur commun aux class-maps de détection et de suivi."""
    plan = ClassPlan(
        rank=rank, min_instances=int(min_instances), min_media=int(min_media),
        source=source,
    )
    ann_by_id = {ann.id: ann for _frame, ann in pairs}

    current: Dict[str, Optional[str]] = {}
    current_rank: Dict[str, Optional[str]] = {}
    for _frame, ann in pairs:
        name = _class_at_rank(session, ann, rank)
        current[ann.id] = name
        current_rank[ann.id] = rank if name else None

    if rank == RANK_FISH:
        # Détecteur mono-classe : un poisson non identifié reste un poisson.
        plan.names = [FISH_CLASS] if pairs else []
        plan.class_of_annotation = current
        plan.rank_of_annotation = current_rank
        plan.stats = _class_counts(pairs, current)
        plan.ignored_instances = 0
        return plan

    demotions: Counter = Counter()
    for _ in range(len(PARENT_RANK) + 1):
        stats = _class_counts(pairs, current)
        weak = {
            name for name, row in stats.items()
            if row["instances"] < plan.min_instances or row["media"] < plan.min_media
        }
        if not weak:
            break
        changed = False
        for ann_id, name in list(current.items()):
            if name is None or name not in weak:
                continue
            from_rank = current_rank[ann_id] or rank
            new_name, new_rank = _class_up_the_ladder(session, ann_by_id[ann_id], from_rank)
            demotions[(name, from_rank, new_name, new_rank)] += 1
            current[ann_id] = new_name
            current_rank[ann_id] = new_rank
            changed = True
        if not changed:
            break

    # Filet de sécurité : ce qui traîne encore sous le seuil part en `ignore`.
    stats = _class_counts(pairs, current)
    weak = {
        name for name, row in stats.items()
        if row["instances"] < plan.min_instances or row["media"] < plan.min_media
    }
    for ann_id, name in list(current.items()):
        if name is not None and name in weak:
            demotions[(name, current_rank[ann_id] or rank, None, None)] += 1
            current[ann_id] = None
            current_rank[ann_id] = None

    plan.class_of_annotation = current
    plan.rank_of_annotation = current_rank
    plan.stats = _class_counts(pairs, current)
    plan.names = sorted(plan.stats)
    plan.ignored_instances = sum(1 for name in current.values() if name is None)
    plan.fallbacks = [
        {
            "from": from_name,
            "from_rank": from_rank,
            "to": to_name,
            "to_rank": to_rank,
            "instances": count,
            "outcome": "ignore" if to_name is None else "repli_rang_parent",
        }
        for (from_name, from_rank, to_name, to_rank), count in sorted(
            demotions.items(), key=lambda item: (item[0][0], item[0][1], str(item[0][2]))
        )
    ]
    return plan


# ── Groupes et split ───────────────────────────────────────────────────────


def suggest_split_by(session: Session) -> str:
    """Grain de split à proposer par défaut dans l'interface.

    `media` sépare les **deux caméras d'une même session stéréo** : le même
    poisson, au même instant, vu de gauche et de droite, part alors de part et
    d'autre du découpage. C'est une fuite de données, et c'était le défaut de
    l'onglet Exports. Dès qu'une session existe en base, le grain sûr est donc
    `session` ; sans aucune session (base de photos importées), il n'y a rien à
    regrouper et `media` reste le grain le plus fin qui tienne.

    Ce n'est qu'un **défaut** : l'opérateur reste libre de choisir `site`, plus
    généralisant encore, ou de revenir à `media` en connaissance de cause.
    """
    try:
        has_session = session.scalar(select(CaptureSession.id).limit(1)) is not None
    except Exception:  # base absente, schéma antérieur : on ne devine pas
        return SPLIT_BY_MEDIA
    return SPLIT_BY_SESSION if has_session else SPLIT_BY_MEDIA


def group_key_for_frame(
    session: Session,
    frame: ExportFrame,
    split_by: str,
    cache: Dict[str, str],
    *,
    degraded: Optional[Dict[str, str]] = None,
) -> str:
    """Groupe indivisible auquel appartient la frame.

    Un média sans session (photo importée, vidéo jamais rattachée) forme son
    propre groupe : c'est le grain le plus fin qui reste sûr, jamais la frame.

    Ce repli n'est **pas** anodin : demander `split_by='site'` et retomber sur
    le média laisse deux vidéos du même site partir de part et d'autre du
    découpage - c'est exactement la corrélation qu'on croyait avoir coupée.
    `degraded` (dict optionnel `media_id → raison`) collecte ces replis pour
    que l'export les compte, les affiche et refuse au-delà d'un seuil, au lieu
    d'annoncer `group_by_site` en promettant ce qu'il n'a pas fait.
    """
    cached = cache.get(frame.media_id)
    if cached is not None:
        return cached

    key = f"media:{frame.media_id}"
    reason: Optional[str] = None
    if split_by != SPLIT_BY_MEDIA:
        capture = sessions_mod.find_session_for_media(session, frame.media_id)
        if split_by == SPLIT_BY_SESSION:
            if capture is not None:
                key = f"session:{capture.id}"
            else:
                reason = "aucune session ne référence ce média"
        elif split_by == SPLIT_BY_SITE:
            site = (capture.site if capture is not None else None) or None
            if not site:
                media = session.get(MediaAsset, frame.media_id)
                site = (getattr(media, "site", None) or "").strip() or None
            if site:
                key = f"site:{site}"
            else:
                reason = "ni la session ni le média ne portent de site"
    cache[frame.media_id] = key
    if reason is not None and degraded is not None:
        degraded[frame.media_id] = reason
    return key


def grouping_degradation(
    split_by: str,
    media_ids: Set[str],
    degraded: Dict[str, str],
    group_of_frame: Dict[str, str],
    *,
    strict_grouping: Optional[float] = DEFAULT_STRICT_GROUPING,
) -> Dict[str, Any]:
    """Bilan des replis de grain, et refus au-delà du seuil `strict_grouping`.

    `strict_grouping` est la **part maximale tolérée** de médias sans la
    métadonnée demandée (0.5 = la moitié). `None` désactive le refus : le bilan
    reste écrit dans le manifeste, mais l'export va au bout.
    """
    total = len(media_ids)
    concerned = {mid: why for mid, why in degraded.items() if mid in media_ids}
    fallback_groups = sorted({
        group for group in group_of_frame.values()
        if group.startswith("media:") and group.split(":", 1)[1] in concerned
    })
    ratio = (len(concerned) / total) if total else 0.0
    info: Dict[str, Any] = {
        "count": len(concerned),
        "media_total": total,
        "ratio": round(ratio, 4),
        "requested_split_by": split_by,
        "fallback_split_by": SPLIT_BY_MEDIA,
        "threshold": strict_grouping,
        "reasons": dict(sorted(Counter(concerned.values()).items())),
        # Listes bornées : le détail complet est reconstituable depuis
        # splits.json (chaque groupe y est nommé).
        "media": sorted(concerned)[:_GROUP_SAMPLE],
        "groups": fallback_groups[:_GROUP_SAMPLE],
    }
    if not concerned:
        return info
    log.warning(
        "%d média(s) sur %d sans la métadonnée '%s' : repli sur le grain média.",
        len(concerned), total, split_by,
    )
    print(
        f"[!] Grain '{split_by}' dégradé : {len(concerned)}/{total} média(s) "
        f"({info['ratio']:.0%}) n'ont pas cette métadonnée et forment leur propre "
        f"groupe. Deux médias du même {split_by} peuvent alors se retrouver de "
        f"part et d'autre du découpage."
    )
    if strict_grouping is not None and ratio > float(strict_grouping):
        raise ExportIntegrityError(
            f"Grain '{split_by}' impossible à tenir : {len(concerned)} média(s) "
            f"sur {total} ({ratio:.0%}) n'ont pas cette métadonnée, au-delà du "
            f"seuil strict_grouping={strict_grouping:.0%}. Renseignez la "
            f"métadonnée, ou exportez avec split_by='media' - au moins le "
            f"manifeste dira la vérité. Médias concernés : "
            f"{', '.join(sorted(concerned)[:10])}"
            + (" …" if len(concerned) > 10 else "")
        )
    return info


@dataclass
class SplitPlan:
    split_by: str
    seed: int
    ratios: Dict[str, float]
    group_of_frame: Dict[str, str] = field(default_factory=dict)
    split_of_group: Dict[str, str] = field(default_factory=dict)
    forced_train_groups: List[str] = field(default_factory=list)
    # `{"file_name": ..., "sha256": ...}` - jamais un chemin : deux rejeux du
    # même fichier depuis deux postes doivent donner le même manifeste.
    replayed_from: Optional[Dict[str, str]] = None
    new_groups: List[str] = field(default_factory=list)
    # Groupes que le rejeu plaçait en val/test et que la règle NA a ramenés en
    # train : le rejeu fige un découpage, il n'autorise pas à rouvrir la fuite.
    na_replay_overrides: List[Dict[str, str]] = field(default_factory=list)

    @property
    def strategy(self) -> str:
        return f"group_by_{self.split_by}"

    def split_of_frame(self, frame_key: str) -> str:
        return self.split_of_group[self.group_of_frame[frame_key]]


def assert_no_group_leak(
    assignments: Iterable[Tuple[str, str]], *, context: str = "",
) -> None:
    """Refuse qu'un groupe se retrouve dans deux splits.

    À 30 images/seconde, deux frames voisines sont le même exemple photographié
    deux fois : un groupe à cheval sur train et val fait fuiter la réponse et
    gonfle les métriques de 20 à 40 points. L'erreur est **bloquante** : mieux
    vaut pas de dataset qu'un dataset qui ment.
    """
    splits_by_group: Dict[str, Set[str]] = defaultdict(set)
    for group, split in assignments:
        splits_by_group[group].add(split)
    leaked = {
        group: sorted(splits) for group, splits in splits_by_group.items()
        if len(splits) > 1
    }
    if not leaked:
        return
    detail = " ; ".join(
        f"{group} → {', '.join(splits)}" for group, splits in sorted(leaked.items())
    )
    where = f" ({context})" if context else ""
    raise ExportIntegrityError(
        f"Fuite entre splits{where} : {len(leaked)} groupe(s) présent(s) dans "
        f"plusieurs splits - {detail}"
    )


def assert_val_test_fully_identified(
    composition: Dict[str, Dict[str, Any]], *, context: str = "",
) -> None:
    """Dernier verrou : aucune boîte `ignore` livrée en val ou en test.

    La règle NA s'applique en amont (au grain du groupe), mais c'est ici qu'on
    vérifie le **résultat écrit**, après matérialisation des images et
    exclusions éventuelles. Une seule boîte non identifiée en test suffit à
    fausser toute l'évaluation : mieux vaut pas de dataset qu'un dataset dont
    on ne peut pas croire les scores.
    """
    faulty = {
        split: int(composition.get(split, {}).get("ignored_annotations", 0) or 0)
        for split in (SPLIT_VAL, SPLIT_TEST)
    }
    faulty = {split: count for split, count in faulty.items() if count}
    if not faulty:
        return
    where = f" ({context})" if context else ""
    detail = ", ".join(
        f"{split} : {count} annotation(s) ignore" for split, count in sorted(faulty.items())
    )
    raise ExportIntegrityError(
        f"Règle NA violée{where} - val et test doivent être intégralement "
        f"identifiés : {detail}. Un poisson non identifié hors de train rend "
        "l'évaluation fausse."
    )


def load_replay_splits(path: Path) -> Dict[str, Any]:
    """Relit un `splits.json` déjà produit, en vérifiant qu'il ne fuit pas.

    Le fichier porte **deux** vues du même découpage : `groups` (groupe →
    split) et `group_ids` (split → groupes). Elles doivent dire la même chose ;
    une contradiction signifie qu'on a édité l'une sans l'autre, et rejouer
    l'une des deux au hasard donnerait un découpage que personne n'a décidé.

    Le fichier est identifié par son **nom et son empreinte**, jamais par son
    chemin : c'est cette identité-là qui entre dans le manifeste.
    """
    source = Path(path)
    payload = json.loads(source.read_text(encoding="utf-8"))
    declared = payload.get("groups") or {}
    if not isinstance(declared, dict):
        declared = {}
    listed: Dict[str, str] = {}
    pairs: List[Tuple[str, str]] = []
    for split in SPLIT_NAMES:
        for group in (payload.get("group_ids") or {}).get(split, []):
            pairs.append((str(group), split))
            listed[str(group)] = split
    if pairs:
        assert_no_group_leak(pairs, context=f"rejeu de {source.name}")

    if declared and listed:
        # `groups` est la vue complète (tout groupe planifié), `group_ids` ne
        # liste que ceux qui ont produit des images : on vérifie l'accord sur
        # l'intersection, et qu'aucun groupe listé n'est inconnu de `groups`.
        contradictions = sorted(
            f"{group} : groups='{declared.get(group, '-')}' vs group_ids='{split}'"
            for group, split in listed.items()
            if str(declared.get(group, "")) != split
        )
        if contradictions:
            raise ExportIntegrityError(
                f"{source.name} : les deux vues du découpage se contredisent sur "
                f"{len(contradictions)} groupe(s) - {' ; '.join(contradictions)}. "
                "Rejouer un fichier incohérent produirait un découpage que "
                "personne n'a décidé."
            )
    groups = declared or listed
    unknown = {split for split in groups.values() if split not in SPLIT_NAMES}
    if unknown:
        raise ExportIntegrityError(
            f"{source.name} : split(s) inconnu(s) {sorted(unknown)}"
        )
    return {
        "split_by": payload.get("split_by"),
        "seed": payload.get("seed"),
        "groups": {str(k): str(v) for k, v in groups.items()},
        # Identité portable du fichier rejoué : un chemin absolu ferait
        # diverger deux manifestes pourtant issus du même découpage.
        "source": {"file_name": source.name, "sha256": sha256_file(source)},
    }


def plan_splits(
    frames: Sequence[ExportFrame],
    group_of_frame: Dict[str, str],
    *,
    split_by: str,
    seed: int,
    ratios: Dict[str, float],
    classes_of_group: Dict[str, Set[str]],
    class_weight: Dict[str, int],
    na_groups: Set[str],
    replay: Optional[Dict[str, Any]] = None,
) -> SplitPlan:
    """Affecte chaque **groupe entier** à un split.

    Trois règles se disputent la place, et l'ordre est tranché - c'est tout
    l'objet de cette fonction :

    1. **Règle NA** - val et test doivent être intégralement identifiés. Tout
       groupe portant au moins une frame à poisson non identifié (ou dont la
       classe est tombée sous le seuil) part entièrement en `train`. Elle prime
       sur l'équilibre **et sur le rejeu** : un `splits.json` d'hier peut très
       bien placer en val un groupe devenu NA depuis (annotation ajoutée,
       seuil de classe changé, rang différent). Le rejeu fige un découpage, il
       n'autorise pas à contaminer le test - le groupe est ramené en train et
       le manifeste le dit (`na_rule.replay_overrides`).
    2. **Rejeu** - pour tous les autres groupes, le découpage fourni s'applique
       tel quel.
    3. **Stratification gloutonne** - les classes rares d'abord, chaque groupe
       allant au split qui en manque le plus, sous contrainte de ratio. Jamais
       en cassant un groupe.
    """
    plan = SplitPlan(split_by=split_by, seed=int(seed), ratios=dict(ratios))
    plan.group_of_frame = dict(group_of_frame)

    images_per_group: Counter = Counter()
    for frame in frames:
        images_per_group[group_of_frame[frame.key]] += 1
    all_groups = sorted(images_per_group)

    # 1. Règle NA - groupes non intégralement identifiés : train, sans appel,
    # et **avant** toute autre affectation.
    for group in all_groups:
        if group in na_groups:
            plan.split_of_group[group] = SPLIT_TRAIN
            plan.forced_train_groups.append(group)

    replayed: Dict[str, str] = {}
    if replay:
        replayed = replay.get("groups") or {}
        if replay.get("split_by") and replay["split_by"] != split_by:
            raise ExportIntegrityError(
                f"Rejeu impossible : le découpage fourni a été produit avec "
                f"split_by='{replay['split_by']}', l'export demande "
                f"'{split_by}'. Rejouer un découpage d'un autre grain ferait "
                f"fuiter des données."
            )
        assert_no_group_leak(
            [(group, split) for group, split in replayed.items()], context="rejeu",
        )
        for group in all_groups:
            wanted = replayed.get(group)
            if wanted is None:
                continue
            if group in na_groups:
                if wanted != SPLIT_TRAIN:
                    plan.na_replay_overrides.append({
                        "group": group,
                        "replayed_split": wanted,
                        "applied_split": SPLIT_TRAIN,
                        "reason": (
                            "groupe portant une frame non identifiée - la règle "
                            "NA prime sur le rejeu"
                        ),
                    })
                continue
            plan.split_of_group[group] = wanted
        plan.replayed_from = replay.get("source")

    remaining = [g for g in all_groups if g not in plan.split_of_group]
    if plan.replayed_from:
        # « Nouveaux » = absents du fichier rejoué, qu'ils soient NA ou non.
        plan.new_groups = sorted(g for g in all_groups if g not in replayed)

    # 2. Stratification gloutonne, classes rares d'abord.
    rng = random.Random(seed)
    jitter = {group: rng.random() for group in remaining}
    rarity = {
        group: min(
            (class_weight.get(name, 0) for name in classes_of_group.get(group, ())),
            default=10 ** 9,
        )
        for group in remaining
    }
    remaining.sort(key=lambda g: (rarity[g], -images_per_group[g], jitter[g], g))

    total_images = sum(images_per_group[g] for g in all_groups)
    targets = {name: ratios[name] * total_images for name in SPLIT_NAMES}
    taken: Counter = Counter()
    for group, split in plan.split_of_group.items():
        taken[split] += images_per_group[group]
    seen_classes: Dict[str, Set[str]] = {name: set() for name in SPLIT_NAMES}
    for group, split in plan.split_of_group.items():
        seen_classes[split].update(classes_of_group.get(group, ()))

    def _score(split: str, group: str) -> float:
        """Combien d'images ce split réclame encore, classes rares en prime.

        Le déficit est compté en **images** (et non en proportion) : sinon un
        split de 15 % vide bat systématiquement un train de 70 % à peine
        entamé, et trois groupes se répartiraient 1/1/1 au lieu de 2/1/0.
        Le bonus de couverture reste proportionné à la taille du split, pour
        peser sur les cas serrés sans renverser les ratios.
        """
        target = targets[split]
        deficit = target - taken[split]
        bonus = 0.0
        if target > 0 and taken[split] < target:
            missing = classes_of_group.get(group, set()) - seen_classes[split]
            if missing:
                bonus = 0.5 * target
        return deficit + bonus

    for group in remaining:
        best = max(SPLIT_NAMES, key=lambda split: _score(split, group))
        plan.split_of_group[group] = best
        taken[best] += images_per_group[group]
        seen_classes[best].update(classes_of_group.get(group, ()))

    assert_no_group_leak(
        [(group, split) for group, split in plan.split_of_group.items()],
        context="plan de split",
    )
    return plan


def assert_written_layout_matches_plan(
    staging: Path,
    images: Sequence[Dict[str, Any]],
    split_plan: SplitPlan,
) -> None:
    """Anti-fuite sur les fichiers **présents sur le disque**, pas sur le plan.

    On relit `images/<split>/<nom>.jpg`, on remonte de chaque fichier à sa
    frame puis à son groupe via le plan, et on vérifie trois choses : le
    dossier de split correspond bien à ce que le plan a décidé, aucun groupe
    n'a d'images dans deux dossiers de split, et l'ensemble écrit est
    exactement l'ensemble planifié (ni fichier orphelin, ni image manquante).
    """
    root = Path(staging) / "images"
    planned = {image["file_name"]: image for image in images}
    written_pairs: List[Tuple[str, str]] = []
    written_names: Set[str] = set()

    for path in sorted(root.rglob("*.jpg")) if root.is_dir() else []:
        rel = path.relative_to(root).as_posix()
        split, _, name = rel.partition("/")
        written_names.add(rel)
        if not name or split not in SPLIT_NAMES:
            raise ExportIntegrityError(
                f"images/{rel} n'est pas dans un dossier de split connu "
                f"({', '.join(SPLIT_NAMES)})"
            )
        group = split_plan.group_of_frame.get(Path(name).stem)
        if group is None:
            raise ExportIntegrityError(
                f"images/{rel} n'appartient à aucune frame planifiée - fichier "
                "étranger dans le dossier d'export"
            )
        expected = split_plan.split_of_group.get(group)
        if expected != split:
            raise ExportIntegrityError(
                f"images/{rel} écrite dans '{split}' alors que le groupe "
                f"{group} est affecté à '{expected}'"
            )
        written_pairs.append((group, split))

    assert_no_group_leak(written_pairs, context="images écrites sur disque")

    missing = sorted(set(planned) - written_names)
    extra = sorted(written_names - set(planned))
    if missing or extra:
        raise ExportIntegrityError(
            "Le contenu de images/ ne correspond pas au plan - "
            f"{len(missing)} manquante(s) {missing[:5]}, "
            f"{len(extra)} en trop {extra[:5]}"
        )


def apply_frame_stride(
    frames: Sequence[ExportFrame], stride: int,
) -> Tuple[List[ExportFrame], List[Dict[str, Any]]]:
    """Décime les frames quasi identiques d'un même média (donc d'une piste).

    À 30 i/s, dix frames consécutives d'un poisson qui traverse le champ sont
    dix copies du même exemple. `stride=N` ne garde que les frames dont
    l'**index absolu** est multiple de N.

    Le critère est l'index, pas le rang d'apparition : décimer sur le rang
    gardait une frame sur N *parmi les frames annotées*, si bien que les frames
    1000 et 1001 (deux dixièmes de seconde d'écart, le même poisson) passaient
    toutes les deux dès qu'elles étaient voisines dans la liste, tandis que
    deux frames distantes de 30 secondes pouvaient être écartées. Sur l'index,
    l'écart minimal entre deux frames gardées est bien de N images.

    Les photos ne sont jamais décimées : chacune est un exemple distinct.
    """
    stride = max(1, int(stride))
    ordered = sorted(frames, key=lambda f: (f.media_id, f.frame_index))
    if stride == 1:
        return list(ordered), []

    kept: List[ExportFrame] = []
    dropped: List[Dict[str, Any]] = []
    for frame in ordered:
        if frame.media_type != "video":
            kept.append(frame)
            continue
        if int(frame.frame_index) % stride == 0:
            kept.append(frame)
        else:
            dropped.append({
                "export_key": frame.key,
                "media_id": frame.media_id,
                "frame_index": frame.frame_index,
                "annotation_count": len(frame.annotations),
            })
    return kept, dropped


# ── Géométrie ──────────────────────────────────────────────────────────────


def _normalized_box(
    ann: SpatialAnnotation, geom: Dict[str, Any], width: int, height: int,
) -> Tuple[float, float, float, float]:
    """(cx, cy, w, h) normalisés, points compris."""
    if ann.geom_type == "point":
        px = geom.get("x", geom.get("cx", 0.5))
        py = geom.get("y", geom.get("cy", 0.5))
        if max(px, py) > 1.0:
            px, py = px / width, py / height
        return px, py, geom.get("w", 0.05), geom.get("h", 0.05)
    return bbox_from_geometry(geom, width, height)


# Déplacement en dessous duquel un bornage n'est pas un défaut d'annotation
# mais du bruit d'arrondi. Voir `scripts/audit_bbox_bounds.py` : sur la base
# réelle, les 146 « boîtes hors cadre » signalées par la revue de la phase 2
# viennent **toutes** de l'import public Roboflow, dont les fichiers de labels
# arrondissent `cx`, `cy`, `w` et `h` à six décimales *indépendamment*. Une
# boîte collée au bord donne alors `cx − w/2 = −0,0000005` en unités
# normalisées, soit **deux dix-millièmes de pixel** sur une image de 416 px -
# et au pire 0,0037 px sur la plus grande image du lot (7360 px de large).
# Compter cela comme un débordement affichait un avertissement alarmant pour
# une erreur cent fois plus petite qu'un pixel. Le seuil est fixé à un
# centième de pixel : au-delà, le bornage déplace une boîte visible.
CLAMP_EPSILON_PX = 1e-2


def clamp_box_to_image_ex(
    cx: float, cy: float, box_w: float, box_h: float, width: int, height: int,
) -> Tuple[Tuple[float, float, float, float], float]:
    """Borne une boîte. Retour : `((x, y, w, h), déplacement maximal en px)`.

    Une boîte tracée à cheval sur le bord de l'image (ou une géométrie
    légèrement fausse) déborde : COCO l'accepte sans broncher - les métriques
    d'aire deviennent fantaisistes - et YOLO reçoit des coordonnées normalisées
    hors de [0, 1] que les entraîneurs tronquent en silence, chacun à sa façon.
    On borne donc à l'export, en pixels, **une fois**, pour que le COCO et son
    dérivé YOLO décrivent la même boîte.

    Le déplacement est rendu tel quel plutôt qu'un simple booléen : c'est sa
    **magnitude** qui distingue un défaut d'annotation d'un arrondi de fichier
    source, et l'export doit compter les deux séparément au lieu de les
    confondre.
    """
    x0 = cx * width - (box_w * width) / 2.0
    y0 = cy * height - (box_h * height) / 2.0
    x1, y1 = x0 + box_w * width, y0 + box_h * height
    cx0 = min(max(x0, 0.0), float(width))
    cy0 = min(max(y0, 0.0), float(height))
    cx1 = min(max(x1, 0.0), float(width))
    cy1 = min(max(y1, 0.0), float(height))
    delta = max(
        abs(before - after)
        for before, after in ((x0, cx0), (y0, cy0), (x1, cx1), (y1, cy1))
    )
    return (cx0, cy0, cx1 - cx0, cy1 - cy0), delta


def clamp_box_to_image(
    cx: float, cy: float, box_w: float, box_h: float, width: int, height: int,
    *, epsilon_px: float = CLAMP_EPSILON_PX,
) -> Tuple[Tuple[float, float, float, float], bool]:
    """Borne une boîte aux limites de l'image. Retour : `((x, y, w, h), bornée)`.

    `bornée` ne vaut `True` que si un côté a bougé de plus de `epsilon_px` :
    borner sans le dire cacherait un défaut d'annotation, mais le signaler pour
    un dix-millième de pixel noie le vrai défaut sous du bruit d'arrondi.
    """
    box, delta = clamp_box_to_image_ex(cx, cy, box_w, box_h, width, height)
    return box, delta > float(epsilon_px)


# ── Résultat ───────────────────────────────────────────────────────────────


@dataclass
class ExportResult:
    """Ce qu'un export a produit - dossier, manifeste, trace en base."""

    output_dir: Path
    manifest: Dict[str, Any]
    splits: Dict[str, Any]
    report: Dict[str, Any]
    run: ExportRun
    exclusions: List[Dict[str, Any]] = field(default_factory=list)

    @property
    def content_sha256(self) -> str:
        return self.manifest.get("content_sha256", "")


# ── Export ─────────────────────────────────────────────────────────────────


def export_dataset(
    session: Session,
    output_root: Path,
    *,
    split_by: str,
    taxonomy_rank: str = RANK_FISH,
    fmt: str = FORMAT_COCO,
    project_ids: Optional[List[str]] = None,
    media_ids: Optional[List[str]] = None,
    dataset_name: str = DEFAULT_DATASET_NAME,
    dataset_version: str = DEFAULT_DATASET_VERSION,
    seed: int = DEFAULT_SEED,
    ratios: Optional[Dict[str, float]] = None,
    frame_stride: int = 1,
    min_instances: int = DEFAULT_MIN_INSTANCES,
    min_media: int = DEFAULT_MIN_MEDIA,
    rectify_images: bool = True,
    created_at: Optional[datetime] = None,
    replay_splits: Optional[Path] = None,
    mark_sessions_exported: bool = True,
    strict_grouping: Optional[float] = DEFAULT_STRICT_GROUPING,
    ava_hz: float = export_behavior.DEFAULT_AVA_HZ,
    ava_max_gap_s: float = export_behavior.DEFAULT_AVA_MAX_GAP_S,
) -> ExportResult:
    """Produit un dataset versionné sous `output_root`.

    `split_by` n'a **pas** de valeur par défaut : choisir le grain du split est
    une décision scientifique (média, session, site), pas un réglage qu'on
    hérite en silence. `strict_grouping` est la part maximale de médias
    auxquels la métadonnée de ce grain peut manquer avant que l'export refuse
    (`None` pour ne jamais refuser, le manifeste comptant quand même les
    replis).

    `created_at` est la date **figée** qui entrera dans les fichiers hachés.
    L'appelant qui veut deux exports identiques passe la même valeur ; à
    défaut, l'heure courante est utilisée et l'export n'est reproductible qu'à
    date égale.

    Reproductibilité : le contrat porte sur `content_sha256` et sur les
    empreintes fichier par fichier. `source.db_snapshot_sha256` décrit la base
    telle qu'elle était **avant** cet export ; sur une base vivante, il change
    d'un export à l'autre - c'est normal, et c'est écrit.
    """
    if split_by not in SPLIT_BY_VALUES:
        raise ValueError(
            f"split_by doit valoir l'un de {SPLIT_BY_VALUES} - reçu {split_by!r}"
        )
    if taxonomy_rank not in TAXONOMY_RANKS:
        raise ValueError(f"Rang inconnu : {taxonomy_rank!r}")
    if fmt not in SUPPORTED_FORMATS:
        raise ValueError(
            f"Format inconnu : {fmt!r} - attendu l'un de {SUPPORTED_FORMATS}"
        )
    for label, value in (("nom", dataset_name), ("version", dataset_version)):
        if not _SAFE_TOKEN.match(str(value or "")):
            raise ValueError(
                f"{label.capitalize()} de dataset invalide : {value!r}. Lettres, "
                "chiffres, point, tiret et souligné uniquement - ils forment le "
                "nom du dossier d'export."
            )

    ratios = _normalized_ratios(ratios)
    stamp = created_at or datetime.utcnow()
    output_root = Path(output_root)
    output_root.mkdir(parents=True, exist_ok=True)

    db_path = _database_path(session)
    # Le journal WAL d'abord : sans checkpoint, l'empreinte scellerait un état
    # antérieur aux annotations qu'on exporte.
    wal_checkpointed: Optional[bool] = None
    db_sha = None
    if db_path:
        session.commit()
        wal_checkpointed = wal_checkpoint(db_path)
        db_sha = sha256_file(db_path)

    export_filter: Dict[str, Any] = {"project_ids": project_ids}
    if media_ids is not None:
        export_filter["media_ids"] = media_ids

    run = ExportRun(
        id=str(uuid.uuid4()),
        format=fmt,
        taxonomy_rank=taxonomy_rank,
        filter_json=json.dumps(export_filter),
        output_path=str(output_root.resolve()),
        annotation_count=0,
        dataset_name=dataset_name,
        dataset_version=dataset_version,
        git_commit=git_commit(),
        db_snapshot_sha256=db_sha,
        split_strategy=f"group_by_{split_by}",
        split_seed=int(seed),
        status=EXPORT_STATUS_RUNNING,
    )
    session.add(run)
    # Commit volontaire : un export qui échoue doit laisser sa trace `failed`
    # en base, pas disparaître avec le rollback de l'appelant.
    session.commit()

    staging = output_root / f".staging_{run.id[:8]}"
    if staging.exists():
        shutil.rmtree(staging, ignore_errors=True)
    staging.mkdir(parents=True)

    # Renseigné par `_build` juste après le renommage : c'est la seule façon de
    # savoir, depuis ici, qu'un dossier au nom **définitif** a été créé par cet
    # appel et doit disparaître si la suite échoue.
    produced: Dict[str, Path] = {}

    # Trois pipelines, un seul point d'entrée : la détection part des
    # annotations humaines, le suivi des `track_samples`, le comportement des
    # `temporal_events`. Tous partagent le découpage par groupe, le manifeste
    # scellé et la ligne `export_runs` - c'est ce qui fait un noyau.
    builder = _build
    if fmt in TRACKING_FORMATS:
        builder = _build_tracking
    elif fmt in BEHAVIOR_FORMATS:
        builder = _build_behavior

    try:
        result = builder(
            session,
            staging=staging,
            output_root=output_root,
            run=run,
            split_by=split_by,
            taxonomy_rank=taxonomy_rank,
            fmt=fmt,
            project_ids=project_ids,
            media_ids=media_ids,
            dataset_name=dataset_name,
            dataset_version=dataset_version,
            seed=seed,
            ratios=ratios,
            frame_stride=frame_stride,
            min_instances=min_instances,
            min_media=min_media,
            rectify_images=rectify_images,
            stamp=stamp,
            db_sha=db_sha,
            wal_checkpointed=wal_checkpointed,
            replay_splits=replay_splits,
            mark_sessions_exported=mark_sessions_exported,
            strict_grouping=strict_grouping,
            produced=produced,
            ava_hz=ava_hz,
            ava_max_gap_s=ava_max_gap_s,
        )
    except Exception:
        shutil.rmtree(staging, ignore_errors=True)
        final_dir = produced.get("final_dir")
        if final_dir is not None:
            # Un échec après le renommage (manifeste non écrit, commit refusé…)
            # laissait un dossier au **nom valide** : indiscernable d'un export
            # réussi pour qui le lit, alors qu'il n'est pas scellé. Il est créé
            # par cet appel, il repart avec lui.
            log.warning(
                "Export interrompu après renommage - dossier non scellé "
                "supprimé : %s", final_dir,
            )
            shutil.rmtree(final_dir, ignore_errors=True)
        # Le noyau touche à la base (statut des sessions couvertes) : un export
        # qui échoue ne doit rien laisser derrière lui, sauf sa ligne `failed`.
        # Cette trace est un service rendu, jamais une raison de perdre la
        # cause réelle : son propre `try`, et l'exception d'origine repart.
        try:
            session.rollback()
            run.status = EXPORT_STATUS_FAILED
            session.add(run)
            session.commit()
        except Exception:
            log.exception(
                "Échec de l'export %s : impossible d'enregistrer le statut "
                "'failed' en base (l'exception d'origine est relancée).", run.id,
            )
        raise
    return result


def _build(
    session: Session,
    *,
    staging: Path,
    output_root: Path,
    run: ExportRun,
    split_by: str,
    taxonomy_rank: str,
    fmt: str,
    project_ids: Optional[List[str]],
    media_ids: Optional[List[str]],
    dataset_name: str,
    dataset_version: str,
    seed: int,
    ratios: Dict[str, float],
    frame_stride: int,
    min_instances: int,
    min_media: int,
    rectify_images: bool,
    stamp: datetime,
    db_sha: Optional[str],
    wal_checkpointed: Optional[bool],
    replay_splits: Optional[Path],
    mark_sessions_exported: bool,
    strict_grouping: Optional[float],
    produced: Dict[str, Path],
    # Réglages propres au comportement : acceptés ici pour que les trois
    # pipelines partagent une seule signature d'appel.
    ava_hz: float = export_behavior.DEFAULT_AVA_HZ,
    ava_max_gap_s: float = export_behavior.DEFAULT_AVA_MAX_GAP_S,
) -> ExportResult:
    exclusions = ExportExclusions()
    default_rectifier = load_left_rectifier() if rectify_images else None
    rectifier_cache: Dict[tuple, Any] = {}
    if rectify_images and default_rectifier is None:
        log.warning(
            "Aucune calibration exploitable : les images sortent BRUTES "
            "(image_space='raw'), les boîtes ont été tracées en rectifié."
        )

    # 1. Collecte + décimation.
    frames = collect_export_frames(
        session, project_ids, media_ids=media_ids, exclusions=exclusions,
    )
    frames, stride_dropped = apply_frame_stride(frames, frame_stride)
    for frame in frames:
        frame.annotations.sort(key=lambda a: a.id)

    # 2. Classes depuis les données.
    plan = build_class_plan(
        session, frames, taxonomy_rank,
        min_instances=min_instances, min_media=min_media,
    )

    # 3. Groupes, règle NA, split.
    cache: Dict[str, str] = {}
    degraded: Dict[str, str] = {}
    group_of_frame = {
        frame.key: group_key_for_frame(session, frame, split_by, cache, degraded=degraded)
        for frame in frames
    }
    degraded_groups = grouping_degradation(
        split_by, {frame.media_id for frame in frames}, degraded, group_of_frame,
        strict_grouping=strict_grouping,
    )
    classes_of_group: Dict[str, Set[str]] = defaultdict(set)
    na_groups: Set[str] = set()
    na_frames: List[Dict[str, Any]] = []
    for frame in frames:
        group = group_of_frame[frame.key]
        unknown = [
            ann for ann in frame.annotations
            if plan.class_of_annotation.get(ann.id) is None
        ]
        for ann in frame.annotations:
            name = plan.class_of_annotation.get(ann.id)
            if name is not None:
                classes_of_group[group].add(name)
        if unknown:
            na_groups.add(group)
            na_frames.append({
                "export_key": frame.key,
                "media_id": frame.media_id,
                "frame_index": frame.frame_index,
                "unidentified_annotation_ids": sorted(a.id for a in unknown),
                "annotation_count": len(frame.annotations),
            })

    replay = load_replay_splits(replay_splits) if replay_splits else None
    class_weight = {name: plan.stats[name]["instances"] for name in plan.names}
    split_plan = plan_splits(
        frames,
        group_of_frame,
        split_by=split_by,
        seed=seed,
        ratios=ratios,
        classes_of_group=classes_of_group,
        class_weight=class_weight,
        na_groups=na_groups,
        replay=replay,
    )

    # 4. Matérialisation des images (l'ordre fixe rend l'export reproductible).
    images: List[Dict[str, Any]] = []
    boxes: List[Dict[str, Any]] = []
    meta_rows: List[Dict[str, Any]] = []
    materialized: List[ExportFrame] = []
    image_id_of_frame: Dict[str, int] = {}
    clamped_boxes = 0
    # Bornages sous le seuil : du bruit d'arrondi de fichier source, pas un
    # défaut d'annotation. Comptés à part plutôt que passés sous silence.
    rounding_clamped = 0

    for frame in frames:
        split = split_plan.split_of_frame(frame.key)
        rel_name = f"{split}/{frame.file_name}"
        dims = materialize_frame_image(
            session, frame, staging / "images" / split / frame.file_name,
            transform=_frame_rectifier(
                session,
                frame,
                enabled=rectify_images,
                default_rectifier=default_rectifier,
                cache=rectifier_cache,
            ),
            exclusions=exclusions,
        )
        if dims is None:
            continue
        materialized.append(frame)
        width, height = dims
        image_id = len(images) + 1
        image_id_of_frame[frame.key] = image_id
        images.append({
            "id": image_id,
            "file_name": rel_name,
            "width": width,
            "height": height,
            "split": split,
            "group": group_of_frame[frame.key],
            **_portable_frame_meta(frame.meta()),
        })

        for ann in frame.annotations:
            try:
                geom = parse_geometry(ann)
                cx, cy, box_w, box_h = _normalized_box(ann, geom, width, height)
            except (ValueError, TypeError, KeyError, json.JSONDecodeError) as exc:
                exclusions.add(
                    ann.id, REASON_GEOMETRIE_ILLISIBLE, media_id=frame.media_id,
                    frame_index=frame.frame_index, detail=str(exc),
                )
                continue
            (box_x, box_y, abs_w, abs_h), clamp_delta = clamp_box_to_image_ex(
                cx, cy, box_w, box_h, width, height,
            )
            if abs_w <= 0 or abs_h <= 0:
                exclusions.add(
                    ann.id, REASON_GEOMETRIE_ILLISIBLE, media_id=frame.media_id,
                    frame_index=frame.frame_index,
                    detail="boîte entièrement hors de l'image après bornage",
                )
                continue
            if clamp_delta > CLAMP_EPSILON_PX:
                clamped_boxes += 1
            elif clamp_delta > 0:
                rounding_clamped += 1
            # COCO et YOLO décrivent la **même** boîte bornée : les normalisées
            # sont recalculées depuis les pixels, jamais reprises de l'origine.
            cx, cy = (box_x + abs_w / 2) / width, (box_y + abs_h / 2) / height
            box_w, box_h = abs_w / width, abs_h / height
            cls_name = plan.class_of_annotation.get(ann.id)
            boxes.append({
                "id": len(boxes) + 1,
                "image_id": image_id,
                "split": split,
                "class_name": cls_name,
                "bbox": [box_x, box_y, abs_w, abs_h],
                "yolo": (cx, cy, box_w, box_h),
                "area": abs_w * abs_h,
                "attributes": {
                    "track_id": ann.track_id,
                    "spatial_annotation_id": ann.id,
                    "geometry_space": geometry_space(geom),
                    "frame_ref": frame.frame_ref,
                    "source": ann.source,
                    "author": ann.author,
                    "confidence": ann.confidence,
                    "measurement_mm": ann.measurement_mm,
                    "position_mm": {
                        "x": ann.position_x_mm,
                        "y": ann.position_y_mm,
                        "z": ann.position_z_mm,
                    },
                    "model_id": getattr(ann, "model_id", None),
                    "model_sha256": getattr(ann, "model_sha256", None),
                    "model_conf_threshold": getattr(
                        ann, "model_conf_threshold", None,
                    ),
                    "identified": _has_authoritative_identification(ann, session),
                    "identification_status": getattr(ann, "identification_status", None),
                    "family_is_na": getattr(ann, "family_is_na", None),
                    "genus_is_na": getattr(ann, "genus_is_na", None),
                    "species_is_na": getattr(ann, "species_is_na", None),
                },
            })
            meta_rows.append({
                "annotation_id": ann.id,
                "export_key": frame.key,
                "split": split,
                "group": group_of_frame[frame.key],
                "class": cls_name,
                "track_id": ann.track_id,
                **_portable_frame_meta(frame.meta()),
            })

    # Second contrôle, sur ce qui a réellement été écrit **sur le disque** : le
    # plan peut être juste et l'écriture fautive (nom de fichier, dossier de
    # split…). Confronter `image["group"]` à `image["split"]`, tous deux
    # recopiés du plan, ne vérifiait rien d'autre que la première assertion.
    assert_written_layout_matches_plan(staging, images, split_plan)

    space_summary = _portable_space_summary(export_image_space_summary(materialized))

    # 5. Dérivé YOLO : les images portant un `ignore` en sont retirées.
    ignore_image_ids = {
        box["image_id"] for box in boxes if box["class_name"] is None
    }
    yolo_images = [img for img in images if img["id"] not in ignore_image_ids]
    dropped_frames = [
        row for row in na_frames if image_id_of_frame.get(row["export_key"]) in ignore_image_ids
    ]
    if fmt == FORMAT_YOLO:
        by_image = defaultdict(list)
        for box in boxes:
            by_image[box["image_id"]].append(box)
        for image in images:
            if image["id"] not in ignore_image_ids:
                continue
            for box in by_image[image["id"]]:
                ann_id = box["attributes"]["spatial_annotation_id"]
                reason = (
                    REASON_HORS_RANG if box["class_name"] is None
                    else REASON_FRAME_NON_IDENTIFIEE
                )
                exclusions.add(
                    ann_id, reason, media_id=image.get("media_id"),
                    frame_index=image.get("frame_index"),
                    detail=(
                        f"rang {taxonomy_rank} - frame écartée du dérivé YOLO "
                        "(l'ignore de COCO n'a pas d'équivalent)"
                    ),
                )

    # 6. Écriture des fichiers d'annotation.
    identified_count = sum(1 for box in boxes if box["class_name"] is not None)
    class_payload = _class_payload(plan, images, boxes)
    composition = _split_composition(images, boxes, split_plan)
    # Dernier verrou avant d'écrire quoi que ce soit : la règle NA est vérifiée
    # sur la composition réelle, pas sur l'intention du plan.
    assert_val_test_fully_identified(composition, context=f"rang {taxonomy_rank}")
    split_payload = _splits_payload(
        split_plan, images, session, composition=composition,
    )
    _write_json(staging / SPLITS_NAME, split_payload)

    coco_files = _write_coco(
        staging,
        images=images,
        boxes=boxes,
        plan=plan,
        space_summary=space_summary,
        exclusions=exclusions,
        dataset_name=dataset_name,
        dataset_version=dataset_version,
        stamp=stamp,
    )

    yolo_info: Dict[str, Any] = {}
    if fmt == FORMAT_YOLO:
        yolo_info = _write_yolo(
            staging, images=yolo_images, boxes=boxes, plan=plan,
            excluded_images=len(images) - len(yolo_images),
        )

    report: Dict[str, Any] = {
        "format": fmt,
        "taxonomy_rank": taxonomy_rank,
        "generated_at": stamp.isoformat(timespec="seconds"),
        # Pour YOLO, ce qui compte est ce qui a réellement été écrit en label :
        # les annotations d'une image écartée ne sont nulle part dans le dérivé.
        "annotation_count": (
            yolo_info.get("label_count", 0) if fmt == FORMAT_YOLO else len(boxes)
        ),
        "image_count": len(yolo_images) if fmt == FORMAT_YOLO else len(images),
        "coco_annotation_count": len(boxes),
        "coco_image_count": len(images),
        "ignored_annotation_count": len(boxes) - identified_count,
        "coordinate_frame": space_summary,
        "split": {
            "strategy": split_plan.strategy,
            "split_by": split_by,
            "seed": split_plan.seed,
            "ratios": split_plan.ratios,
            "composition": composition,
            "forced_train_group_count": len(split_plan.forced_train_groups),
            "replayed_from": split_plan.replayed_from,
            "degraded_groups": degraded_groups,
        },
        "classes": class_payload,
        "frame_stride": {"stride": max(1, int(frame_stride)), "dropped": len(stride_dropped)},
        # Boîtes ramenées dans les bornes de l'image : ce n'est pas une erreur
        # d'export mais un défaut d'annotation, il doit se voir.
        "bbox_clamped": {
            "count": clamped_boxes,
            "threshold_px": CLAMP_EPSILON_PX,
            # Séparer les deux est le résultat de l'enquête « 146 bbox hors
            # cadre » : sur la base réelle, elles venaient toutes des labels
            # Roboflow arrondis à six décimales, pour deux dix-millièmes de
            # pixel. Les mélanger faisait passer un arrondi de fichier source
            # pour un défaut d'annotation.
            "rounding_only": rounding_clamped,
            "rule": (
                "boîte débordant de l'image bornée à [0, largeur] × [0, hauteur] "
                "- COCO et YOLO décrivent la même boîte bornée. `count` ne "
                f"retient que les déplacements supérieurs à {CLAMP_EPSILON_PX} px ; "
                "en dessous, c'est un arrondi du fichier source, compté dans "
                "`rounding_only`."
            ),
        },
        "unidentified_frames": {
            "count": len(dropped_frames),
            # Le rapport d'un export COCO annonçait une exclusion qui n'a lieu
            # que dans le dérivé YOLO : en COCO ces frames sont livrées.
            "excluded_from_dataset": fmt == FORMAT_YOLO,
            "reason": (
                "YOLO ne gère pas l'ignore : une frame contenant un poisson "
                "non identifié au rang demandé est écartée en entier du dérivé."
                if fmt == FORMAT_YOLO else
                "Export COCO : ces frames sont livrées, leurs boîtes non "
                "identifiées portant ignore=1/iscrowd=1. Aucune image n'est "
                "écartée pour cette raison - l'exclusion ne concerne que le "
                "dérivé YOLO."
            ),
            "frames": dropped_frames,
        },
        "exclusions": exclusions.as_dict(),
    }
    _write_json(staging / REPORT_NAME, report)
    _write_json(staging / "annotations_meta.json", {
        "coordinate_frame": space_summary,
        "annotations": meta_rows,
        "excluded_annotations": exclusions.rows(),
        "unidentified_frames": dropped_frames,
    })

    # 7. Statut des sessions couvertes, puis manifeste et nom définitif.
    # Le marquage précède le manifeste (qui en rend compte) mais reste dans la
    # transaction : si l'écriture échoue, le `rollback` de l'appelant le défait.
    exported_media = {image["media_id"] for image in images}
    covered = (
        _mark_sessions_exported(session, exported_media)
        if mark_sessions_exported else []
    )

    manifest_core: Dict[str, Any] = {
        "schema": MANIFEST_SCHEMA,
        "dataset_name": dataset_name,
        "dataset_version": dataset_version,
        "created_at": stamp.isoformat(timespec="seconds"),
        "git_commit": run.git_commit,
        "format": fmt,
        "taxonomy_rank": taxonomy_rank,
        "source": {
            "db_snapshot_sha256": db_sha,
            # Empreinte du seul fichier `.db` : sans checkpoint, les commits
            # restés dans le journal WAL n'y seraient pas. `false` = empreinte
            # potentiellement en retard sur la base réelle.
            "db_snapshot_wal_checkpointed": wal_checkpointed,
            "filter": {
                **{"project_ids": project_ids},
                **({"media_ids": media_ids} if media_ids is not None else {}),
            },
            "media_count": len({img["media_id"] for img in images}),
            "image_count": len(images),
            "annotation_count": len(boxes),
            "identified_annotation_count": identified_count,
            "bbox_clamped_count": clamped_boxes,
            "bbox_clamped_rounding_count": rounding_clamped,
            "frame_stride": max(1, int(frame_stride)),
            "frames_dropped_by_stride": len(stride_dropped),
        },
        "coordinate_frame": space_summary,
        "split": {
            "strategy": split_plan.strategy,
            "split_by": split_by,
            "seed": split_plan.seed,
            "ratios": split_plan.ratios,
            "ratios_realized": _realized_ratios(composition),
            "composition": composition,
            "group_count": len(split_plan.split_of_group),
            # Listes bornées : l'affectation complète groupe par groupe vit
            # dans splits.json (haché comme les autres fichiers), le manifeste
            # reste lisible même avec 1600 groupes.
            "forced_train_groups": sorted(split_plan.forced_train_groups)[:_GROUP_SAMPLE],
            "na_rule": {
                "rule": (
                    "val et test intégralement identifiés - tout groupe portant "
                    "une frame non identifiée part en train, y compris s'il est "
                    "rejoué ailleurs"
                ),
                "groups_forced_to_train": len(split_plan.forced_train_groups),
                "frames_concerned": len(na_frames),
                # Trace explicite : un rejeu ne peut pas rouvrir la fuite, mais
                # l'utilisateur doit savoir que son découpage a été amendé.
                "groups_moved_from_replay": len(split_plan.na_replay_overrides),
                "replay_overrides": split_plan.na_replay_overrides[:_GROUP_SAMPLE],
            },
            "replayed_from": split_plan.replayed_from,
            "new_group_count": len(split_plan.new_groups),
            "degraded_groups": degraded_groups,
            "groups_file": SPLITS_NAME,
        },
        "class_stats": class_payload,
        "exclusions": {
            "count": len(exclusions),
            "by_reason": exclusions.by_reason(),
            "summary": exclusions.summary(),
            "detail_file": REPORT_NAME,
        },
        "yolo": yolo_info,
        "sessions_marked_exported": covered,
    }
    final_dir, manifest = _seal_export(
        session,
        staging=staging,
        output_root=output_root,
        run=run,
        manifest_core=manifest_core,
        report=report,
        exclusions=exclusions,
        space_summary=space_summary,
        dataset_name=dataset_name,
        dataset_version=dataset_version,
        stamp=stamp,
        produced=produced,
    )

    log.info(
        "Export %s v%s (%s) : %d image(s), %d annotation(s) dont %d ignore - %s",
        dataset_name, dataset_version, fmt, len(images), len(boxes),
        len(boxes) - identified_count, exclusions.summary(),
    )
    if fmt == FORMAT_YOLO:
        print(
            f"Export YOLO : {report['annotation_count']} label(s) sur "
            f"{report['image_count']} images, dérivés de {len(boxes)} annotations "
            f"COCO dont {len(boxes) - identified_count} ignore=1 - "
            f"{exclusions.summary()}"
        )
    else:
        print(
            f"Export COCO : {len(boxes)} annotations "
            f"({len(boxes) - identified_count} ignore=1) sur {len(images)} images - "
            f"{exclusions.summary()}"
        )
    empty = [name for name in SPLIT_NAMES if composition[name]["images"] == 0]
    if empty:
        # Un split vide n'est pas une erreur (il n'y a peut-être qu'un groupe),
        # mais un entraînement dessus échouera : autant le dire tout de suite.
        print(
            f"[!] Split(s) vide(s) : {', '.join(empty)} - pas assez de groupes "
            f"({len(split_plan.split_of_group)}) pour le grain '{split_by}'."
        )
    if not plan.names:
        print(
            f"[!] Aucune classe ne franchit les seuils (min_instances="
            f"{min_instances}, min_media={min_media}) au rang {taxonomy_rank} : "
            "tout sort en ignore. Baissez les seuils ou annotez davantage."
        )
    if yolo_info.get("names_without_instances"):
        print(
            "[!] Classe(s) déclarées dans data.yaml sans aucune instance livrée "
            f"en YOLO : {', '.join(yolo_info['names_without_instances'])} - "
            "leurs images portaient toutes un ignore. Les index restent alignés "
            f"sur instances_{taxonomy_rank}.json (voir yolo.names_policy)."
        )
    if clamped_boxes:
        print(
            f"[!] {clamped_boxes} boîte(s) débordaient de l'image et ont été "
            "bornées à ses limites (voir export_report.json, champ bbox_clamped)."
        )
    if rounding_clamped:
        print(
            f"[i] {rounding_clamped} boîte(s) recalées d'un arrondi de moins de "
            f"{CLAMP_EPSILON_PX} px (labels du dataset source arrondis) - sans "
            "conséquence, signalé pour mémoire."
        )
    if split_plan.na_replay_overrides:
        print(
            f"[!] Rejeu amendé : {len(split_plan.na_replay_overrides)} groupe(s) "
            "placés en val/test par le découpage rejoué contiennent un poisson "
            "non identifié - ramenés en train (voir split.na_rule.replay_overrides)."
        )
    if wal_checkpointed is False:
        print(
            "[!] Journal WAL non rabattu avant l'empreinte de base : "
            "db_snapshot_sha256 peut ignorer des écritures récentes "
            "(manifeste, champ source.db_snapshot_wal_checkpointed = false)."
        )
    print(f"Dossier : {final_dir}")

    return ExportResult(
        output_dir=final_dir,
        manifest=manifest,
        splits=split_payload,
        report=report,
        run=run,
        exclusions=exclusions.rows(),
    )


def _seal_export(
    session: Session,
    *,
    staging: Path,
    output_root: Path,
    run: ExportRun,
    manifest_core: Dict[str, Any],
    report: Dict[str, Any],
    exclusions: ExportExclusions,
    space_summary: Dict[str, Any],
    dataset_name: str,
    dataset_version: str,
    stamp: datetime,
    produced: Dict[str, Path],
) -> Tuple[Path, Dict[str, Any]]:
    """Scelle le dossier : empreintes, nom définitif, manifeste, `export_runs`.

    Partagé par les trois pipelines. Le condensé `content_sha256` est calculé
    sur le manifeste **avant** qu'il ne connaisse son propre emplacement : c'est
    ce qui rend deux exports du même contenu identiques d'un poste à l'autre.
    Le renommage précède l'écriture du manifeste, et `produced['final_dir']`
    permet à l'appelant de supprimer un dossier au nom valide mais non scellé si
    la suite échoue.
    """
    manifest_core = dict(manifest_core)
    files = _file_digests(staging)
    # Descripteur Croissant (MLCommons) : écrit **avant** le scellement, comme
    # `datapackage.json` en phase 4a, pour entrer de lui-même dans `files[]` et
    # dans le condensé. Son `distribution[]` réutilise les empreintes déjà
    # calculées ci-dessus - il ne peut porter que celles des autres fichiers,
    # jamais la sienne ; c'est le manifeste qui la fixe.
    croissant_path = croissant.write_croissant(
        staging, manifest_core=manifest_core, files=files,
    )
    files.append({
        "path": croissant_path.relative_to(staging).as_posix(),
        "sha256": sha256_file(croissant_path),
        "bytes": croissant_path.stat().st_size,
    })
    manifest_core["files"] = sorted(files, key=lambda row: row["path"])
    manifest_core["croissant"] = {
        "file": croissant.CROISSANT_NAME,
        "conforms_to": croissant.CONFORMS_TO,
        "note": (
            "descripteur JSON-LD lisible par les catalogues de datasets ; il "
            "décrit cet export, le manifeste en reste la source"
        ),
    }
    content_sha = hashlib.sha256(canonical_json(manifest_core)).hexdigest()
    manifest = dict(manifest_core)
    manifest["content_sha256"] = content_sha

    final_dir = output_root / (
        f"{dataset_name}_v{dataset_version}_{stamp:%Y%m%d}_{content_sha[:8]}"
    )
    if final_dir.exists():
        # Même contenu par construction (le nom porte l'empreinte) : on remplace
        # plutôt que de laisser deux dossiers concurrents.
        log.info("Export identique déjà présent, remplacé : %s", final_dir)
        shutil.rmtree(final_dir)
    staging.rename(final_dir)
    produced["final_dir"] = final_dir
    _write_json(final_dir / MANIFEST_NAME, manifest)
    manifest_sha = sha256_file(final_dir / MANIFEST_NAME)

    calib = (space_summary or {}).get("calibration") or {}
    run.output_path = str(final_dir.resolve())
    run.annotation_count = report["annotation_count"]
    run.image_count = report["image_count"]
    run.image_space = (space_summary or {}).get("image_space") or IMAGE_SPACE_RAW
    run.calibration_profile = calib.get("profile_name")
    run.calibration_sha256 = calib.get("calibration_sha256")
    run.manifest_sha256 = manifest_sha
    run.total_bytes = _disk_bytes(final_dir)
    run.status = EXPORT_STATUS_COMPLETED
    session.add(run)
    session.commit()

    run.exclusions = exclusions.rows()
    run.report = report
    run.manifest = manifest
    return final_dir, manifest


# ── Suivi : COCO-VID et son dérivé MOT ─────────────────────────────────────


def _build_tracking(
    session: Session,
    *,
    staging: Path,
    output_root: Path,
    run: ExportRun,
    split_by: str,
    taxonomy_rank: str,
    fmt: str,
    project_ids: Optional[List[str]],
    media_ids: Optional[List[str]],
    dataset_name: str,
    dataset_version: str,
    seed: int,
    ratios: Dict[str, float],
    frame_stride: int,
    min_instances: int,
    min_media: int,
    rectify_images: bool,
    stamp: datetime,
    db_sha: Optional[str],
    wal_checkpointed: Optional[bool],
    replay_splits: Optional[Path],
    mark_sessions_exported: bool,
    strict_grouping: Optional[float],
    produced: Dict[str, Path],
    ava_hz: float = export_behavior.DEFAULT_AVA_HZ,
    ava_max_gap_s: float = export_behavior.DEFAULT_AVA_MAX_GAP_S,
) -> ExportResult:
    """Dataset de suivi : `coco_vid.json`, et `mot/` quand le format le demande.

    Même squelette que la détection - collecte, décimation, class-map, split par
    groupe avec assertion anti-fuite, images rectifiées, manifeste scellé - mais
    la matière première est `track_samples` : c'est là que vivent les
    trajectoires, et c'est de là que vient `origin`, dont dépend la `visibility`
    MOT.
    """
    exclusions = ExportExclusions()
    default_rectifier = load_left_rectifier() if rectify_images else None
    rectifier_cache: Dict[tuple, Any] = {}
    if rectify_images and default_rectifier is None:
        log.warning(
            "Aucune calibration exploitable : les images sortent BRUTES "
            "(image_space='raw'), les boîtes de suivi ont été tracées en rectifié."
        )

    # 1. Collecte + décimation.
    frames, tracks_by_id, media_by_id = export_tracking.collect_track_frames(
        session, project_ids, media_ids=media_ids, exclusions=exclusions,
    )
    frames, stride_dropped = apply_frame_stride(frames, frame_stride)
    for frame in frames:
        frame.annotations.sort(key=lambda s: s.id)

    # 2. Classes depuis les pistes, avec la class-map du noyau.
    track_taxa = resolve_track_taxa(session, tracks_by_id.values())
    plan = build_class_plan_for_tracks(
        session, list(tracks_by_id.values()), taxonomy_rank,
        min_instances=min_instances, min_media=min_media,
        resolved_taxa=track_taxa,
    )
    instance_of_track = export_tracking.instance_ids(tracks_by_id.values())

    # 3. Groupes, règle NA, split - un média entier d'un seul côté du découpage.
    cache: Dict[str, str] = {}
    degraded: Dict[str, str] = {}
    group_of_frame = {
        frame.key: group_key_for_frame(session, frame, split_by, cache, degraded=degraded)
        for frame in frames
    }
    degraded_groups = grouping_degradation(
        split_by, {frame.media_id for frame in frames}, degraded, group_of_frame,
        strict_grouping=strict_grouping,
    )
    group_of_media = {
        frame.media_id: group_of_frame[frame.key] for frame in frames
    }
    classes_of_group: Dict[str, Set[str]] = defaultdict(set)
    na_groups: Set[str] = set()
    na_tracks: List[Dict[str, Any]] = []
    for track in tracks_by_id.values():
        group = group_of_media.get(track.media_id)
        if group is None:
            continue
        name = plan.class_of_annotation.get(track.id)
        if name is None:
            na_groups.add(group)
            na_tracks.append({
                "track_id": track.id,
                "media_id": track.media_id,
                "instance_id": instance_of_track.get(track.id),
            })
        else:
            classes_of_group[group].add(name)

    replay = load_replay_splits(replay_splits) if replay_splits else None
    class_weight = {name: plan.stats[name]["instances"] for name in plan.names}
    split_plan = plan_splits(
        frames, group_of_frame, split_by=split_by, seed=seed, ratios=ratios,
        classes_of_group=classes_of_group, class_weight=class_weight,
        na_groups=na_groups, replay=replay,
    )

    # 4. Matérialisation des images (ordre fixe = export reproductible).
    images: List[Dict[str, Any]] = []
    boxes: List[Dict[str, Any]] = []
    materialized: List[ExportFrame] = []
    clamped_boxes = 0
    unreadable_samples = 0

    for frame in frames:
        split = split_plan.split_of_frame(frame.key)
        dims = materialize_frame_image(
            session, frame, staging / "images" / split / frame.file_name,
            transform=_frame_rectifier(
                session,
                frame,
                enabled=rectify_images,
                default_rectifier=default_rectifier,
                cache=rectifier_cache,
                assume_rectified=True,
            ),
            exclusions=exclusions,
        )
        if dims is None:
            continue
        materialized.append(frame)
        width, height = dims
        media = media_by_id.get(frame.media_id)
        ref_w = float(media.width) if media is not None and media.width else float(width)
        ref_h = float(media.height) if media is not None and media.height else float(height)
        image_id = len(images) + 1
        images.append({
            "id": image_id,
            "file_name": f"{split}/{frame.file_name}",
            "width": width,
            "height": height,
            "split": split,
            "group": group_of_frame[frame.key],
            # `frame_id` est le nom TAO de l'index de frame ; il vaut l'index
            # absolu du fichier source, convention du dépôt.
            "frame_id": frame.frame_index,
            **_portable_frame_meta(frame.meta()),
        })

        for sample in frame.annotations:
            track = tracks_by_id.get(sample.track_id)
            if track is None:
                continue
            corners = export_tracking.sample_box_pixels(
                sample, ref_width=ref_w, ref_height=ref_h,
                image_width=width, image_height=height,
            )
            if corners is None:
                unreadable_samples += 1
                exclusions.add(
                    f"track_sample:{sample.id}", REASON_GEOMETRIE_ILLISIBLE,
                    media_id=frame.media_id, frame_index=frame.frame_index,
                    detail="bbox_json illisible",
                )
                continue
            (x1, y1, x2, y2), clamped = export_tracking.clamp_pixel_box(
                *corners, width, height,
            )
            if x2 - x1 <= 0 or y2 - y1 <= 0:
                exclusions.add(
                    f"track_sample:{sample.id}", REASON_GEOMETRIE_ILLISIBLE,
                    media_id=frame.media_id, frame_index=frame.frame_index,
                    detail="boîte entièrement hors de l'image après bornage",
                )
                continue
            if clamped:
                clamped_boxes += 1
            cls_name = plan.class_of_annotation.get(track.id)
            boxes.append({
                "id": len(boxes) + 1,
                "image_id": image_id,
                "media_id": frame.media_id,
                "frame_index": frame.frame_index,
                "split": split,
                "class_name": cls_name,
                "category_id": plan.category_id(cls_name),
                "instance_id": instance_of_track[track.id],
                "bbox": [x1, y1, x2 - x1, y2 - y1],
                "area": (x2 - x1) * (y2 - y1),
                "iscrowd": 1 if cls_name is None else 0,
                "ignore": 1 if cls_name is None else 0,
                "visibility": export_tracking.visibility_for(sample.origin),
                "attributes": {
                    "track_db_id": track.id,
                    "external_track_id": track.external_track_id,
                    "track_sample_id": sample.id,
                    "origin": sample.origin,
                    "edited_by": sample.edited_by,
                    "frame_ref": frame.frame_ref,
                    "identified": track_taxa.get(track.id) is not None,
                },
            })

    assert_written_layout_matches_plan(staging, images, split_plan)
    space_summary = _portable_space_summary(export_image_space_summary(materialized))

    # 5. Tables COCO-VID : videos, tracks, catégories.
    videos, video_id_of_media = export_tracking.build_videos(
        images, media_by_id, boxes=boxes,
    )
    for image in images:
        image["video_id"] = video_id_of_media.get(image["media_id"])
    for box in boxes:
        box["video_id"] = video_id_of_media.get(box["media_id"])

    categories = [
        {"id": index + 1, "name": name, "supercategory": plan.rank}
        for index, name in enumerate(plan.names)
    ]
    if plan.rank != RANK_FISH:
        categories.append({
            "id": len(plan.names) + 1,
            "name": UNIDENTIFIED_CATEGORY_NAME,
            "supercategory": plan.rank,
            "description": (
                "Piste observée mais non identifiable à ce rang (ou classe sous "
                "le seuil d'effectif) - annotations marquées ignore=1 / iscrowd=1."
            ),
        })

    live_tracks = {box["attributes"]["track_db_id"] for box in boxes}
    tracks_payload = [
        {
            "id": instance_of_track[track_id],
            "track_db_id": track_id,
            "external_track_id": tracks_by_id[track_id].external_track_id,
            "video_id": video_id_of_media.get(tracks_by_id[track_id].media_id),
            "media_id": tracks_by_id[track_id].media_id,
            "category_id": plan.category_id(plan.class_of_annotation.get(track_id)),
            "taxon_node_id": track_taxa.get(track_id),
            "source": tracks_by_id[track_id].source,
            "first_frame": tracks_by_id[track_id].first_frame,
            "last_frame": tracks_by_id[track_id].last_frame,
        }
        for track_id in sorted(live_tracks, key=lambda t: instance_of_track[t])
    ]

    # Les actions sont exportées avec leur frame exacte. Une frame absente du
    # suivi échantillonné est copiée à part : aucune boîte n'est inventée et
    # les images non annotées ne deviennent pas des négatifs dans MOT.
    event_exclusions = ExportExclusions()
    collected_events, _event_media, _event_tracks = export_behavior.collect_events(
        session, project_ids, media_ids=media_ids, exclusions=event_exclusions,
    )
    type_rows, _action_ids = export_behavior.active_action_table(
        session, {event["event_type"] for event in collected_events},
    )
    event_image_cache = {
        (image["media_id"], image["frame_index"]): {
            "image_id": image["id"], "file_name": "images/" + image["file_name"],
        }
        for image in images
    }
    extra_event_images = []
    missing_event_images = []

    def resolve_event_image(media_id, frame_index):
        key = (media_id, frame_index)
        if key not in event_image_cache:
            media = media_by_id[media_id]
            if frame_index < 0 or (media.frame_count and frame_index >= media.frame_count):
                event_image_cache[key] = {"image_id": None, "file_name": None}
                missing_event_images.append({
                    "media_id": media_id, "frame_index": frame_index, "reason": "hors_video",
                })
                return event_image_cache[key]
            event_frame = ExportFrame(media_id=media_id, frame_index=frame_index, media_type="video")
            relative = "event_images/" + event_frame.file_name
            dims = materialize_frame_image(
                session, event_frame, staging / relative,
                transform=_frame_rectifier(
                    session, event_frame, enabled=rectify_images,
                    default_rectifier=default_rectifier, cache=rectifier_cache,
                    assume_rectified=True,
                ),
            )
            event_image_cache[key] = {"image_id": None, "file_name": relative if dims else None}
            if dims:
                extra_event_images.append(relative)
            else:
                missing_event_images.append({"media_id": media_id, "frame_index": frame_index})
        return event_image_cache[key]

    track_events, track_event_types = export_tracking.build_track_events(
        collected_events, tracks_payload, type_rows,
        resolve_image=resolve_event_image, exclusions=event_exclusions,
    )
    event_report = {
        "schema": export_tracking.EVENT_SCHEMA,
        "source_table": "temporal_events",
        "file": export_tracking.TRACK_EVENTS_NAME,
        "count": len(track_events),
        "by_type": dict(sorted(Counter(event["event_type"] for event in track_events).items())),
        "by_source": dict(sorted(Counter(event["source"] or "unknown" for event in track_events).items())),
        "additional_image_count": len(extra_event_images),
        "missing_images": missing_event_images,
        "exclusions": event_exclusions.as_dict(),
    }

    # 6. Écriture.
    identified_count = sum(1 for box in boxes if box["class_name"] is not None)
    class_payload = _class_payload(plan, images, boxes)
    composition = _split_composition(images, boxes, split_plan)
    assert_val_test_fully_identified(composition, context=f"rang {taxonomy_rank}")
    split_payload = _splits_payload(split_plan, images, session, composition=composition)
    _write_json(staging / SPLITS_NAME, split_payload)

    export_tracking.write_coco_vid(
        staging, videos=videos, images=images, boxes=boxes,
        tracks_payload=tracks_payload, categories=categories,
        space_summary=space_summary, exclusions=exclusions,
        dataset_name=dataset_name, dataset_version=dataset_version,
        created_at=stamp.isoformat(timespec="seconds"), year=stamp.year,
        events=track_events, event_types=track_event_types,
        excluded_events=event_exclusions.rows(),
    )
    mot_info: Dict[str, Any] = {}
    if fmt == FORMAT_MOT:
        mot_info = export_tracking.write_mot(
            staging, videos=videos, images=images, boxes=boxes,
            link_image=_link_or_copy,
        )

    covered_frames = sum(video["exported_frames"] for video in videos)
    covered_span = sum(video["covered_span"] for video in videos)
    coverage = {
        "rule": (
            "frames exportées / étendue couverte (dernière − première + 1). "
            "Le suivi n'a été lancé que sur des portions de vidéo : un gt.txt "
            "sans cette information laisserait croire à une séquence continue."
        ),
        "exported_frames": covered_frames,
        "covered_span": covered_span,
        "coverage_ratio": round(covered_frames / covered_span, 4) if covered_span else 0.0,
        "per_video": [
            {
                "video_id": video["id"], "name": video["name"],
                "exported_frames": video["exported_frames"],
                "covered_span": video["covered_span"],
                "coverage_ratio": video["coverage_ratio"],
                "video_frame_count": video["frame_count"],
                "track_count": video["track_count"],
            }
            for video in videos
        ],
    }

    report: Dict[str, Any] = {
        "format": fmt,
        "taxonomy_rank": taxonomy_rank,
        "generated_at": stamp.isoformat(timespec="seconds"),
        "annotation_count": len(boxes),
        "image_count": len(images),
        "video_count": len(videos),
        "track_count": len(tracks_payload),
        "events": event_report,
        "ignored_annotation_count": len(boxes) - identified_count,
        "coordinate_frame": space_summary,
        "coverage": coverage,
        "split": {
            "strategy": split_plan.strategy,
            "split_by": split_by,
            "seed": split_plan.seed,
            "ratios": split_plan.ratios,
            "composition": composition,
            "forced_train_group_count": len(split_plan.forced_train_groups),
            "replayed_from": split_plan.replayed_from,
            "degraded_groups": degraded_groups,
        },
        "classes": class_payload,
        "frame_stride": {"stride": max(1, int(frame_stride)), "dropped": len(stride_dropped)},
        "bbox_clamped": {
            "count": clamped_boxes,
            "rule": (
                "boîte de suivi débordant de l'image bornée à ses limites - "
                "COCO-VID et MOT décrivent la même boîte bornée"
            ),
        },
        "unreadable_samples": unreadable_samples,
        "tracks_without_class": {
            "count": len(na_tracks),
            "rule": (
                "une piste sans taxon au rang demandé sort en ignore=1 et force "
                "son groupe entier en train"
            ),
            "tracks": na_tracks[:_GROUP_SAMPLE],
        },
        "mot": mot_info,
        "exclusions": exclusions.as_dict(),
    }
    _write_json(staging / REPORT_NAME, report)

    exported_media = {image["media_id"] for image in images}
    covered_sessions = (
        _mark_sessions_exported(session, exported_media)
        if mark_sessions_exported else []
    )

    manifest_core: Dict[str, Any] = {
        "schema": MANIFEST_SCHEMA,
        "dataset_name": dataset_name,
        "dataset_version": dataset_version,
        "created_at": stamp.isoformat(timespec="seconds"),
        "git_commit": run.git_commit,
        "format": fmt,
        "taxonomy_rank": taxonomy_rank,
        "source": {
            "table": "track_samples",
            "db_snapshot_sha256": db_sha,
            "db_snapshot_wal_checkpointed": wal_checkpointed,
            "filter": {
                **{"project_ids": project_ids},
                **({"media_ids": media_ids} if media_ids is not None else {}),
            },
            "media_count": len(videos),
            "image_count": len(images),
            "annotation_count": len(boxes),
            "identified_annotation_count": identified_count,
            "track_count": len(tracks_payload),
            "bbox_clamped_count": clamped_boxes,
            "frame_stride": max(1, int(frame_stride)),
            "frames_dropped_by_stride": len(stride_dropped),
        },
        "coordinate_frame": space_summary,
        "coverage": coverage,
        "tracking": {
            "pivot": export_tracking.COCO_VID_NAME,
            "instance_id": (
                "entier dense propre à cet export ; l'identité durable de la "
                "piste est `track_db_id` (UUID) / `external_track_id`, tous deux "
                "écrits dans coco_vid.json[\"tracks\"]"
            ),
            "frame_id": "index absolu dans le fichier vidéo source",
            "visibility_rule": export_tracking.VISIBILITY_RULE,
            "visibility_by_origin": dict(export_tracking.VISIBILITY_BY_ORIGIN),
            "sample_origins": dict(sorted(Counter(
                box["attributes"]["origin"] for box in boxes
            ).items())),
            "events": event_report,
        },
        "split": {
            "strategy": split_plan.strategy,
            "split_by": split_by,
            "seed": split_plan.seed,
            "ratios": split_plan.ratios,
            "ratios_realized": _realized_ratios(composition),
            "composition": composition,
            "group_count": len(split_plan.split_of_group),
            "forced_train_groups": sorted(split_plan.forced_train_groups)[:_GROUP_SAMPLE],
            "na_rule": {
                "rule": (
                    "val et test intégralement identifiés - tout groupe portant "
                    "une piste non identifiée part en train"
                ),
                "groups_forced_to_train": len(split_plan.forced_train_groups),
                "tracks_concerned": len(na_tracks),
                "groups_moved_from_replay": len(split_plan.na_replay_overrides),
                "replay_overrides": split_plan.na_replay_overrides[:_GROUP_SAMPLE],
            },
            "replayed_from": split_plan.replayed_from,
            "new_group_count": len(split_plan.new_groups),
            "degraded_groups": degraded_groups,
            "groups_file": SPLITS_NAME,
        },
        "class_stats": class_payload,
        "exclusions": {
            "count": len(exclusions),
            "by_reason": exclusions.by_reason(),
            "summary": exclusions.summary(),
            "detail_file": REPORT_NAME,
        },
        "mot": mot_info,
        "sessions_marked_exported": covered_sessions,
    }
    final_dir, manifest = _seal_export(
        session, staging=staging, output_root=output_root, run=run,
        manifest_core=manifest_core, report=report, exclusions=exclusions,
        space_summary=space_summary, dataset_name=dataset_name,
        dataset_version=dataset_version, stamp=stamp, produced=produced,
    )

    print(
        f"Export {fmt} : {len(boxes)} boîte(s) de suivi sur {len(images)} image(s), "
        f"{len(tracks_payload)} piste(s), {len(videos)} vidéo(s) - "
        f"couverture {coverage['coverage_ratio']:.1%} - {exclusions.summary()}"
    )
    if not tracks_by_id:
        print(
            "[!] Aucune piste en base : lancez le suivi automatique sur la page "
            "Mesure avant d'exporter un dataset de suivi."
        )
    if clamped_boxes:
        print(
            f"[!] {clamped_boxes} boîte(s) de suivi débordaient de l'image et ont "
            "été bornées (voir export_report.json, champ bbox_clamped)."
        )
    if coverage["coverage_ratio"] and coverage["coverage_ratio"] < 0.5:
        print(
            f"[!] Couverture de suivi {coverage['coverage_ratio']:.1%} : les "
            "séquences MOT sont pleines de trous. Les numéros de frame sont "
            "relatifs, la correspondance est dans frames_map.csv."
        )
    print(f"Dossier : {final_dir}")

    return ExportResult(
        output_dir=final_dir, manifest=manifest, splits=split_payload,
        report=report, run=run, exclusions=exclusions.rows(),
    )


# ── Comportement : CSV type AVA et JSONL d'intervalles ─────────────────────


def _build_behavior(
    session: Session,
    *,
    staging: Path,
    output_root: Path,
    run: ExportRun,
    split_by: str,
    taxonomy_rank: str,
    fmt: str,
    project_ids: Optional[List[str]],
    media_ids: Optional[List[str]],
    dataset_name: str,
    dataset_version: str,
    seed: int,
    ratios: Dict[str, float],
    frame_stride: int,
    min_instances: int,
    min_media: int,
    rectify_images: bool,
    stamp: datetime,
    db_sha: Optional[str],
    wal_checkpointed: Optional[bool],
    replay_splits: Optional[Path],
    mark_sessions_exported: bool,
    strict_grouping: Optional[float],
    produced: Dict[str, Path],
    ava_hz: float = export_behavior.DEFAULT_AVA_HZ,
    ava_max_gap_s: float = export_behavior.DEFAULT_AVA_MAX_GAP_S,
) -> ExportResult:
    """Dataset de comportement : `events.jsonl`, et `events.csv` pour AVA.

    Aucune image n'est matérialisée : un CSV AVA désigne des instants et des
    boîtes normalisées, pas des fichiers. Le découpage par groupe s'applique
    quand même - `splits.json` dit quel média va dans quel split, et comme un
    média entier ne se coupe jamais, filtrer `events.csv` sur `video_id` suffit
    à obtenir le train/val d'AVA.
    """
    exclusions = ExportExclusions()

    # 1. Intervalles, ramenés en index absolu (les événements de la base sont
    #    en `timeline_legacy`).
    events, media_by_id, tracks_by_id = export_behavior.collect_events(
        session, project_ids, media_ids=media_ids, exclusions=exclusions,
    )
    for row in events:
        media = media_by_id.get(row["media_id"])
        row["media_frame_count"] = (
            int(media.frame_count) if media is not None and media.frame_count else None
        )
    event_media = {row["media_id"] for row in events}
    instance_of_track = export_tracking.instance_ids([
        track for track in tracks_by_id.values() if track.media_id in event_media
    ])

    # 2. Table de labels AVA depuis `event_types`.
    actions, action_of_key = export_behavior.active_action_table(
        session, {str(row["event_type"]) for row in events},
    )

    # 3. Groupes et split - le grain est celui des autres exports.
    pseudo_frames = [
        _TrackAsFrame(key=row["event_id"], media_id=row["media_id"]) for row in events
    ]
    cache: Dict[str, str] = {}
    degraded: Dict[str, str] = {}
    group_of_frame = {
        frame.key: group_key_for_frame(session, frame, split_by, cache, degraded=degraded)
        for frame in pseudo_frames
    }
    degraded_groups = grouping_degradation(
        split_by, event_media, degraded, group_of_frame,
        strict_grouping=strict_grouping,
    )
    classes_of_group: Dict[str, Set[str]] = defaultdict(set)
    for row in events:
        classes_of_group[group_of_frame[row["event_id"]]].add(row["event_type"])
    class_weight = Counter(row["event_type"] for row in events)
    replay = load_replay_splits(replay_splits) if replay_splits else None
    split_plan = plan_splits(
        pseudo_frames, group_of_frame, split_by=split_by, seed=seed, ratios=ratios,
        classes_of_group=classes_of_group, class_weight=dict(class_weight),
        na_groups=set(), replay=replay,
    )
    split_of_event = {
        row["event_id"]: split_plan.split_of_frame(row["event_id"]) for row in events
    }

    # 4. Écriture.
    written = [export_behavior.write_events_jsonl(
        staging, events=events, instance_of_track=instance_of_track,
    )]
    ava_rows: List[Dict[str, Any]] = []
    ava_report: Dict[str, Any] = {}
    if fmt == FORMAT_AVA:
        ava_rows, ava_report = export_behavior.build_ava_rows(
            session, events, action_of_key, instance_of_track,
            ava_hz=ava_hz, max_gap_s=ava_max_gap_s, exclusions=exclusions,
        )
        written += export_behavior.write_ava(
            staging, rows=ava_rows, actions=actions, action_of_key=action_of_key,
        )
        # Descripteur Frictionless des deux tables CSV. Écrit **avant** le
        # scellement : il entre donc de lui-même dans `files[]` du manifeste
        # et dans son condensé. `created` vient de `stamp`, fourni par
        # l'appelant - jamais d'horodatage spontané dans un fichier haché.
        datapackage.write_behavior_datapackage(
            staging,
            events_csv=export_behavior.EVENTS_CSV,
            actions_csv=export_behavior.ACTIONS_CSV,
            created=stamp.isoformat(timespec="seconds"),
            dataset_name=dataset_name,
            dataset_version=dataset_version,
        )
        written.append(DATAPACKAGE_NAME)

    composition = _behavior_composition(
        events, ava_rows, split_of_event, group_of_frame,
    )
    pseudo_images = [
        {
            "split": split_of_event[row["event_id"]],
            "media_id": row["media_id"],
            "group": group_of_frame[row["event_id"]],
        }
        for row in events
    ]
    split_payload = _splits_payload(
        split_plan, pseudo_images, session, composition=composition,
    )
    split_payload["unit"] = "temporal_events"
    _write_json(staging / SPLITS_NAME, split_payload)

    by_source = dict(sorted(Counter(row["source"] for row in events).items()))
    metrics = export_behavior.events_metrics(events)
    report: Dict[str, Any] = {
        "format": fmt,
        "taxonomy_rank": taxonomy_rank,
        "generated_at": stamp.isoformat(timespec="seconds"),
        # Un export de comportement ne produit pas d'image : le compte
        # d'annotations est celui des lignes réellement écrites.
        "annotation_count": len(ava_rows) if fmt == FORMAT_AVA else len(events),
        "image_count": 0,
        "event_count": len(events),
        "events_by_source": by_source,
        "media_count": len(event_media),
        "files": written,
        "split": {
            "strategy": split_plan.strategy,
            "split_by": split_by,
            "seed": split_plan.seed,
            "composition": composition,
            "degraded_groups": degraded_groups,
        },
        "ava": ava_report,
        "actions": [
            {"action_id": action_of_key[str(a["key"])], "key": a["key"],
             "label": a["label"], "usage": a.get("usage", 0)}
            for a in sorted(actions, key=lambda a: action_of_key[str(a["key"])])
        ],
        "metrics": metrics,
        "exclusions": exclusions.as_dict(),
    }
    _write_json(staging / REPORT_NAME, report)

    covered_sessions = (
        _mark_sessions_exported(session, event_media) if mark_sessions_exported else []
    )

    manifest_core: Dict[str, Any] = {
        "schema": MANIFEST_SCHEMA,
        "dataset_name": dataset_name,
        "dataset_version": dataset_version,
        "created_at": stamp.isoformat(timespec="seconds"),
        "git_commit": run.git_commit,
        "format": fmt,
        "taxonomy_rank": taxonomy_rank,
        "source": {
            "table": "temporal_events",
            "db_snapshot_sha256": db_sha,
            "db_snapshot_wal_checkpointed": wal_checkpointed,
            "filter": {
                **{"project_ids": project_ids},
                **({"media_ids": media_ids} if media_ids is not None else {}),
            },
            "media_count": len(event_media),
            "image_count": 0,
            "event_count": len(events),
            "events_by_source": by_source,
            "annotation_count": len(ava_rows) if fmt == FORMAT_AVA else len(events),
        },
        # Aucune image n'est écrite, mais les boîtes AVA sont normalisées dans
        # l'espace où elles ont été tracées : le dire évite qu'on les replace
        # sur une frame brute.
        "coordinate_frame": {
            "image_space": "stereo_rectified_left",
            "frame_index_convention": "absolute",
            "bbox_units": "normalized",
            "note": (
                "bbox AVA normalisées par les dimensions du média ; les "
                "positions viennent de track_samples, tracées sur l'image "
                "rectifiée gauche affichée par l'application"
            ),
        },
        "behavior": {
            "events_file": export_behavior.EVENTS_JSONL,
            "dataset_rule": (
                "seuls les événements source='manual' entrent dans le dataset "
                "AVA ; les événements 'heuristic' sont des présomptions "
                "automatiques, exclus avec leur raison et comptés ici"
            ),
            "duration_convention": "(frame_end − frame_start + 1) / fps",
            "jsonl_scope": (
                "tous les événements, chacun portant sa source et "
                "in_ava_dataset - la vue de revue doit montrer ce que le "
                "dataset écarte"
            ),
        },
        "ava": (
            {
                "events_csv": export_behavior.EVENTS_CSV,
                "actions_csv": export_behavior.ACTIONS_CSV,
                "columns": list(export_behavior.AVA_COLUMNS),
                "has_header": True,
                "action_id": (
                    "index dense propre à cet export, calculé sur (sort_order, "
                    "key) ; l'identité durable est `key` / `event_type_id`, "
                    "toutes deux dans actions.csv"
                ),
                "track_id": "instance_id, le même entier que l'export de suivi",
                "split_usage": (
                    "events.csv n'a pas de colonne de split : un média entier "
                    "va d'un seul côté du découpage, filtrer sur video_id avec "
                    f"{SPLITS_NAME} suffit"
                ),
                **ava_report,
            }
            if fmt == FORMAT_AVA else {}
        ),
        "metrics": metrics,
        "split": {
            "strategy": split_plan.strategy,
            "split_by": split_by,
            "seed": split_plan.seed,
            "ratios": split_plan.ratios,
            "composition": composition,
            "group_count": len(split_plan.split_of_group),
            "degraded_groups": degraded_groups,
            "groups_file": SPLITS_NAME,
            "unit": "temporal_events",
        },
        "exclusions": {
            "count": len(exclusions),
            "by_reason": exclusions.by_reason(),
            "summary": exclusions.summary(),
            "detail_file": REPORT_NAME,
        },
        "sessions_marked_exported": covered_sessions,
    }
    final_dir, manifest = _seal_export(
        session, staging=staging, output_root=output_root, run=run,
        manifest_core=manifest_core, report=report, exclusions=exclusions,
        space_summary={}, dataset_name=dataset_name,
        dataset_version=dataset_version, stamp=stamp, produced=produced,
    )

    if fmt == FORMAT_AVA:
        print(
            f"Export AVA : {len(ava_rows)} ligne(s) à {ava_hz} Hz depuis "
            f"{ava_report.get('events_kept', 0)}/{len(events)} événement(s) "
            f"manuel(s) - {exclusions.summary()}"
        )
        if not ava_rows:
            print(
                "[!] Aucune ligne AVA produite. Seules les annotations "
                "humaines (source='manual') entrent dans un dataset de "
                "comportement ; voir manifest.ava.events_excluded."
            )
    else:
        print(
            f"Export événements : {len(events)} intervalle(s) écrits en JSONL "
            f"({by_source}) - {exclusions.summary()}"
        )
    print(f"Dossier : {final_dir}")

    return ExportResult(
        output_dir=final_dir, manifest=manifest, splits=split_payload,
        report=report, run=run, exclusions=exclusions.rows(),
    )


def _behavior_composition(
    events: Sequence[Dict[str, Any]],
    ava_rows: Sequence[Dict[str, Any]],
    split_of_event: Dict[str, str],
    group_of_event: Dict[str, str],
) -> Dict[str, Dict[str, Any]]:
    """Ce que chaque split contient - en événements, en lignes et en durée."""
    rows_by_event: Counter = Counter(row["event_id"] for row in ava_rows)
    out: Dict[str, Dict[str, Any]] = {}
    for split in SPLIT_NAMES:
        selected = [e for e in events if split_of_event.get(e["event_id"]) == split]
        out[split] = {
            # `images` reste renseigné (à 0) : plusieurs lecteurs du manifeste
            # comptent dessus, et un export de comportement n'en produit aucune.
            "images": 0,
            "events": len(selected),
            "annotations": sum(rows_by_event.get(e["event_id"], 0) for e in selected),
            "ignored_annotations": 0,
            "media": len({e["media_id"] for e in selected}),
            "groups": len({group_of_event[e["event_id"]] for e in selected}),
            "duration_s": round(sum(e["duration_s"] or 0.0 for e in selected), 3),
            "classes": dict(sorted(Counter(e["event_type"] for e in selected).items())),
        }
    return out


def _class_payload(
    plan: ClassPlan,
    images: List[Dict[str, Any]],
    boxes: List[Dict[str, Any]],
) -> Dict[str, Any]:
    """Effectifs par classe **tels qu'ils sont dans le dataset produit**.

    Le plan de classes est calculé sur les annotations collectées ; celles dont
    l'image n'a finalement pas pu être écrite (fichier disparu) n'entrent pas
    dans le dataset. Le manifeste doit décrire ce qui est livré, pas ce qui
    était prévu.
    """
    media_of_image = {img["id"]: img["media_id"] for img in images}
    instances: Counter = Counter()
    per_image: Dict[str, Set[int]] = defaultdict(set)
    per_media: Dict[str, Set[str]] = defaultdict(set)
    ignored = 0
    for box in boxes:
        name = box["class_name"]
        if name is None:
            ignored += 1
            continue
        instances[name] += 1
        per_image[name].add(box["image_id"])
        per_media[name].add(media_of_image.get(box["image_id"], ""))

    payload = plan.as_dict()
    payload["stats"] = {
        name: {
            "instances": instances.get(name, 0),
            "images": len(per_image.get(name, ())),
            "media": len(per_media.get(name, ())),
        }
        for name in plan.names
    }
    payload["ignored_instances"] = ignored
    return payload


def _realized_ratios(composition: Dict[str, Dict[str, Any]]) -> Dict[str, float]:
    """Ratios réellement obtenus - ils s'écartent des ratios demandés quand il
    y a trop peu de groupes pour les respecter sans casser un groupe."""
    total = sum(row["images"] for row in composition.values())
    if not total:
        return {name: 0.0 for name in SPLIT_NAMES}
    return {
        name: round(composition[name]["images"] / total, 4) for name in SPLIT_NAMES
    }


def _split_composition(
    images: List[Dict[str, Any]],
    boxes: List[Dict[str, Any]],
    split_plan: SplitPlan,
) -> Dict[str, Dict[str, Any]]:
    """Ce que chaque split contient réellement, une fois les images écrites."""
    out: Dict[str, Dict[str, Any]] = {}
    for split in SPLIT_NAMES:
        split_images = [img for img in images if img["split"] == split]
        split_boxes = [box for box in boxes if box["split"] == split]
        classes = Counter(
            box["class_name"] for box in split_boxes if box["class_name"]
        )
        out[split] = {
            "images": len(split_images),
            "annotations": len(split_boxes),
            "ignored_annotations": sum(
                1 for box in split_boxes if box["class_name"] is None
            ),
            "groups": len({img["group"] for img in split_images}),
            "media": len({img["media_id"] for img in split_images}),
            "classes": dict(sorted(classes.items())),
        }
    return out


def _splits_payload(
    split_plan: SplitPlan,
    images: List[Dict[str, Any]],
    session: Session,
    *,
    composition: Dict[str, Dict[str, Any]],
) -> Dict[str, Any]:
    """`splits.json` - le split est une donnée, pas l'effet de bord d'une graine."""
    media_by_split: Dict[str, Set[str]] = {name: set() for name in SPLIT_NAMES}
    groups_by_split: Dict[str, Set[str]] = {name: set() for name in SPLIT_NAMES}
    for image in images:
        media_by_split[image["split"]].add(image["media_id"])
        groups_by_split[image["split"]].add(image["group"])

    session_by_split: Dict[str, Set[str]] = {name: set() for name in SPLIT_NAMES}
    for split, media_ids in media_by_split.items():
        for media_id in media_ids:
            capture = sessions_mod.find_session_for_media(session, media_id)
            if capture is not None:
                session_by_split[split].add(capture.id)

    return {
        "strategy": split_plan.strategy,
        "split_by": split_plan.split_by,
        "seed": split_plan.seed,
        "ratios": split_plan.ratios,
        "ratios_realized": _realized_ratios(composition),
        "forced_train_groups": sorted(split_plan.forced_train_groups),
        "replayed_from": split_plan.replayed_from,
        "new_groups": sorted(split_plan.new_groups),
        "groups": dict(sorted(split_plan.split_of_group.items())),
        "group_ids": {
            name: sorted(groups_by_split[name]) for name in SPLIT_NAMES
        },
        "media_ids": {name: sorted(media_by_split[name]) for name in SPLIT_NAMES},
        "session_ids": {name: sorted(session_by_split[name]) for name in SPLIT_NAMES},
        "composition": composition,
    }


def _write_coco(
    staging: Path,
    *,
    images: List[Dict[str, Any]],
    boxes: List[Dict[str, Any]],
    plan: ClassPlan,
    space_summary: Dict[str, Any],
    exclusions: ExportExclusions,
    dataset_name: str,
    dataset_version: str,
    stamp: datetime,
) -> List[str]:
    """`instances_fish.json` + `instances_<rang>.json` - mêmes images, mêmes ids.

    Le premier sert le détecteur mono-classe (tout est du poisson, zéro
    `ignore`), le second le modèle taxonomique (classes réelles + `ignore` sur
    ce qui n'a pas pu être identifié). Les identifiants d'image et d'annotation
    sont **les mêmes** dans les deux : on peut comparer les deux modèles boîte
    à boîte.
    """
    coco_images = [
        {k: v for k, v in image.items() if k != "group"} for image in images
    ]
    written: List[str] = []

    def _dump(path: Path, categories: List[dict], annotations: List[dict], ignored: int):
        payload = {
            "info": {
                "description": f"{dataset_name} - export annotations",
                "version": dataset_version,
                "date_created": stamp.isoformat(timespec="seconds"),
                "year": stamp.year,
                **space_summary,
                "ignored_annotation_count": ignored,
            },
            "licenses": [],
            "categories": categories,
            "images": coco_images,
            "annotations": annotations,
            # Champ libre : pycocotools ignore les clés inconnues, et l'export
            # ne doit jamais laisser croire qu'il a tout sorti.
            "excluded_annotations": exclusions.rows(),
        }
        _write_json(path, payload)
        written.append(path.name)

    fish_annotations = [
        {
            "id": box["id"],
            "image_id": box["image_id"],
            "category_id": 1,
            "bbox": box["bbox"],
            "area": box["area"],
            "iscrowd": 0,
            "ignore": 0,
            "attributes": box["attributes"],
        }
        for box in boxes
    ]
    _dump(
        staging / "instances_fish.json",
        [{"id": 1, "name": FISH_CLASS, "supercategory": FISH_CLASS}],
        fish_annotations,
        0,
    )

    if plan.rank == RANK_FISH:
        return written

    categories = [
        {"id": index + 1, "name": name, "supercategory": plan.rank}
        for index, name in enumerate(plan.names)
    ]
    categories.append({
        "id": len(plan.names) + 1,
        "name": UNIDENTIFIED_CATEGORY_NAME,
        "supercategory": plan.rank,
        "description": (
            "Poisson observé mais non identifiable à ce rang (ou classe sous le "
            "seuil d'effectif) - annotations marquées ignore=1 / iscrowd=1."
        ),
    })
    rank_annotations = []
    ignored = 0
    for box in boxes:
        is_ignored = box["class_name"] is None
        ignored += 1 if is_ignored else 0
        rank_annotations.append({
            "id": box["id"],
            "image_id": box["image_id"],
            "category_id": plan.category_id(box["class_name"]),
            "bbox": box["bbox"],
            "area": box["area"],
            # ignore=1 : zone exclue de la perte et de l'évaluation.
            "iscrowd": 1 if is_ignored else 0,
            "ignore": 1 if is_ignored else 0,
            "attributes": box["attributes"],
        })
    _dump(staging / f"instances_{plan.rank}.json", categories, rank_annotations, ignored)
    return written


def _write_yolo(
    staging: Path,
    *,
    images: List[Dict[str, Any]],
    boxes: List[Dict[str, Any]],
    plan: ClassPlan,
    excluded_images: int = 0,
) -> Dict[str, Any]:
    """Dérive le dataset YOLO **depuis la sortie COCO**, jamais depuis la base.

    Les images sont partagées (lien matériel, copie à défaut) et seules celles
    sans `ignore` sont reprises : YOLO n'a pas de zone neutre, garder une frame
    à poisson non identifié apprendrait au modèle que ce poisson est du fond.
    """
    import yaml

    yolo_root = staging / "yolo"
    kept_ids = {image["id"] for image in images}
    by_image: Dict[int, List[Dict[str, Any]]] = defaultdict(list)
    for box in boxes:
        if box["image_id"] in kept_ids:
            by_image[box["image_id"]].append(box)

    per_split: Counter = Counter()
    labels = 0
    delivered: Counter = Counter()
    for image in images:
        split = image["split"]
        source = staging / "images" / image["file_name"]
        target = yolo_root / "images" / image["file_name"]
        _link_or_copy(source, target)
        lines = []
        for box in by_image[image["id"]]:
            cx, cy, box_w, box_h = box["yolo"]
            cls_id = plan.index(box["class_name"])
            delivered[box["class_name"]] += 1
            lines.append(f"{cls_id} {cx:.6f} {cy:.6f} {box_w:.6f} {box_h:.6f}")
        label_path = yolo_root / "labels" / split / f"{Path(image['file_name']).stem}.txt"
        label_path.parent.mkdir(parents=True, exist_ok=True)
        label_path.write_text(
            "\n".join(lines) + ("\n" if lines else ""), encoding="utf-8",
        )
        per_split[split] += 1
        labels += len(lines)

    for split in SPLIT_NAMES:
        (yolo_root / "images" / split).mkdir(parents=True, exist_ok=True)
        (yolo_root / "labels" / split).mkdir(parents=True, exist_ok=True)

    # Une classe peut exister dans la class-map et n'avoir **aucune** instance
    # livrée en YOLO : ses images ont toutes été écartées parce qu'elles
    # portaient aussi un `ignore`. Décision retenue : on **garde** l'entrée dans
    # `names` - la retirer renumérotarait les classes et désynchroniserait le
    # dérivé de `instances_<rang>.json`, dont les identifiants sont un contrat
    # (« mêmes ids dans les deux jeux de catégories ») - et on **signale**
    # l'écart, dans le YAML, dans le manifeste et à l'écran.
    missing_names = [name for name in plan.names if not delivered.get(name)]

    # `path` volontairement absent : Ultralytics retombe alors sur le dossier
    # du fichier YAML, ce qui rend le dataset déplaçable - et l'export
    # reproductible au bit près (aucun chemin absolu dans un fichier haché).
    data_yaml = {
        "train": "images/train",
        "val": "images/val",
        "test": "images/test",
        "names": {index: name for index, name in enumerate(plan.names)},
    }
    with (yolo_root / "data.yaml").open("w", encoding="utf-8") as handle:
        handle.write(
            "# Genere par export_core.py - derive de instances_"
            f"{plan.rank}.json (images a ignore exclues).\n"
            "# 'path' omis volontairement : la racine est le dossier de ce fichier.\n"
        )
        handle.write(
            "# Instances livrees par classe : "
            + (
                ", ".join(f"{name}={delivered.get(name, 0)}" for name in plan.names)
                or "aucune classe"
            )
            + "\n"
        )
        if missing_names:
            handle.write(
                "# [!] Classe(s) sans aucune instance livree ici (images "
                "ecartees car porteuses d'un ignore) : "
                + ", ".join(missing_names)
                + "\n# Les index restent ceux de instances_"
                + f"{plan.rank}.json : ne les renumerotez pas.\n"
            )
        yaml.dump(data_yaml, handle, default_flow_style=False, allow_unicode=True, sort_keys=True)

    return {
        "root": "yolo",
        "data_yaml": "yolo/data.yaml",
        "images": dict(sorted(per_split.items())),
        "image_count": len(images),
        "label_count": labels,
        "excluded_image_count": excluded_images,
        "excluded_reason": (
            "image portant au moins une annotation ignore : YOLO n'a pas de "
            "zone neutre, la garder apprendrait que ce poisson est du fond"
        ),
        "class_count": len(plan.names),
        "instances_per_class": {name: delivered.get(name, 0) for name in plan.names},
        "names_without_instances": missing_names,
        "names_policy": (
            "names aligné sur instances_<rang>.json : une classe sans instance "
            "livrée reste déclarée (les index sont un contrat entre les deux "
            "jeux de catégories), l'écart est signalé plutôt que masqué"
        ),
    }


def _disk_bytes(root: Path) -> int:
    """Poids réel sur disque : un fichier partagé par lien matériel compte une fois.

    Le dérivé YOLO partage les images de l'export COCO ; les additionner
    afficherait le double de la place occupée.
    """
    total = 0
    seen: Set[tuple] = set()
    for path in Path(root).rglob("*"):
        if not path.is_file():
            continue
        try:
            info = path.stat()
        except OSError:
            continue
        key = (info.st_dev, info.st_ino)
        if info.st_ino and key in seen:
            continue
        seen.add(key)
        total += info.st_size
    return total


def _file_digests(root: Path) -> List[Dict[str, Any]]:
    """Empreinte et taille de **chaque** fichier écrit, chemins relatifs."""
    rows: List[Dict[str, Any]] = []
    for path in sorted(root.rglob("*"), key=lambda p: p.relative_to(root).as_posix()):
        if not path.is_file():
            continue
        rows.append({
            "path": path.relative_to(root).as_posix(),
            "sha256": sha256_file(path),
            "bytes": path.stat().st_size,
        })
    return rows


def _mark_sessions_exported(session: Session, media_ids: Set[str]) -> List[Dict[str, str]]:
    """Passe à `exported` les sessions dont un média est entré dans le dataset.

    Décision superviseur : le statut est posé **automatiquement** par le noyau
    d'export, il n'y a pas de bouton. Une session dont la vidéo a servi à
    fabriquer un dataset est exportée, point.
    """
    if not media_ids:
        return []
    marked: List[Dict[str, str]] = []
    for capture in session.scalars(select(CaptureSession)):
        owned = set(sessions_mod.session_media_ids(session, capture))
        if not owned or not (owned & media_ids):
            continue
        if capture.status != sessions_mod.STATUS_EXPORTED:
            capture.status = sessions_mod.STATUS_EXPORTED
            capture.updated_at = datetime.utcnow()
        marked.append({"session_id": capture.id, "name": capture.name})
    if marked:
        session.flush()
    return sorted(marked, key=lambda row: row["session_id"])


def latest_export_run(
    session: Session, *, fmt: Optional[str] = None, status: str = EXPORT_STATUS_COMPLETED,
) -> Optional[ExportRun]:
    """Dernier export réussi enregistré dans `export_runs`.

    C'est ce que doivent viser les outils aval (audit, réentraînement) : le
    dossier réellement produit par le bouton, pas un chemin deviné.
    """
    stmt = select(ExportRun).order_by(ExportRun.created_at.desc(), ExportRun.id)
    for row in session.scalars(stmt):
        if fmt and row.format != fmt:
            continue
        if status and row.status != status:
            continue
        if row.output_path and Path(row.output_path).is_dir():
            return row
    return None
