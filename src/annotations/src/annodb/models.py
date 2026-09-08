"""SQLAlchemy ORM models."""

from __future__ import annotations

from datetime import datetime
from typing import Optional

from sqlalchemy import (
    Boolean,
    DateTime,
    Float,
    ForeignKey,
    Index,
    Integer,
    LargeBinary,
    String,
    Text,
    UniqueConstraint,
    text,
)
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship


class Base(DeclarativeBase):
    pass


class TaxonNode(Base):
    __tablename__ = "taxon_nodes"

    id: Mapped[str] = mapped_column(String, primary_key=True)
    parent_id: Mapped[Optional[str]] = mapped_column(String, ForeignKey("taxon_nodes.id"))
    rank: Mapped[str] = mapped_column(String, nullable=False)
    scientific_name: Mapped[str] = mapped_column(String, nullable=False)
    common_name: Mapped[Optional[str]] = mapped_column(String)
    is_provisional: Mapped[bool] = mapped_column(Boolean, default=False)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    parent: Mapped[Optional["TaxonNode"]] = relationship(remote_side=[id])


class Project(Base):
    __tablename__ = "projects"

    id: Mapped[str] = mapped_column(String, primary_key=True)
    name: Mapped[str] = mapped_column(String, unique=True, nullable=False)
    description: Mapped[Optional[str]] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)


class MediaAsset(Base):
    __tablename__ = "media_assets"

    id: Mapped[str] = mapped_column(String, primary_key=True)
    project_id: Mapped[str] = mapped_column(String, ForeignKey("projects.id"), nullable=False)
    media_type: Mapped[str] = mapped_column(String, nullable=False)
    rel_path: Mapped[str] = mapped_column(String, nullable=False)
    sha256: Mapped[Optional[str]] = mapped_column(String)
    # Signature du fichier au moment du calcul du sha256 : tant que la taille et
    # la date de modification n'ont pas bougé, l'empreinte est réutilisée telle
    # quelle. Sans ce cache, chaque annotation relisait plusieurs Go de vidéo.
    sha256_size: Mapped[Optional[int]] = mapped_column(Integer)
    sha256_mtime: Mapped[Optional[float]] = mapped_column(Float)
    width: Mapped[Optional[int]] = mapped_column(Integer)
    height: Mapped[Optional[int]] = mapped_column(Integer)
    fps: Mapped[Optional[float]] = mapped_column(Float)
    frame_count: Mapped[Optional[int]] = mapped_column(Integer)
    captured_at: Mapped[Optional[datetime]] = mapped_column(DateTime)
    site: Mapped[Optional[str]] = mapped_column(String)
    session_title: Mapped[Optional[str]] = mapped_column(String)
    notes: Mapped[Optional[str]] = mapped_column(Text)
    session_date: Mapped[Optional[datetime]] = mapped_column(DateTime)
    # Calibration active au moment où le média a été rattaché à une session :
    # une recalibration ne doit pas invalider silencieusement les boîtes déjà
    # tracées dans l'espace rectifié d'alors.
    calibration_id: Mapped[Optional[str]] = mapped_column(
        String, ForeignKey("calibrations.id")
    )
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)

    __table_args__ = (UniqueConstraint("project_id", "rel_path"),)


class Calibration(Base):
    """Un jeu de paramètres stéréo effectivement utilisé, identifié par son sha256.

    Le profil actif vivait uniquement sur disque (`camera_parameters/`) : rien
    en base ne disait avec quelle calibration une boîte avait été tracée, donc
    une recalibration invalidait tout l'historique sans laisser de trace.

    Le `sha256` porte le jeu de `.npy` (cf. `rectify.CALIB_FILES`) : deux
    sessions calibrées à l'identique partagent **une seule** ligne.
    """

    __tablename__ = "calibrations"

    id: Mapped[str] = mapped_column(String, primary_key=True)
    profile_name: Mapped[str] = mapped_column(String, nullable=False)
    sha256: Mapped[str] = mapped_column(String, nullable=False)
    # alpha de cv2.stereoRectify - 0 dans toute l'application.
    alpha: Mapped[Optional[float]] = mapped_column(Float)
    image_width: Mapped[Optional[int]] = mapped_column(Integer)
    image_height: Mapped[Optional[int]] = mapped_column(Integer)
    baseline_mm: Mapped[Optional[float]] = mapped_column(Float)
    stereo_rmse: Mapped[Optional[float]] = mapped_column(Float)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)


