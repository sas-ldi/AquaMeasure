"""Database connection and initialization."""

from __future__ import annotations

import os
import sqlite3
from pathlib import Path

from contextlib import contextmanager

from sqlalchemy import create_engine, event
from sqlalchemy.orm import Session, sessionmaker

from .models import GENERIC_TAXON_ID, Base

_PKG_ROOT = Path(__file__).resolve().parents[2]
_DEFAULT_DB = _PKG_ROOT / "data" / "fish_annotations.db"
_SCHEMA = _PKG_ROOT / "db" / "schema.sql"
_SEED = _PKG_ROOT / "db" / "seed_taxonomy.sql"

_engine = None
_SessionLocal = None
_migrated_paths: set[str] = set()


def get_db_path() -> Path:
    """Base d'annotations : `FISH_VISION_DB` > racine configurée > défaut.

    La variable d'environnement reste **souveraine** : c'est elle qui protège
    la vraie base pendant les tests et les copies de travail. Sans elle, la
    racine choisie dans la page Paramètres l'emporte ; sans configuration, on
    retombe sur `annotations/data/fish_annotations.db` - l'historique, au bit
    près.
    """
    env = os.environ.get("FISH_VISION_DB")
    if env:
        return Path(env)
    from .storage_config import configured_db_path

    configured = configured_db_path()
    if configured is not None:
        return configured
    return _DEFAULT_DB


def get_engine(db_path: Path | None = None):
    global _engine, _SessionLocal
    path = db_path or get_db_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    url = f"sqlite:///{path.as_posix()}"
    if _engine is None or str(_engine.url).replace("sqlite:///", "") != path.as_posix():
        if _engine is not None:
            # Changement de base : sans disposer l'ancien moteur, ses
            # connexions restent ouvertes et le fichier précédent reste
            # verrouillé (sous Windows, il devient même indéracinable).
            _engine.dispose()
        _engine = create_engine(
            url, future=True, connect_args={"check_same_thread": False, "timeout": 30})

        @event.listens_for(_engine, "connect")
        def _set_sqlite_pragma(dbapi_conn, _):
            cursor = dbapi_conn.cursor()
            cursor.execute("PRAGMA foreign_keys=ON")
            cursor.execute("PRAGMA journal_mode=WAL")
            cursor.execute("PRAGMA busy_timeout=30000")
            cursor.close()

        _SessionLocal = sessionmaker(bind=_engine, expire_on_commit=False)
    return _engine


def get_session(db_path: Path | None = None) -> Session:
    ensure_schema(db_path)
    get_engine(db_path)
    return _SessionLocal()


@contextmanager
def session_scope(db_path: Path | None = None):
    session = get_session(db_path)
    try:
        yield session
        session.commit()
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()


def _run_sql_file(conn: sqlite3.Connection, path: Path) -> None:
    sql = path.read_text(encoding="utf-8")
    conn.executescript(sql)


