"""Migration `frame_ref` : ajout de colonne + marquage de l'existant."""

from __future__ import annotations

import sqlite3
import unittest

from annotations.helpers import TempDbCase, dispose_engine

from src.annodb import connection


class FrameRefMigrationTest(TempDbCase):
    def _legacy_db(self) -> None:
        """Base à l'ancien schéma : colonne frame_ref absente, données dedans."""
        conn = sqlite3.connect(self.db_path)
        conn.executescript(connection._SCHEMA.read_text(encoding="utf-8"))
        conn.executescript(
            """
            INSERT INTO projects (id, name) VALUES ('p1', 'test');
            INSERT INTO media_assets (id, project_id, media_type, rel_path)
                VALUES ('m1', 'p1', 'video', 'clip.mp4');
            INSERT INTO tracks (id, media_id, external_track_id)
                VALUES ('t1', 'm1', 7);
            INSERT INTO spatial_annotations
                (id, media_id, frame_index, geom_type, geometry_json)
                VALUES ('a1', 'm1', 1500, 'bbox', '{"x_min":1,"y_min":2,"x_max":3,"y_max":4}');
            INSERT INTO temporal_events (id, track_id, frame_start, frame_end)
                VALUES ('e1', 't1', 100, 200);
            """
        )
        conn.commit()
        conn.close()

    def test_colonne_ajoutee_et_existant_marque_legacy(self):
        self._legacy_db()

        conn = sqlite3.connect(self.db_path)
        cols = {r[1] for r in conn.execute("PRAGMA table_info(spatial_annotations)")}
        self.assertNotIn("frame_ref", cols)
        conn.close()

        dispose_engine()
        connection.ensure_schema(self.db_path)

        conn = sqlite3.connect(self.db_path)
        for table in ("spatial_annotations", "temporal_events", "frame_abundance"):
            cols = {r[1] for r in conn.execute(f"PRAGMA table_info({table})")}
            self.assertIn("frame_ref", cols, table)
        # Tout ce qui existait a été écrit en index timeline relatif.
        self.assertEqual(
            conn.execute("SELECT frame_ref FROM spatial_annotations").fetchall(),
            [("timeline_legacy",)],
        )
        self.assertEqual(
            conn.execute("SELECT frame_ref FROM temporal_events").fetchall(),
            [("timeline_legacy",)],
        )
        conn.close()

    def test_migration_idempotente_et_sans_effet_sur_les_lignes_recentes(self):
        self._legacy_db()
        dispose_engine()
        connection.ensure_schema(self.db_path)

        conn = sqlite3.connect(self.db_path)
        conn.execute(
            "INSERT INTO spatial_annotations "
            "(id, media_id, frame_index, frame_ref, geom_type, geometry_json) "
            "VALUES ('a2', 'm1', 3940, 'absolute', 'bbox', '{}')"
        )
        conn.commit()
        conn.close()

        # Un second passage ne doit pas repasser les lignes récentes en legacy.
        dispose_engine()
        connection.ensure_schema(self.db_path)

        conn = sqlite3.connect(self.db_path)
        rows = dict(conn.execute("SELECT id, frame_ref FROM spatial_annotations"))
        conn.close()
        self.assertEqual(rows, {"a1": "timeline_legacy", "a2": "absolute"})


if __name__ == "__main__":
    unittest.main()