class Annotator(Base):
    """Identité réelle de la personne qui annote.

    `author` valait `'operator'` en dur sur toutes les lignes : impossible de
    savoir qui avait identifié quoi, ni de créditer un jeu de données publié.
    L'ORCID est facultatif - il sert à créditer sans ambiguïté.
    """

    __tablename__ = "annotators"

    id: Mapped[str] = mapped_column(String, primary_key=True)
    display_name: Mapped[str] = mapped_column(String, nullable=False)
    orcid: Mapped[Optional[str]] = mapped_column(String)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)


class CaptureSession(Base):
    """Une session de terrain = une journée/campagne, avec plusieurs paires.

    `left_media_id` / `right_media_id` restent le pointeur compatible vers la
    paire ouverte le plus récemment. La liste complète vit dans
    `session_media_pairs` : une journée peut contenir autant de prises stéréo
    que nécessaire sans créer de fausses sessions.

    `frame_offset` est le décalage de synchro (`absolu = timeline + offset`)
    **figé** au moment où la session devient active. Sans lui, la relecture des
    lignes historiques dépendrait du `sync_frames.npy` courant, qui change dès
    qu'une nouvelle synchro est faite.
    """

    __tablename__ = "sessions"

    id: Mapped[str] = mapped_column(String, primary_key=True)
    name: Mapped[str] = mapped_column(String, nullable=False)
    site: Mapped[str] = mapped_column(String, nullable=False)
    session_date: Mapped[datetime] = mapped_column(DateTime, nullable=False)
    operator: Mapped[Optional[str]] = mapped_column(String)
    # planned : prévue, sans vidéos - active : paire attachée, en cours
    # done : annotation terminée - exported : dataset produit.
    status: Mapped[str] = mapped_column(String, default="planned")
    left_media_id: Mapped[Optional[str]] = mapped_column(
        String, ForeignKey("media_assets.id")
    )
    right_media_id: Mapped[Optional[str]] = mapped_column(
        String, ForeignKey("media_assets.id")
    )
    calibration_profile: Mapped[Optional[str]] = mapped_column(String)
    calibration_sha256: Mapped[Optional[str]] = mapped_column(String)
    # Ligne `calibrations` correspondante - le couple profil/sha reste dupliqué
    # ici pour ne pas casser la lecture des sessions déjà enregistrées.
    calibration_id: Mapped[Optional[str]] = mapped_column(
        String, ForeignKey("calibrations.id")
    )
    frame_offset: Mapped[Optional[int]] = mapped_column(Integer)
    notes: Mapped[Optional[str]] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime, default=datetime.utcnow, onupdate=datetime.utcnow
    )


class SessionMediaPair(Base):
    """Une prise vidéo stéréo appartenant à une session de terrain."""

    __tablename__ = "session_media_pairs"

    id: Mapped[str] = mapped_column(String, primary_key=True)
    session_id: Mapped[str] = mapped_column(
        String, ForeignKey("sessions.id", ondelete="CASCADE"), nullable=False,
    )
    position: Mapped[int] = mapped_column(Integer, default=0)
    left_media_id: Mapped[Optional[str]] = mapped_column(
        String, ForeignKey("media_assets.id", ondelete="SET NULL")
    )
    right_media_id: Mapped[Optional[str]] = mapped_column(
        String, ForeignKey("media_assets.id", ondelete="SET NULL")
    )
    frame_offset: Mapped[Optional[int]] = mapped_column(Integer)
    calibration_profile: Mapped[Optional[str]] = mapped_column(String)
    calibration_sha256: Mapped[Optional[str]] = mapped_column(String)
    calibration_id: Mapped[Optional[str]] = mapped_column(
        String, ForeignKey("calibrations.id")
    )
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime, default=datetime.utcnow, onupdate=datetime.utcnow
    )

    __table_args__ = (
        UniqueConstraint("session_id", "position"),
    )