def _migrate_schema(conn: sqlite3.Connection) -> None:
    cols = {row[1] for row in conn.execute("PRAGMA table_info(spatial_annotations)")}
    if "measurement_mm" not in cols:
        conn.execute("ALTER TABLE spatial_annotations ADD COLUMN measurement_mm REAL")
    if "is_grazing" not in cols:
        conn.execute("ALTER TABLE spatial_annotations ADD COLUMN is_grazing INTEGER")
    for col in ("position_x_mm", "position_y_mm", "position_z_mm"):
        if col not in cols:
            conn.execute(f"ALTER TABLE spatial_annotations ADD COLUMN {col} REAL")

    media_cols = {row[1] for row in conn.execute("PRAGMA table_info(media_assets)")}
    for col, ddl in (
        ("site", "ALTER TABLE media_assets ADD COLUMN site TEXT"),
        ("session_title", "ALTER TABLE media_assets ADD COLUMN session_title TEXT"),
        ("notes", "ALTER TABLE media_assets ADD COLUMN notes TEXT"),
        ("session_date", "ALTER TABLE media_assets ADD COLUMN session_date TEXT"),
    ):
        if col not in media_cols:
            conn.execute(ddl)

    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS taxon_reference_embeddings (
            id                    TEXT PRIMARY KEY,
            taxon_node_id         TEXT NOT NULL REFERENCES taxon_nodes(id) ON DELETE CASCADE,
            spatial_annotation_id TEXT REFERENCES spatial_annotations(id) ON DELETE SET NULL,
            media_id              TEXT NOT NULL REFERENCES media_assets(id) ON DELETE CASCADE,
            frame_index           INTEGER NOT NULL DEFAULT 0,
            embedding             BLOB NOT NULL,
            source                TEXT NOT NULL DEFAULT 'validated',
            created_at            TEXT NOT NULL DEFAULT (datetime('now'))
        )
        """
    )
    conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_taxon_ref_embeddings_taxon "
        "ON taxon_reference_embeddings(taxon_node_id)"
    )
    conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_taxon_ref_embeddings_media "
        "ON taxon_reference_embeddings(media_id)"
    )
    _migrate_taxon_reference_unicity(conn)

    _migrate_export_runs(conn)

    track_cols = set()
    if conn.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name='track_samples'"
    ).fetchone():
        track_cols = {
            row[1] for row in conn.execute("PRAGMA table_info(track_samples)")
        }
    for col, ddl in (
        ("position_x_mm", "ALTER TABLE track_samples ADD COLUMN position_x_mm REAL"),
        ("position_y_mm", "ALTER TABLE track_samples ADD COLUMN position_y_mm REAL"),
        ("position_z_mm", "ALTER TABLE track_samples ADD COLUMN position_z_mm REAL"),
        ("match_score", "ALTER TABLE track_samples ADD COLUMN match_score REAL"),
        ("match_method", "ALTER TABLE track_samples ADD COLUMN match_method TEXT"),
        (
            "origin",
            "ALTER TABLE track_samples ADD COLUMN origin TEXT NOT NULL DEFAULT 'auto'",
        ),
        ("edited_by", "ALTER TABLE track_samples ADD COLUMN edited_by TEXT"),
    ):
        if col not in track_cols:
            conn.execute(ddl)

    tracks_cols = {
        row[1] for row in conn.execute("PRAGMA table_info(tracks)")
    }
    if tracks_cols and "identification_status" not in tracks_cols:
        conn.execute("ALTER TABLE tracks ADD COLUMN identification_status TEXT")

    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS frame_abundance (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            media_id TEXT NOT NULL REFERENCES media_assets(id) ON DELETE CASCADE,
            frame_index INTEGER NOT NULL,
            ai_count INTEGER NOT NULL DEFAULT 0,
            manual_count INTEGER,
            validated INTEGER NOT NULL DEFAULT 0,
            updated_at TEXT NOT NULL DEFAULT (datetime('now')),
            UNIQUE(media_id, frame_index)
        )
        """
    )
    conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_frame_abundance_media "
        "ON frame_abundance(media_id)"
    )

    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS event_types (
            id          TEXT PRIMARY KEY,
            key         TEXT NOT NULL UNIQUE,
            label       TEXT NOT NULL,
            color       TEXT NOT NULL DEFAULT '#f59e0b',
            symbol      TEXT NOT NULL DEFAULT '●',
            shortcut    TEXT,
            scope       TEXT NOT NULL DEFAULT 'interval',
            description TEXT,
            is_builtin  INTEGER NOT NULL DEFAULT 0,
            is_active   INTEGER NOT NULL DEFAULT 1,
            sort_order  INTEGER NOT NULL DEFAULT 0,
            created_at  TEXT NOT NULL DEFAULT (datetime('now'))
        )
        """
    )
    conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_temporal_events_type "
        "ON temporal_events(event_type)"
    )
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS spatial_behavior_flags (
            spatial_annotation_id TEXT NOT NULL
                REFERENCES spatial_annotations(id) ON DELETE CASCADE,
            event_type TEXT NOT NULL
                REFERENCES event_types(key) ON DELETE CASCADE,
            author TEXT,
            created_at TEXT NOT NULL DEFAULT (datetime('now')),
            PRIMARY KEY (spatial_annotation_id, event_type)
        )
        """
    )
    conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_spatial_behavior_flags_type "
        "ON spatial_behavior_flags(event_type)"
    )

    _migrate_sessions(conn)
    _migrate_frame_ref(conn)
    _migrate_calibrations(conn)
    _migrate_session_media_pairs(conn)
    _migrate_annotators(conn)
    _migrate_provenance(conn)
    _migrate_media_sha_cache(conn)
    _migrate_media_session_unicity(conn)


