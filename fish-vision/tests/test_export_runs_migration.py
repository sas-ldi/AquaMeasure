"""`export_runs` : CHECK elargi, colonnes de tracabilite, aucune perte de ligne.

SQLite ne sait pas modifier une contrainte : la table est reconstruite
(`_new` / INSERT / DROP / RENAME). Ce test verifie que la reconstruction ne
perd rien et qu'elle est idempotente.
"""

from __future__ import annotations

import sqlite3
import unittest

from tests.helpers import TempDbCase, dispose_engine

from src.annodb.models import EXPORT_FORMATS

_LEGACY_DDL = """
CREATE TABLE export_runs (
    id            TEXT PRIMARY KEY,
    created_at    TEXT NOT NULL DEFAULT (datetime('now')),
    format        TEXT NOT NULL CHECK (
        format IN ('yolo', 'coco', 'csv_timeline', 'csv_grazing')
    ),
    taxonomy_rank TEXT NOT NULL CHECK (
        taxonomy_rank IN ('fish', 'family', 'genus', 'species')
    ),
    filter_json   TEXT,
    output_path   TEXT NOT NULL,
    annotation_count INTEGER NOT NULL DEFAULT 0
);
"""

_ADDED_COLUMNS = (
    "dataset_name", "dataset_version", "git_commit", "db_snapshot_sha256",
    "manifest_sha256", "split_strategy", "split_seed", "image_space",
    "calibration_profile", "calibration_sha256", "image_count", "total_bytes",
    "status",
)


class ExportRunsMigrationTest(TempDbCase):
    def _legacy_db(self) -> None:
        from src.annodb import connection

        conn = sqlite3.connect(self.db_path)
        schema = connection._SCHEMA.read_text(encoding="utf-8")
        conn.executescript(schema)
        conn.execute("DROP TABLE export_runs")
        conn.executescript(_LEGACY_DDL)
        for i, fmt in enumerate(("yolo", "coco", "csv_timeline", "csv_grazing")):
            conn.execute(
                "INSERT INTO export_runs "
                "(id, created_at, format, taxonomy_rank, filter_json, output_path, "
                " annotation_count) VALUES (?, '2026-01-0%d 00:00:00', ?, 'family', "
                " '{\"k\": 1}', ?, ?)" % (i + 1),
                (f"run{i}", fmt, f"/exports/run{i}", 10 * (i + 1)),
            )
        conn.commit()
        conn.close()

    def test_reconstruction_sans_perte(self):
        from src.annodb import connection

        self._legacy_db()
        dispose_engine()
        connection.ensure_schema(self.db_path)

        conn = sqlite3.connect(self.db_path)
        rows = list(conn.execute(
            "SELECT id, created_at, format, taxonomy_rank, filter_json, "
            "output_path, annotation_count FROM export_runs ORDER BY id"
        ))
        self.assertEqual(len(rows), 4)
        self.assertEqual(
            [r[2] for r in rows], ["yolo", "coco", "csv_timeline", "csv_grazing"],
        )
        self.assertEqual([r[6] for r in rows], [10, 20, 30, 40])
        self.assertEqual([r[4] for r in rows], ['{"k": 1}'] * 4)
        self.assertEqual(rows[0][1], "2026-01-01 00:00:00")
        conn.close()

    def test_colonnes_de_tracabilite_ajoutees(self):
        from src.annodb import connection

        self._legacy_db()
        dispose_engine()
        connection.ensure_schema(self.db_path)

        conn = sqlite3.connect(self.db_path)
        cols = {r[1] for r in conn.execute("PRAGMA table_info(export_runs)")}
        for col in _ADDED_COLUMNS:
            self.assertIn(col, cols, col)
        conn.close()

    def test_check_elargi_accepte_les_nouveaux_formats(self):
        from src.annodb import connection

        self._legacy_db()
        dispose_engine()
        connection.ensure_schema(self.db_path)

        conn = sqlite3.connect(self.db_path)
        for fmt in EXPORT_FORMATS:
            conn.execute(
                "INSERT INTO export_runs (id, format, taxonomy_rank, output_path) "
                "VALUES (?, ?, 'fish', '/tmp/x')", (f"new_{fmt}", fmt),
            )
        # Un format inconnu reste refuse : le CHECK n'est pas devenu decoratif.
        with self.assertRaises(sqlite3.IntegrityError):
            conn.execute(
                "INSERT INTO export_runs (id, format, taxonomy_rank, output_path) "
                "VALUES ('bad', 'parquet', 'fish', '/tmp/x')"
            )
        conn.close()

    def test_migration_idempotente(self):
        from src.annodb import connection

        self._legacy_db()
        dispose_engine()
        connection.ensure_schema(self.db_path)
        dispose_engine()
        connection.ensure_schema(self.db_path)

        conn = sqlite3.connect(self.db_path)
        self.assertEqual(
            conn.execute("SELECT COUNT(*) FROM export_runs").fetchone()[0], 4,
        )
        # Aucune table de travail laissee derriere.
        tables = {
            r[0] for r in conn.execute(
                "SELECT name FROM sqlite_master WHERE type='table'"
            )
        }
        self.assertNotIn("export_runs_new", tables)
        conn.close()


# `ExportRunProvenanceTest` vivait ici et testait
# `export_media.export_run_provenance`, une aide devenue morte avec le noyau de
# la phase 2 (aucun appelant : le noyau remplit lui-meme la ligne). La fonction
# est supprimee, ses deux cas sont repris a l'identique — et renforces sur les
# champs que l'aide laissait NULL — dans `test_export_core.py` :
# `ExportRunProvenanceTest` et `ExportRunProvenanceWithoutCalibrationTest`,
# joues sur la ligne `export_runs` reellement ecrite par un export.


if __name__ == "__main__":
    unittest.main()