class Track(Base):
    __tablename__ = "tracks"

    id: Mapped[str] = mapped_column(String, primary_key=True)
    media_id: Mapped[str] = mapped_column(String, ForeignKey("media_assets.id"), nullable=False)
    external_track_id: Mapped[int] = mapped_column(Integer, nullable=False)
    taxon_node_id: Mapped[Optional[str]] = mapped_column(String, ForeignKey("taxon_nodes.id"))
    # Autorité de la détermination propagée depuis une observation. Un taxon
    # ByteTrack seul reste une proposition et ne devient jamais scientifique.
    identification_status: Mapped[Optional[str]] = mapped_column(String)
    first_frame: Mapped[int] = mapped_column(Integer, default=0)
    last_frame: Mapped[int] = mapped_column(Integer, default=0)
    source: Mapped[str] = mapped_column(String, default="manual")
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)

    __table_args__ = (UniqueConstraint("media_id", "external_track_id", "source"),)


class TrackSample(Base):
    __tablename__ = "track_samples"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    track_id: Mapped[str] = mapped_column(String, ForeignKey("tracks.id"), nullable=False)
    frame_index: Mapped[int] = mapped_column(Integer, nullable=False)
    cx: Mapped[float] = mapped_column(Float, nullable=False)
    cy: Mapped[float] = mapped_column(Float, nullable=False)
    bbox_json: Mapped[str] = mapped_column(Text, nullable=False)

    position_x_mm: Mapped[Optional[float]] = mapped_column(Float)
    position_y_mm: Mapped[Optional[float]] = mapped_column(Float)
    position_z_mm: Mapped[Optional[float]] = mapped_column(Float)
    match_score: Mapped[Optional[float]] = mapped_column(Float)
    match_method: Mapped[Optional[str]] = mapped_column(String)

    # auto : produit par le tracker - keyframe : posé ou corrigé à la main -
    # interpolated : calculé entre deux keyframes.
    origin: Mapped[str] = mapped_column(String, default="auto")
    edited_by: Mapped[Optional[str]] = mapped_column(String)

    __table_args__ = (UniqueConstraint("track_id", "frame_index"),)