def _migrate_taxon_reference_unicity(conn: sqlite3.Connection) -> None:
    """Déduplique l'historique puis impose une référence liée par annotation.

    Les embeddings non vides et correctement alignés float32 sont conservés
    en priorité. Le tri par date/id rend le choix stable quand plusieurs lignes
    ont la même qualité. La requête et l'index sont idempotents.
    """
    from .embeddings import canonical_embedding_dimension, normalize_embedding

    embeddings = conn.execute(
        "SELECT rowid, embedding FROM taxon_reference_embeddings"
    ).fetchall()
    canonical_dimension = canonical_embedding_dimension(
        embedding for _rowid, embedding in embeddings
    )
    invalid_rowids = [
        rowid for rowid, embedding in embeddings
        if normalize_embedding(
            embedding, expected_dimension=canonical_dimension,
        ) is None
    ]
    conn.executemany(
        "DELETE FROM taxon_reference_embeddings WHERE rowid = ?",
        ((rowid,) for rowid in invalid_rowids),
    )
    conn.execute(
        """
        DELETE FROM taxon_reference_embeddings
         WHERE rowid IN (
               SELECT rowid
                 FROM (
                       SELECT rowid,
                              ROW_NUMBER() OVER (
                                  PARTITION BY spatial_annotation_id
                                   ORDER BY created_at DESC,
                                       id DESC
                              ) AS duplicate_rank
                         FROM taxon_reference_embeddings
                        WHERE spatial_annotation_id IS NOT NULL
                      ) ranked
                WHERE duplicate_rank > 1
         )
        """
    )
    conn.execute(
        "CREATE UNIQUE INDEX IF NOT EXISTS ux_taxon_ref_embeddings_annotation "
        "ON taxon_reference_embeddings(spatial_annotation_id) "
        "WHERE spatial_annotation_id IS NOT NULL"
    )


def _migrate_export_runs(conn: sqlite3.Connection) -> None:
    """Élargit le CHECK de `format` et ajoute les colonnes de traçabilité.

    SQLite ne sait pas modifier une contrainte : on reconstruit la table
    (`_new` / INSERT / DROP / RENAME), motif déjà utilisé ici. Idempotent :
    on ne reconstruit que si une colonne manque ou si un format du catalogue
    n'est pas encore accepté.
    """
    from .models import EXPORT_FORMATS

    row = conn.execute(
        "SELECT sql FROM sqlite_master WHERE type='table' AND name='export_runs'"
    ).fetchone()
    if not row or not row[0]:
        return
    ddl_sql = row[0]
    cols = {r[1] for r in conn.execute("PRAGMA table_info(export_runs)")}
    added_cols = (
        "dataset_name", "dataset_version", "git_commit", "db_snapshot_sha256",
        "manifest_sha256", "split_strategy", "split_seed", "image_space",
        "calibration_profile", "calibration_sha256", "image_count",
        "total_bytes", "status",
    )
    if all(c in cols for c in added_cols) and all(
        f"'{fmt}'" in ddl_sql for fmt in EXPORT_FORMATS
    ):
        return

    formats = ", ".join(f"'{fmt}'" for fmt in EXPORT_FORMATS)
    # Colonnes recopiées = celles que la table possède **déjà**. La reconstruction
    # ne reprenait que les 7 colonnes d'origine : chaque nouveau format ajouté au
    # catalogue effaçait donc `dataset_name`, `manifest_sha256`, `split_*`… de
    # tous les exports passés. Une migration ne perd pas de données.
    base_cols = (
        "id", "created_at", "format", "taxonomy_rank", "filter_json",
        "output_path", "annotation_count",
    )
    carried = [name for name in (*base_cols, *added_cols) if name in cols]
    columns = ", ".join(carried)
    conn.executescript(
        f"""
        DROP TABLE IF EXISTS export_runs_new;
        CREATE TABLE export_runs_new (
            id TEXT PRIMARY KEY,
            created_at TEXT NOT NULL DEFAULT (datetime('now')),
            format TEXT NOT NULL CHECK (format IN ({formats})),
            taxonomy_rank TEXT NOT NULL CHECK (
                taxonomy_rank IN ('fish', 'family', 'genus', 'species')
            ),
            filter_json TEXT,
            output_path TEXT NOT NULL,
            annotation_count INTEGER NOT NULL DEFAULT 0,
            dataset_name TEXT,
            dataset_version TEXT,
            git_commit TEXT,
            db_snapshot_sha256 TEXT,
            manifest_sha256 TEXT,
            split_strategy TEXT,
            split_seed INTEGER,
            image_space TEXT,
            calibration_profile TEXT,
            calibration_sha256 TEXT,
            image_count INTEGER,
            total_bytes INTEGER,
            status TEXT
        );
        INSERT INTO export_runs_new ({columns})
            SELECT {columns} FROM export_runs;
        DROP TABLE export_runs;
        ALTER TABLE export_runs_new RENAME TO export_runs;
        """
    )


