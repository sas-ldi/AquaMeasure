"""Un media n'appartient qu'a **une seule** session (decision superviseur).

Sans cette regle, le meme fichier pouvait etre annote sous deux sessions aux
offsets de synchro differents : les deux univers d'annotations designaient alors
des images differentes du meme film, sans qu'aucun controle ne le detecte.
"""

from __future__ import annotations

import sqlite3
import unittest
import uuid

from annotations.helpers import TempDbCase, dispose_engine

from src.annodb import sessions as sessions_mod
from src.annodb.connection import init_db, session_scope
from src.annodb.models import MediaAsset, Project


class MediaSessionUnicityTest(TempDbCase):
    def setUp(self):
        super().setUp()
        init_db(self.db_path, seed=False)
        self.left_id = str(uuid.uuid4())
        self.right_id = str(uuid.uuid4())
        with session_scope(self.db_path) as db:
            proj = Project(id=str(uuid.uuid4()), name="test")
            db.add(proj)
            db.flush()
            db.add_all([
                MediaAsset(id=self.left_id, project_id=proj.id, media_type="video",
                           rel_path="gauche.mp4"),
                MediaAsset(id=self.right_id, project_id=proj.id, media_type="video",
                           rel_path="droite.mp4"),
            ])
            self.first_id = sessions_mod.create_session(
                db, name="Première sortie", site="Récif Nord",
                session_date="2026-08-20",
            ).id
            self.second_id = sessions_mod.create_session(
                db, name="Autre sortie", site="Passe Sud", session_date="2026-08-21",
            ).id
            sessions_mod.attach_media_pair(
                db, self.first_id, left_media_id=self.left_id,
                right_media_id=self.right_id, frame_offset=2440,
            )

    def test_seconde_attache_refusee_avec_message_clair(self):
        with session_scope(self.db_path) as db:
            with self.assertRaises(sessions_mod.MediaAlreadyAttachedError) as ctx:
                sessions_mod.attach_media_pair(
                    db, self.second_id, left_media_id=self.left_id,
                )
        message = str(ctx.exception)
        self.assertIn("gauche.mp4", message)
        self.assertIn("Première sortie", message)
        self.assertIn("une seule session", message)

    def test_media_droit_deja_pris_refuse_aussi(self):
        with session_scope(self.db_path) as db:
            with self.assertRaises(sessions_mod.MediaAlreadyAttachedError):
                sessions_mod.attach_media_pair(
                    db, self.second_id, left_media_id=None,
                    right_media_id=self.right_id,
                )

    def test_refus_avant_toute_ecriture(self):
        """La session visee ne doit pas etre modifiee par un refus."""
        with session_scope(self.db_path) as db:
            try:
                sessions_mod.attach_media_pair(
                    db, self.second_id, left_media_id=self.left_id,
                    frame_offset=99,
                )
            except sessions_mod.MediaAlreadyAttachedError:
                pass
        with session_scope(self.db_path) as db:
            from src.annodb.models import CaptureSession

            row = db.get(CaptureSession, self.second_id)
            self.assertIsNone(row.left_media_id)
            self.assertIsNone(row.frame_offset)
            self.assertEqual(row.status, sessions_mod.STATUS_PLANNED)

    def test_reattache_sur_la_meme_session_autorisee(self):
        with session_scope(self.db_path) as db:
            row = sessions_mod.attach_media_pair(
                db, self.first_id, left_media_id=self.left_id,
                right_media_id=self.right_id, frame_offset=2500,
            )
            self.assertEqual(row.frame_offset, 2500)

    def test_meme_video_a_gauche_et_a_droite_refusee(self):
        with session_scope(self.db_path) as db:
            with self.assertRaises(sessions_mod.MediaAlreadyAttachedError):
                sessions_mod.attach_media_pair(
                    db, self.second_id, left_media_id=self.right_id,
                    right_media_id=self.right_id,
                )

    def test_update_session_ne_contourne_pas_la_garde(self):
        with session_scope(self.db_path) as db:
            with self.assertRaises(sessions_mod.MediaAlreadyAttachedError):
                sessions_mod.update_session(
                    db, self.second_id, left_media_id=self.left_id,
                )

    def test_index_unique_pose_en_base(self):
        """La regle est aussi dans le schema, pas seulement dans le code."""
        conn = sqlite3.connect(self.db_path)
        indexes = {
            r[0] for r in conn.execute(
                "SELECT name FROM sqlite_master WHERE type='index' "
                "AND tbl_name='sessions'"
            )
        }
        self.assertIn("ux_sessions_left_media", indexes)
        self.assertIn("ux_sessions_right_media", indexes)
        with self.assertRaises(sqlite3.IntegrityError):
            conn.execute(
                "UPDATE sessions SET left_media_id = ? WHERE id = ?",
                (self.left_id, self.second_id),
            )
        conn.close()

    def test_plusieurs_sessions_planifiees_sans_video(self):
        """Les index partiels ne doivent pas interdire les sessions a venir."""
        with session_scope(self.db_path) as db:
            for i in range(3):
                sessions_mod.create_session(
                    db, name=f"À venir {i}", site="S", session_date="2026-09-01",
                )
        with session_scope(self.db_path) as db:
            planned = [
                r for r in sessions_mod.list_sessions(db)
                if r["status"] == sessions_mod.STATUS_PLANNED
            ]
        self.assertEqual(len(planned), 4)


class UnicityMigrationTest(TempDbCase):
    """Une base contenant deja un doublon ne doit pas empecher le demarrage."""

    def test_doublon_existant_ne_bloque_pas_la_migration(self):
        from src.annodb import connection

        init_db(self.db_path, seed=False)
        conn = sqlite3.connect(self.db_path)
        conn.executescript(
            """
            INSERT INTO projects (id, name) VALUES ('p1', 'test');
            INSERT INTO media_assets (id, project_id, media_type, rel_path)
                VALUES ('m1', 'p1', 'video', 'clip.mp4');
            """
        )
        # Les index d'unicite ont deja ete poses par init_db : on les retire
        # pour reproduire une base anterieure a la phase 1.
        conn.execute("DROP INDEX IF EXISTS ux_sessions_left_media")
        conn.execute("DROP INDEX IF EXISTS ux_sessions_right_media")
        for sid in ("s1", "s2"):
            conn.execute(
                "INSERT INTO sessions (id, name, site, session_date, status, "
                "left_media_id) VALUES (?, ?, 'S', '2026-08-20', 'active', 'm1')",
                (sid, sid),
            )
        conn.commit()
        conn.close()

        dispose_engine()
        connection.ensure_schema(self.db_path)

        conn = sqlite3.connect(self.db_path)
        indexes = {
            r[0] for r in conn.execute(
                "SELECT name FROM sqlite_master WHERE type='index' "
                "AND tbl_name='sessions'"
            )
        }
        # Index non cree (doublon present) mais les deux sessions survivent.
        self.assertNotIn("ux_sessions_left_media", indexes)
        self.assertEqual(
            conn.execute("SELECT COUNT(*) FROM sessions").fetchone()[0], 2,
        )
        conn.close()


if __name__ == "__main__":
    unittest.main()