class SpatialAnnotation(Base):
    __tablename__ = "spatial_annotations"

    id: Mapped[str] = mapped_column(String, primary_key=True)
    media_id: Mapped[str] = mapped_column(String, ForeignKey("media_assets.id"), nullable=False)
    frame_index: Mapped[int] = mapped_column(Integer, default=0)
    # absolute : index dans le fichier vidéo source (convention cible) -
    # timeline_legacy : index relatif au début de la fenêtre synchronisée,
    # posé par la migration sur les lignes antérieures.
    frame_ref: Mapped[Optional[str]] = mapped_column(String)
    geom_type: Mapped[str] = mapped_column(String, nullable=False)
    geometry_json: Mapped[str] = mapped_column(Text, nullable=False)
    taxon_node_id: Mapped[Optional[str]] = mapped_column(String, ForeignKey("taxon_nodes.id"))
    track_id: Mapped[Optional[str]] = mapped_column(String, ForeignKey("tracks.id"))
    confidence: Mapped[Optional[float]] = mapped_column(Float)
    is_provisional: Mapped[bool] = mapped_column(Boolean, default=False)
    author: Mapped[Optional[str]] = mapped_column(String)
    source: Mapped[str] = mapped_column(String, default="manual")

    # ── Colonnes DÉPRÉCIÉES (phase 7) ───────────────────────────────────
    # Elles restent en base : reconstruire `spatial_annotations` pour trois
    # colonnes vides ferait courir plus de risque au jeu de données réel
    # qu'elles n'en font peser (décision superviseur). Mais **rien ne les
    # écrit ni ne les lit** dans le code vivant, et rien ne doit recommencer :
    # une colonne toujours nulle qu'un export publie laisse croire qu'elle a
    # été renseignée.
    #
    # `cvat_task_id` : jamais écrite, par aucun chemin d'import. 0 ligne sur
    #   les 6 624 de la base de travail.
    # `cvat_shape_id` : écrite par le seul import CVAT **XML**
    #   (`ingest_cvat._ingest_xml`) ; l'import réel s'est fait par le chemin
    #   ZIP/YOLO, d'où 0 ligne renseignée. Conservée tant que ce chemin
    #   d'import existe - mais ne pas la lire comme si elle l'était toujours.
    # `is_grazing` : remplacée par la table `temporal_events` (intervalles
    #   typés, catalogue `event_types` ouvert). 1 ligne héritée sur 6 624, à
    #   `0`. La vérité du broutage est l'intervalle, pas un drapeau par boîte -
    #   l'export de crops la publiait encore, ce qui donnait « is_grazing:
    #   null » sur 100 % des lignes.
    cvat_task_id: Mapped[Optional[int]] = mapped_column(Integer)
    cvat_shape_id: Mapped[Optional[int]] = mapped_column(Integer)
    is_grazing: Mapped[Optional[bool]] = mapped_column(Boolean)

    measurement_mm: Mapped[Optional[float]] = mapped_column(Float)
    position_x_mm: Mapped[Optional[float]] = mapped_column(Float)
    position_y_mm: Mapped[Optional[float]] = mapped_column(Float)
    position_z_mm: Mapped[Optional[float]] = mapped_column(Float)

    # Copie locale autonome de la bbox. Elle permet d'enrichir Fishial même si
    # la vidéo source a été archivée ou supprimée après l'export COCO.
    crop_path: Mapped[Optional[str]] = mapped_column(String)

    # ── Provenance du modèle (immuable par ajout) ───────────────────────
    # Renseignés à l'écriture quand `source='model'`. La validation humaine
    # AJOUTE `reviewed_by`/`reviewed_at` : elle n'efface jamais ces trois
    # colonnes ni `confidence`, sans quoi on ne pourrait plus mesurer si le
    # modèle avait raison - et on s'entraînerait sur ses propres erreurs.
    model_id: Mapped[Optional[str]] = mapped_column(String)
    model_sha256: Mapped[Optional[str]] = mapped_column(String)
    model_conf_threshold: Mapped[Optional[float]] = mapped_column(Float)

    # ── Statut d'identification et révision humaine ─────────────────────
    # unreviewed  : proposé (modèle) ou saisi sans taxon, jamais relu
    # identified  : relu par un humain, taxon retenu
    # ambiguous   : relu, deux déterminations possibles (réservé)
    # unidentifiable : relu, le cliché ne permet aucune détermination (« tout NA »)
    identification_status: Mapped[Optional[str]] = mapped_column(String)
    # Décision explicite par rang. ``None`` signifie « donnée historique ou
    # rang pas encore relu » ; ``True`` signifie que l'observateur a choisi
    # NA pour ce rang. Le taxon le plus fin ne suffit pas à reconstruire ces
    # trois décisions (famille connue, genre NA, espèce NA par exemple).
    family_is_na: Mapped[Optional[bool]] = mapped_column(Boolean)
    genus_is_na: Mapped[Optional[bool]] = mapped_column(Boolean)
    species_is_na: Mapped[Optional[bool]] = mapped_column(Boolean)
    reviewed_by: Mapped[Optional[str]] = mapped_column(String)
    reviewed_at: Mapped[Optional[datetime]] = mapped_column(DateTime)
    updated_at: Mapped[Optional[datetime]] = mapped_column(DateTime)

    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)


# Nœud « poisson générique » de la taxonomie : porter ce taxon ne veut pas dire
# « identifié » - c'est le repli quand rien n'a pu être déterminé.
GENERIC_TAXON_ID = "taxon-fish-generic"