def _migrate_calibrations(conn: sqlite3.Connection) -> None:
    """Table `calibrations` + lien depuis les médias et les sessions.

    Sans elle, aucune trace en base de la calibration qui a servi à tracer les
    boîtes : une recalibration invalidait tout l'historique en silence.
    """
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS calibrations (
            id            TEXT PRIMARY KEY,
            profile_name  TEXT NOT NULL,
            sha256        TEXT NOT NULL,
            alpha         REAL,
            image_width   INTEGER,
            image_height  INTEGER,
            baseline_mm   REAL,
            stereo_rmse   REAL,
            created_at    TEXT NOT NULL DEFAULT (datetime('now'))
        )
        """
    )
    # Une calibration = un sha256 : le dédoublonnage est garanti par le schéma,
    # pas seulement par le code appelant.
    conn.execute(
        "CREATE UNIQUE INDEX IF NOT EXISTS ux_calibrations_sha256 "
        "ON calibrations(sha256)"
    )
    for table in ("media_assets", "sessions"):
        exists = conn.execute(
            "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (table,)
        ).fetchone()
        if not exists:
            continue
        cols = {r[1] for r in conn.execute(f"PRAGMA table_info({table})")}
        if "calibration_id" not in cols:
            # Pas de REFERENCES dans l'ALTER : SQLite exige alors une valeur
            # par défaut NULL et n'appliquerait la contrainte qu'aux nouvelles
            # lignes. Le lien est garanti côté code (calibrations.py).
            conn.execute(f"ALTER TABLE {table} ADD COLUMN calibration_id TEXT")


def _migrate_annotators(conn: sqlite3.Connection) -> None:
    """Table `annotators` : qui a annoté, réellement."""
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS annotators (
            id           TEXT PRIMARY KEY,
            display_name TEXT NOT NULL,
            orcid        TEXT,
            created_at   TEXT NOT NULL DEFAULT (datetime('now'))
        )
        """
    )


def _migrate_media_sha_cache(conn: sqlite3.Connection) -> None:
    """Cache d'empreinte : (taille, mtime) au moment du calcul du sha256.

    Le sha256 de chaque vidéo était recalculé à chaque annotation et à chaque
    enregistrement de session - plusieurs Go relus sur le thread UI.
    """
    cols = {r[1] for r in conn.execute("PRAGMA table_info(media_assets)")}
    if "sha256_size" not in cols:
        conn.execute("ALTER TABLE media_assets ADD COLUMN sha256_size INTEGER")
    if "sha256_mtime" not in cols:
        conn.execute("ALTER TABLE media_assets ADD COLUMN sha256_mtime REAL")


