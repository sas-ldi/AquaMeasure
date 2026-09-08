-- annotations annotation database schema (SQLite)
-- Source of truth for all annotation projects.

PRAGMA foreign_keys = ON;

CREATE TABLE IF NOT EXISTS taxon_nodes (
    id              TEXT PRIMARY KEY,
    parent_id       TEXT REFERENCES taxon_nodes(id) ON DELETE SET NULL,
    rank            TEXT NOT NULL CHECK (rank IN ('family', 'genus', 'species', 'provisional')),
    scientific_name TEXT NOT NULL,
    common_name     TEXT,
    is_provisional  INTEGER NOT NULL DEFAULT 0,
    created_at      TEXT NOT NULL DEFAULT (datetime('now')),
    updated_at      TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE INDEX IF NOT EXISTS idx_taxon_parent ON taxon_nodes(parent_id);
CREATE INDEX IF NOT EXISTS idx_taxon_rank ON taxon_nodes(rank);

CREATE TABLE IF NOT EXISTS projects (
    id          TEXT PRIMARY KEY,
    name        TEXT NOT NULL UNIQUE,
    description TEXT,
    created_at  TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS media_assets (
    id             TEXT PRIMARY KEY,
    project_id     TEXT NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
    media_type     TEXT NOT NULL CHECK (media_type IN ('image', 'video')),
    rel_path       TEXT NOT NULL,
    sha256         TEXT,
    width          INTEGER,
    height         INTEGER,
    fps            REAL,
    frame_count    INTEGER,
    captured_at    TEXT,
    site           TEXT,
    session_title  TEXT,
    notes          TEXT,
    session_date   TEXT,
    created_at     TEXT NOT NULL DEFAULT (datetime('now')),
    UNIQUE(project_id, rel_path)
);

CREATE INDEX IF NOT EXISTS idx_media_project ON media_assets(project_id);

CREATE TABLE IF NOT EXISTS tracks (
    id                TEXT PRIMARY KEY,
    media_id          TEXT NOT NULL REFERENCES media_assets(id) ON DELETE CASCADE,
    external_track_id INTEGER NOT NULL,
    taxon_node_id     TEXT REFERENCES taxon_nodes(id) ON DELETE SET NULL,
    identification_status TEXT,
    first_frame       INTEGER NOT NULL DEFAULT 0,
    last_frame        INTEGER NOT NULL DEFAULT 0,
    source            TEXT NOT NULL DEFAULT 'manual'
        CHECK (source IN ('cvat', 'bytetrack', 'manual', 'model', 'heuristic')),
    created_at        TEXT NOT NULL DEFAULT (datetime('now')),
    UNIQUE(media_id, external_track_id, source)
);

CREATE INDEX IF NOT EXISTS idx_tracks_media ON tracks(media_id);

CREATE TABLE IF NOT EXISTS track_samples (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    track_id   TEXT NOT NULL REFERENCES tracks(id) ON DELETE CASCADE,
    frame_index INTEGER NOT NULL,
    cx         REAL NOT NULL,
    cy         REAL NOT NULL,
    bbox_json  TEXT NOT NULL,
    position_x_mm REAL,
    position_y_mm REAL,
    position_z_mm REAL,
    match_score   REAL,
    match_method  TEXT,
    UNIQUE(track_id, frame_index)
);

CREATE INDEX IF NOT EXISTS idx_track_samples_track ON track_samples(track_id);

CREATE TABLE IF NOT EXISTS spatial_annotations (
    id              TEXT PRIMARY KEY,
    media_id        TEXT NOT NULL REFERENCES media_assets(id) ON DELETE CASCADE,
    frame_index     INTEGER NOT NULL DEFAULT 0,
    geom_type       TEXT NOT NULL CHECK (geom_type IN ('bbox', 'polygon', 'mask', 'point')),
    geometry_json   TEXT NOT NULL,
    taxon_node_id   TEXT REFERENCES taxon_nodes(id) ON DELETE SET NULL,
    track_id        TEXT REFERENCES tracks(id) ON DELETE SET NULL,
    confidence      REAL,
    is_provisional  INTEGER NOT NULL DEFAULT 0,
    author          TEXT,
    source          TEXT NOT NULL DEFAULT 'manual'
        CHECK (source IN ('cvat', 'manual', 'model', 'heuristic', 'validated')),
    cvat_task_id    INTEGER,
    cvat_shape_id   INTEGER,
    measurement_mm  REAL,
    is_grazing      INTEGER,
    position_x_mm   REAL,
    position_y_mm   REAL,
    position_z_mm   REAL,
    crop_path       TEXT,
    created_at      TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE INDEX IF NOT EXISTS idx_spatial_media ON spatial_annotations(media_id, frame_index);
CREATE INDEX IF NOT EXISTS idx_spatial_taxon ON spatial_annotations(taxon_node_id);

CREATE TABLE IF NOT EXISTS temporal_events (
    id            TEXT PRIMARY KEY,
    track_id      TEXT NOT NULL REFERENCES tracks(id) ON DELETE CASCADE,
    event_type    TEXT NOT NULL DEFAULT 'grazing',
    frame_start   INTEGER NOT NULL,
    frame_end     INTEGER NOT NULL,
    confidence    REAL,
    source        TEXT NOT NULL DEFAULT 'manual'
        CHECK (source IN ('manual', 'heuristic', 'model', 'validated')),
    author        TEXT,
    metadata_json TEXT,
    created_at    TEXT NOT NULL DEFAULT (datetime('now')),
    CHECK (frame_end >= frame_start)
);

CREATE INDEX IF NOT EXISTS idx_events_track ON temporal_events(track_id);

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
);

CREATE TABLE IF NOT EXISTS spatial_behavior_flags (
    spatial_annotation_id TEXT NOT NULL
        REFERENCES spatial_annotations(id) ON DELETE CASCADE,
    event_type TEXT NOT NULL REFERENCES event_types(key) ON DELETE CASCADE,
    author TEXT,
    created_at TEXT NOT NULL DEFAULT (datetime('now')),
    PRIMARY KEY (spatial_annotation_id, event_type)
);

CREATE INDEX IF NOT EXISTS idx_spatial_behavior_flags_type
ON spatial_behavior_flags(event_type);

CREATE TABLE IF NOT EXISTS cvat_label_map (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    cvat_label    TEXT NOT NULL,
    taxon_node_id TEXT NOT NULL REFERENCES taxon_nodes(id) ON DELETE CASCADE,
    project_id    TEXT REFERENCES projects(id) ON DELETE CASCADE,
    UNIQUE(cvat_label, project_id)
);

CREATE TABLE IF NOT EXISTS taxon_reference_embeddings (
    id                    TEXT PRIMARY KEY,
    taxon_node_id         TEXT NOT NULL REFERENCES taxon_nodes(id) ON DELETE CASCADE,
    spatial_annotation_id TEXT REFERENCES spatial_annotations(id) ON DELETE SET NULL,
    media_id              TEXT NOT NULL REFERENCES media_assets(id) ON DELETE CASCADE,
    frame_index           INTEGER NOT NULL DEFAULT 0,
    embedding             BLOB NOT NULL,
    source                TEXT NOT NULL DEFAULT 'validated',
    created_at            TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE INDEX IF NOT EXISTS idx_taxon_ref_embeddings_taxon ON taxon_reference_embeddings(taxon_node_id);
CREATE INDEX IF NOT EXISTS idx_taxon_ref_embeddings_media ON taxon_reference_embeddings(media_id);
CREATE UNIQUE INDEX IF NOT EXISTS ux_taxon_ref_embeddings_annotation
ON taxon_reference_embeddings(spatial_annotation_id)
WHERE spatial_annotation_id IS NOT NULL;

CREATE TABLE IF NOT EXISTS export_runs (
    id            TEXT PRIMARY KEY,
    created_at    TEXT NOT NULL DEFAULT (datetime('now')),
    format        TEXT NOT NULL CHECK (format IN ('yolo', 'coco', 'csv_timeline', 'csv_grazing')),
    taxonomy_rank TEXT NOT NULL CHECK (taxonomy_rank IN ('fish', 'family', 'genus', 'species')),
    filter_json   TEXT,
    output_path   TEXT NOT NULL,
    annotation_count INTEGER NOT NULL DEFAULT 0
);