# Valeurs admises pour `SpatialAnnotation.identification_status`.
STATUS_UNREVIEWED = "unreviewed"
STATUS_IDENTIFIED = "identified"
STATUS_AMBIGUOUS = "ambiguous"
STATUS_UNIDENTIFIABLE = "unidentifiable"

IDENTIFICATION_STATUSES = (
    STATUS_UNREVIEWED,
    STATUS_IDENTIFIED,
    STATUS_AMBIGUOUS,
    STATUS_UNIDENTIFIABLE,
)


class TemporalEvent(Base):
    __tablename__ = "temporal_events"

    id: Mapped[str] = mapped_column(String, primary_key=True)
    track_id: Mapped[str] = mapped_column(String, ForeignKey("tracks.id"), nullable=False)
    event_type: Mapped[str] = mapped_column(String, default="grazing")
    frame_start: Mapped[int] = mapped_column(Integer, nullable=False)
    frame_end: Mapped[int] = mapped_column(Integer, nullable=False)
    # Voir SpatialAnnotation.frame_ref - même convention.
    frame_ref: Mapped[Optional[str]] = mapped_column(String)
    confidence: Mapped[Optional[float]] = mapped_column(Float)
    source: Mapped[str] = mapped_column(String, default="manual")
    author: Mapped[Optional[str]] = mapped_column(String)
    metadata_json: Mapped[Optional[str]] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)


class EventType(Base):
    """Catalogue des comportements annotables (broutage, fuite, ponte…).

    `TemporalEvent.event_type` référence `key`. Le lien reste une chaîne pour
    ne pas casser les événements déjà enregistrés avec un type retiré ensuite.
    """

    __tablename__ = "event_types"

    id: Mapped[str] = mapped_column(String, primary_key=True)
    key: Mapped[str] = mapped_column(String, nullable=False, unique=True)
    label: Mapped[str] = mapped_column(String, nullable=False)
    color: Mapped[str] = mapped_column(String, default="#f59e0b")
    symbol: Mapped[str] = mapped_column(String, default="●")
    shortcut: Mapped[Optional[str]] = mapped_column(String)
    scope: Mapped[str] = mapped_column(String, default="interval")
    description: Mapped[Optional[str]] = mapped_column(Text)
    is_builtin: Mapped[bool] = mapped_column(Boolean, default=False)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
    sort_order: Mapped[int] = mapped_column(Integer, default=0)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)


class SpatialBehaviorFlag(Base):
    """Comportement ponctuel porté par une observation/bbox enregistrée."""

    __tablename__ = "spatial_behavior_flags"

    spatial_annotation_id: Mapped[str] = mapped_column(
        String,
        ForeignKey("spatial_annotations.id", ondelete="CASCADE"),
        primary_key=True,
    )
    event_type: Mapped[str] = mapped_column(
        String,
        ForeignKey("event_types.key", ondelete="CASCADE"),
        primary_key=True,
    )
    author: Mapped[Optional[str]] = mapped_column(String)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)


class CvatLabelMap(Base):
    __tablename__ = "cvat_label_map"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    cvat_label: Mapped[str] = mapped_column(String, nullable=False)
    taxon_node_id: Mapped[str] = mapped_column(String, ForeignKey("taxon_nodes.id"), nullable=False)
    project_id: Mapped[Optional[str]] = mapped_column(String, ForeignKey("projects.id"))

    __table_args__ = (UniqueConstraint("cvat_label", "project_id"),)


class TaxonReferenceEmbedding(Base):
    __tablename__ = "taxon_reference_embeddings"

    id: Mapped[str] = mapped_column(String, primary_key=True)
    taxon_node_id: Mapped[str] = mapped_column(String, ForeignKey("taxon_nodes.id"), nullable=False)
    spatial_annotation_id: Mapped[Optional[str]] = mapped_column(
        String, ForeignKey("spatial_annotations.id")
    )
    media_id: Mapped[str] = mapped_column(String, ForeignKey("media_assets.id"), nullable=False)
    frame_index: Mapped[int] = mapped_column(Integer, default=0)
    embedding: Mapped[bytes] = mapped_column(LargeBinary, nullable=False)
    source: Mapped[str] = mapped_column(String, default="validated")
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)

    __table_args__ = (
        Index(
            "ux_taxon_ref_embeddings_annotation",
            "spatial_annotation_id",
            unique=True,
            sqlite_where=text("spatial_annotation_id IS NOT NULL"),
        ),
    )