def _migrate_media_session_unicity(conn: sqlite3.Connection) -> None:
    """Un même média n'appartient qu'à **une seule** session (décision superviseur).

    Index partiels : sans le `WHERE ... IS NOT NULL`, toutes les sessions
    planifiées (deux médias nuls) entreraient en collision, SQLite considérant
    ici les NULL comme distincts mais la contrainte n'ayant alors aucun sens.

    Si la base contient déjà un doublon, l'index n'est pas créé : on ne casse
    pas le démarrage d'une base existante. La garde applicative
    (`sessions.assert_media_free`) reste, elle, toujours active.
    """
    exists = conn.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name='sessions'"
    ).fetchone()
    if not exists:
        return
    for column, index in (
        ("left_media_id", "ux_sessions_left_media"),
        ("right_media_id", "ux_sessions_right_media"),
    ):
        duplicates = conn.execute(
            f"SELECT COUNT(*) FROM (SELECT {column} FROM sessions "
            f"WHERE {column} IS NOT NULL GROUP BY {column} HAVING COUNT(*) > 1)"
        ).fetchone()[0]
        if duplicates:
            import logging

            logging.getLogger(__name__).warning(
                "%d média(s) rattachés à plusieurs sessions via %s - index "
                "d'unicité non créé, corrigez les doublons puis relancez.",
                duplicates, column,
            )
            continue
        conn.execute(
            f"CREATE UNIQUE INDEX IF NOT EXISTS {index} ON sessions({column}) "
            f"WHERE {column} IS NOT NULL"
        )


def _migrate_provenance(conn: sqlite3.Connection) -> None:
    """Provenance du modèle + statut d'identification sur `spatial_annotations`."""
    cols = {r[1] for r in conn.execute("PRAGMA table_info(spatial_annotations)")}
    added: set[str] = set()
    for col, ddl in (
        ("model_id", "ALTER TABLE spatial_annotations ADD COLUMN model_id TEXT"),
        ("model_sha256", "ALTER TABLE spatial_annotations ADD COLUMN model_sha256 TEXT"),
        (
            "model_conf_threshold",
            "ALTER TABLE spatial_annotations ADD COLUMN model_conf_threshold REAL",
        ),
        (
            "identification_status",
            "ALTER TABLE spatial_annotations ADD COLUMN identification_status TEXT",
        ),
        ("family_is_na", "ALTER TABLE spatial_annotations ADD COLUMN family_is_na INTEGER"),
        ("genus_is_na", "ALTER TABLE spatial_annotations ADD COLUMN genus_is_na INTEGER"),
        ("species_is_na", "ALTER TABLE spatial_annotations ADD COLUMN species_is_na INTEGER"),
        ("reviewed_by", "ALTER TABLE spatial_annotations ADD COLUMN reviewed_by TEXT"),
        ("reviewed_at", "ALTER TABLE spatial_annotations ADD COLUMN reviewed_at TEXT"),
        ("updated_at", "ALTER TABLE spatial_annotations ADD COLUMN updated_at TEXT"),
        ("crop_path", "ALTER TABLE spatial_annotations ADD COLUMN crop_path TEXT"),
    ):
        if col not in cols:
            conn.execute(ddl)
            added.add(col)
    if "identification_status" in added:
        _backfill_identification_status(conn)
    # L'ancien chemin ``unidentifiable`` ne pouvait être produit que par
    # « tout NA ». Cette seule décision historique est donc reconstructible
    # sans inventer l'état des rangs des autres lignes.
    if added.intersection({"family_is_na", "genus_is_na", "species_is_na"}):
        conn.execute(
            "UPDATE spatial_annotations SET family_is_na = 1, genus_is_na = 1, "
            "species_is_na = 1 WHERE identification_status = 'unidentifiable'"
        )
    if "updated_at" in added:
        conn.execute(
            "UPDATE spatial_annotations SET updated_at = created_at "
            "WHERE updated_at IS NULL"
        )


def _backfill_identification_status(conn: sqlite3.Connection) -> None:
    """Statut des lignes antérieures, déduit de leur `source` et de leur taxon.

    - `validated` : la ligne est passée par la validation ligne à ligne, donc
      relue. **`reviewed_at` reçoit `created_at` faute de mieux** - la date de
      validation n'a jamais été enregistrée jusqu'ici, c'est la seule
      approximation possible, et elle est majorante (la relecture est
      postérieure à la création).
      Rattachée au nœud générique, elle vient du chemin « tout NA » : c'est un
      `unidentifiable`, pas un `identified` (une ligne NA affichée comme
      identifiée serait exactement la fausse vérité terrain qu'on supprime).
    - `model` / `heuristic` : proposition d'un modèle, jamais relue.
    - `manual` / `cvat` : saisie humaine, donc relue **si** elle porte un taxon
      autre que le générique ; sinon la boîte existe mais rien n'a été
      déterminé.

    Le backfill n'a lieu qu'au moment de l'ALTER : le rejouer à chaque
    démarrage écraserait les statuts posés depuis.
    """
    conn.execute(
        """
        UPDATE spatial_annotations
           SET identification_status = CASE
                 WHEN source = 'validated'
                      AND taxon_node_id IS NOT NULL
                      AND taxon_node_id <> ?
                   THEN 'identified'
                 WHEN source = 'validated' THEN 'unidentifiable'
                 WHEN source IN ('manual', 'cvat')
                      AND taxon_node_id IS NOT NULL
                      AND taxon_node_id <> ?
                   THEN 'identified'
                 ELSE 'unreviewed'
               END
         WHERE identification_status IS NULL
        """,
        (GENERIC_TAXON_ID, GENERIC_TAXON_ID),
    )
    conn.execute(
        "UPDATE spatial_annotations SET reviewed_at = created_at "
        "WHERE reviewed_at IS NULL AND identification_status IN "
        "('identified', 'unidentifiable')"
    )


def _migrate_sessions(conn: sqlite3.Connection) -> None:
    """Table `sessions` : une ligne = une sortie/journée de terrain.

    Les deux médias sont nullables : une session peut être préparée avant la
    sortie terrain, donc avant d'avoir la moindre vidéo (statut `planned`).
    """
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS sessions (
            id                  TEXT PRIMARY KEY,
            name                TEXT NOT NULL,
            site                TEXT NOT NULL,
            session_date        TEXT NOT NULL,
            operator            TEXT,
            status              TEXT NOT NULL DEFAULT 'planned' CHECK (
                status IN ('planned', 'active', 'done', 'exported')
            ),
            left_media_id       TEXT REFERENCES media_assets(id) ON DELETE SET NULL,
            right_media_id      TEXT REFERENCES media_assets(id) ON DELETE SET NULL,
            calibration_profile TEXT,
            calibration_sha256  TEXT,
            frame_offset        INTEGER,
            notes               TEXT,
            created_at          TEXT NOT NULL DEFAULT (datetime('now')),
            updated_at          TEXT NOT NULL DEFAULT (datetime('now'))
        )
        """
    )
    for ddl in (
        "CREATE INDEX IF NOT EXISTS idx_sessions_left_media ON sessions(left_media_id)",
        "CREATE INDEX IF NOT EXISTS idx_sessions_right_media ON sessions(right_media_id)",
        "CREATE INDEX IF NOT EXISTS idx_sessions_status ON sessions(status)",
    ):
        conn.execute(ddl)


def _migrate_session_media_pairs(conn: sqlite3.Connection) -> None:
    """Ajoute les prises multiples et reprend chaque ancienne paire.

    La migration est idempotente : la contrainte `(session_id, position)` et
    le `NOT EXISTS` empêchent de dupliquer une paire historique au démarrage.
    """
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS session_media_pairs (
            id                  TEXT PRIMARY KEY,
            session_id          TEXT NOT NULL REFERENCES sessions(id) ON DELETE CASCADE,
            position            INTEGER NOT NULL DEFAULT 0,
            left_media_id       TEXT REFERENCES media_assets(id) ON DELETE SET NULL,
            right_media_id      TEXT REFERENCES media_assets(id) ON DELETE SET NULL,
            frame_offset        INTEGER,
            calibration_profile TEXT,
            calibration_sha256  TEXT,
            calibration_id      TEXT,
            created_at          TEXT NOT NULL DEFAULT (datetime('now')),
            updated_at          TEXT NOT NULL DEFAULT (datetime('now')),
            UNIQUE(session_id, position)
        )
        """
    )
    conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_session_pairs_session "
        "ON session_media_pairs(session_id, position)"
    )
    conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_session_pairs_left "
        "ON session_media_pairs(left_media_id)"
    )
    conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_session_pairs_right "
        "ON session_media_pairs(right_media_id)"
    )
    # hex(randomblob()) fournit ici un identifiant de migration stable une fois
    # écrit. Les nouveaux enregistrements utilisent des UUID côté Python.
    conn.execute(
        """
        INSERT INTO session_media_pairs (
            id, session_id, position, left_media_id, right_media_id,
            frame_offset, calibration_profile, calibration_sha256,
            calibration_id, created_at, updated_at
        )
        SELECT lower(hex(randomblob(16))), s.id, 0,
               s.left_media_id, s.right_media_id, s.frame_offset,
               s.calibration_profile, s.calibration_sha256, s.calibration_id,
               s.created_at, s.updated_at
          FROM sessions s
         WHERE (s.left_media_id IS NOT NULL OR s.right_media_id IS NOT NULL)
           AND NOT EXISTS (
               SELECT 1 FROM session_media_pairs p WHERE p.session_id = s.id
           )
        """
    )