class FrameAbundance(Base):
    __tablename__ = "frame_abundance"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    media_id: Mapped[str] = mapped_column(String, ForeignKey("media_assets.id"), nullable=False)
    frame_index: Mapped[int] = mapped_column(Integer, nullable=False)
    # Voir SpatialAnnotation.frame_ref - même convention.
    frame_ref: Mapped[Optional[str]] = mapped_column(String)
    ai_count: Mapped[int] = mapped_column(Integer, default=0)
    manual_count: Mapped[Optional[int]] = mapped_column(Integer)
    validated: Mapped[bool] = mapped_column(Boolean, default=False)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime, default=datetime.utcnow, onupdate=datetime.utcnow
    )

    __table_args__ = (UniqueConstraint("media_id", "frame_index"),)


class ExportRun(Base):
    """Trace d'un export produit - de quoi le refaire à l'identique.

    Les colonnes ajoutées en phase 1 restent nullables : les exporteurs
    remplissent ce qu'ils savent déjà (espace image, calibration, effectifs),
    le reste (manifeste, split explicite, snapshot de base) est renseigné par
    le noyau d'export de la phase 2.
    """

    __tablename__ = "export_runs"

    id: Mapped[str] = mapped_column(String, primary_key=True)
    format: Mapped[str] = mapped_column(String, nullable=False)
    taxonomy_rank: Mapped[str] = mapped_column(String, nullable=False)
    filter_json: Mapped[Optional[str]] = mapped_column(Text)
    output_path: Mapped[str] = mapped_column(String, nullable=False)
    annotation_count: Mapped[int] = mapped_column(Integer, default=0)

    dataset_name: Mapped[Optional[str]] = mapped_column(String)
    dataset_version: Mapped[Optional[str]] = mapped_column(String)
    git_commit: Mapped[Optional[str]] = mapped_column(String)
    db_snapshot_sha256: Mapped[Optional[str]] = mapped_column(String)
    manifest_sha256: Mapped[Optional[str]] = mapped_column(String)
    split_strategy: Mapped[Optional[str]] = mapped_column(String)
    split_seed: Mapped[Optional[int]] = mapped_column(Integer)
    image_space: Mapped[Optional[str]] = mapped_column(String)
    calibration_profile: Mapped[Optional[str]] = mapped_column(String)
    calibration_sha256: Mapped[Optional[str]] = mapped_column(String)
    image_count: Mapped[Optional[int]] = mapped_column(Integer)
    total_bytes: Mapped[Optional[int]] = mapped_column(Integer)
    status: Mapped[Optional[str]] = mapped_column(String)

    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)


# Formats d'export acceptés par le CHECK de `export_runs`. Liste volontairement
# extensible : ajouter une valeur ici suffit, la migration reconstruit la table.
EXPORT_FORMATS = (
    "yolo",
    "coco",
    # Suivi (phase 3) : COCO-VID est le pivot, MOTChallenge en est le dérivé.
    "coco_vid",
    "mot",
    # Comportement (phase 3) : CSV type AVA + table de labels, et la vue
    # « intervalles » pour la revue humaine et les métriques écologiques.
    "ava",
    "events_jsonl",
    "csv_timeline",
    "csv_grazing",
    "csv_abundance",
    "crops",
    "json_debug",
)

# Statuts d'un export. La ligne naît `running` (le noyau l'écrit avant de
# travailler, pour qu'un export interrompu laisse une trace), puis passe à
# `completed` ou `failed`.
EXPORT_STATUS_RUNNING = "running"
EXPORT_STATUS_COMPLETED = "completed"
EXPORT_STATUS_FAILED = "failed"