def _migrate_frame_ref(conn: sqlite3.Connection) -> None:
    """Marque le référentiel d'index de frame de chaque ligne.

    Tout ce qui existe au moment de l'ajout de la colonne a été écrit en index
    **timeline relative** par `data_controller` : on le déclare
    `timeline_legacy` plutôt que de le laisser passer pour de l'absolu. Les
    écritures postérieures posent `absolute` explicitement.

    Le backfill n'a lieu qu'au moment de l'ALTER : repasser un UPDATE global à
    chaque démarrage retomberait sur des lignes récentes.
    """
    for table in ("spatial_annotations", "temporal_events", "frame_abundance"):
        exists = conn.execute(
            "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (table,)
        ).fetchone()
        if not exists:
            continue
        cols = {row[1] for row in conn.execute(f"PRAGMA table_info({table})")}
        if "frame_ref" in cols:
            continue
        conn.execute(f"ALTER TABLE {table} ADD COLUMN frame_ref TEXT")
        conn.execute(
            f"UPDATE {table} SET frame_ref = 'timeline_legacy' WHERE frame_ref IS NULL"
        )


def _seed_event_types() -> None:
    """Catalogue de comportements livré avec l'app (broutage en premier).

    Sans ce peuplement, `event_types` restait vide et la broute n'était qu'une
    chaîne codée en dur : impossible d'annoter un autre comportement.
    Volontairement silencieux - une base en lecture seule ne doit pas empêcher
    l'application de démarrer.
    """
    from .event_types import ensure_builtin_types

    if _SessionLocal is None:
        return
    try:
        with _SessionLocal() as session:
            if ensure_builtin_types(session):
                session.commit()
    except Exception:  # noqa: BLE001 - démarrage jamais bloqué par le seed
        import logging

        logging.getLogger(__name__).warning(
            "Types d'événements intégrés non initialisés", exc_info=True
        )


def ensure_schema(db_path: Path | None = None) -> Path:
    """Crée la DB si absente, sinon applique les migrations incrémentales."""
    path = db_path or get_db_path()
    key = path.resolve().as_posix()
    if key in _migrated_paths:
        return path
    path.parent.mkdir(parents=True, exist_ok=True)
    if not path.is_file():
        return init_db(path, seed=True)
    with sqlite3.connect(path) as conn:
        conn.execute("PRAGMA foreign_keys=ON")
        _migrate_schema(conn)
        conn.commit()
    get_engine(path)
    Base.metadata.create_all(get_engine(path))
    _migrated_paths.add(key)
    _seed_event_types()
    return path


def init_db(db_path: Path | None = None, seed: bool = True) -> Path:
    """Create schema and optionally seed taxonomy."""
    path = db_path or get_db_path()
    path.parent.mkdir(parents=True, exist_ok=True)

    with sqlite3.connect(path) as conn:
        conn.execute("PRAGMA foreign_keys=ON")
        _run_sql_file(conn, _SCHEMA)
        _migrate_schema(conn)
        if seed:
            _run_sql_file(conn, _SEED)
        conn.commit()

    engine = get_engine(path)
    Base.metadata.create_all(engine)
    _migrated_paths.add(path.resolve().as_posix())
    _seed_event_types()
    return path
