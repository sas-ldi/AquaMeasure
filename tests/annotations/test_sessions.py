"""Sessions de terrain : plusieurs prises et offsets figés par paire.

Une session représente une sortie ou une journée. Elle est créable avant la
sortie puis reçoit autant de paires gauche/droite que nécessaire.
"""

from __future__ import annotations

import sqlite3
import unittest
import uuid

from annotations.helpers import TempDbCase, dispose_engine

from src.annodb import sessions as sessions_mod
from src.annodb.connection import init_db, session_scope
from src.annodb.models import CaptureSession, MediaAsset, Project, SpatialAnnotation


class SessionCreationTest(TempDbCase):
    def setUp(self):
        super().setUp()
        init_db(self.db_path, seed=False)

    def test_session_planifiee_sans_video(self):
        with session_scope(self.db_path) as db:
            row = sessions_mod.create_session(
                db, name="Sortie test", site="Récif Nord", session_date="2026-08-20",
            )
            self.assertEqual(row.status, sessions_mod.STATUS_PLANNED)
            self.assertIsNone(row.left_media_id)
            self.assertIsNone(row.right_media_id)
            self.assertIsNone(row.frame_offset)

    def test_site_et_date_obligatoires(self):
        with session_scope(self.db_path) as db:
            with self.assertRaises(ValueError):
                sessions_mod.create_session(
                    db, name="X", site="  ", session_date="2026-08-20",
                )
            with self.assertRaises(ValueError):
                sessions_mod.create_session(
                    db, name="X", site="Récif Nord", session_date="",
                )

    def test_nom_par_defaut_depuis_site_et_date(self):
        with session_scope(self.db_path) as db:
            row = sessions_mod.create_session(
                db, name="", site="Passe Sud", session_date="2026-08-20",
            )
        self.assertEqual(row.name, "Passe Sud - 2026-08-20")

    def test_statut_inconnu_refuse(self):
        with session_scope(self.db_path) as db:
            with self.assertRaises(ValueError):
                sessions_mod.create_session(
                    db, name="X", site="S", session_date="2026-08-20",
                    status="en_cours",
                )

    def test_liste_groupee_par_statut(self):
        with session_scope(self.db_path) as db:
            sessions_mod.create_session(
                db, name="B terminee", site="S", session_date="2026-08-01",
                status=sessions_mod.STATUS_DONE,
            )
            sessions_mod.create_session(
                db, name="A a venir", site="S", session_date="2026-09-01",
            )
        with session_scope(self.db_path) as db:
            rows = sessions_mod.list_sessions(db)
        self.assertEqual([r["status"] for r in rows], ["planned", "done"])
        self.assertEqual(rows[0]["status_label"], "À venir")
        self.assertEqual(rows[1]["status_label"], "Terminée")


class AttachPairTest(TempDbCase):
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
                MediaAsset(
                    id=self.left_id, project_id=proj.id, media_type="video",
                    rel_path="gauche.mp4",
                ),
                MediaAsset(
                    id=self.right_id, project_id=proj.id, media_type="video",
                    rel_path="droite.mp4",
                ),
            ])
            self.session_id = sessions_mod.create_session(
                db, name="Sortie", site="Récif Nord", session_date="2026-08-20",
            ).id

    def test_activation_attache_la_paire_et_fige_l_offset(self):
        with session_scope(self.db_path) as db:
            row = sessions_mod.attach_media_pair(
                db, self.session_id,
                left_media_id=self.left_id,
                right_media_id=self.right_id,
                frame_offset=2440,
            )
            self.assertIsNotNone(row)
            self.assertEqual(row.status, sessions_mod.STATUS_ACTIVE)
            self.assertEqual(row.left_media_id, self.left_id)
            self.assertEqual(row.right_media_id, self.right_id)
            self.assertEqual(row.frame_offset, 2440)

    def test_les_deux_medias_retrouvent_leur_session(self):
        with session_scope(self.db_path) as db:
            sessions_mod.attach_media_pair(
                db, self.session_id,
                left_media_id=self.left_id,
                right_media_id=self.right_id,
                frame_offset=2440,
            )
        with session_scope(self.db_path) as db:
            for media_id in (self.left_id, self.right_id):
                found = sessions_mod.find_session_for_media(db, media_id)
                self.assertIsNotNone(found, media_id)
                self.assertEqual(found.id, self.session_id)
                self.assertEqual(
                    sessions_mod.frame_offset_for_media(db, media_id), 2440,
                )

    def test_offset_absent_quand_la_session_ne_l_a_pas_fige(self):
        with session_scope(self.db_path) as db:
            sessions_mod.attach_media_pair(
                db, self.session_id, left_media_id=self.left_id,
            )
        with session_scope(self.db_path) as db:
            self.assertIsNone(
                sessions_mod.frame_offset_for_media(db, self.left_id)
            )

    def test_suppression_refusee_quand_des_videos_sont_attachees(self):
        with session_scope(self.db_path) as db:
            sessions_mod.attach_media_pair(
                db, self.session_id, left_media_id=self.left_id, frame_offset=0,
            )
        with session_scope(self.db_path) as db:
            with self.assertRaises(ValueError):
                sessions_mod.delete_session(db, self.session_id)
        with session_scope(self.db_path) as db:
            self.assertEqual(len(sessions_mod.list_sessions(db)), 1)

    def test_suppression_d_une_session_planifiee(self):
        with session_scope(self.db_path) as db:
            self.assertTrue(sessions_mod.delete_session(db, self.session_id))
        with session_scope(self.db_path) as db:
            self.assertEqual(sessions_mod.list_sessions(db), [])

    def test_compteurs_de_la_session(self):
        with session_scope(self.db_path) as db:
            sessions_mod.attach_media_pair(
                db, self.session_id,
                left_media_id=self.left_id, right_media_id=self.right_id,
                frame_offset=0,
            )
            db.add_all([
                SpatialAnnotation(
                    id="a1", media_id=self.left_id, frame_index=10,
                    frame_ref="absolute", geom_type="bbox", geometry_json="{}",
                    measurement_mm=123.4,
                ),
                SpatialAnnotation(
                    id="a2", media_id=self.right_id, frame_index=10,
                    frame_ref="absolute", geom_type="bbox", geometry_json="{}",
                ),
            ])
        with session_scope(self.db_path) as db:
            rows = sessions_mod.list_sessions(db)
        self.assertEqual(rows[0]["observation_count"], 2)
        self.assertEqual(rows[0]["measurement_count"], 1)
        self.assertTrue(rows[0]["has_pair"])

    def test_plusieurs_paires_s_agregent_sans_perdre_leur_offset(self):
        second_left = str(uuid.uuid4())
        second_right = str(uuid.uuid4())
        with session_scope(self.db_path) as db:
            project_id = db.get(MediaAsset, self.left_id).project_id
            db.add_all([
                MediaAsset(
                    id=second_left, project_id=project_id,
                    media_type="video", rel_path="gauche_2.mp4",
                ),
                MediaAsset(
                    id=second_right, project_id=project_id,
                    media_type="video", rel_path="droite_2.mp4",
                ),
            ])
            sessions_mod.attach_media_pair(
                db, self.session_id, left_media_id=self.left_id,
                right_media_id=self.right_id, frame_offset=12,
            )
            sessions_mod.attach_media_pair(
                db, self.session_id, left_media_id=second_left,
                right_media_id=second_right, frame_offset=-7,
            )
            db.add_all([
                SpatialAnnotation(
                    id="pair-1-ann", media_id=self.left_id, frame_index=5,
                    frame_ref="absolute", geom_type="bbox", geometry_json="{}",
                ),
                SpatialAnnotation(
                    id="pair-2-ann", media_id=second_left, frame_index=5,
                    frame_ref="absolute", geom_type="bbox", geometry_json="{}",
                    measurement_mm=98.0,
                ),
            ])

        with session_scope(self.db_path) as db:
            row = sessions_mod.session_as_dict(
                db, db.get(CaptureSession, self.session_id), with_stats=True,
            )
            self.assertEqual(row["pair_count"], 2)
            self.assertEqual(row["media_count"], 4)
            self.assertEqual(row["observation_count"], 2)
            self.assertEqual(row["measurement_count"], 1)
            self.assertEqual(row["max_n"], 0)  # Les fiches ne remplacent pas un comptage validé.
            self.assertEqual(
                sessions_mod.session_media_ids(db, self.session_id),
                [self.left_id, self.right_id, second_left, second_right],
            )
            self.assertEqual(
                sessions_mod.frame_offset_for_media(db, self.left_id), 12,
            )
            self.assertEqual(
                sessions_mod.frame_offset_for_media(db, second_right), -7,
            )

    def test_reattacher_la_meme_paire_met_a_jour_sans_la_dupliquer(self):
        with session_scope(self.db_path) as db:
            sessions_mod.attach_media_pair(
                db, self.session_id, left_media_id=self.left_id,
                right_media_id=self.right_id, frame_offset=3,
            )
            sessions_mod.attach_media_pair(
                db, self.session_id, left_media_id=self.left_id,
                right_media_id=self.right_id, frame_offset=9,
            )
        with session_scope(self.db_path) as db:
            self.assertEqual(len(sessions_mod.media_pairs(db, self.session_id)), 1)
            self.assertEqual(
                sessions_mod.frame_offset_for_media(db, self.right_id), 9,
            )


class SessionsMigrationTest(TempDbCase):
    """La table `sessions` doit apparaitre sur une base a l'ancien schema."""

    def test_table_creee_sans_perte_de_lignes(self):
        conn = sqlite3.connect(self.db_path)
        from src.annodb import connection

        conn.executescript(connection._SCHEMA.read_text(encoding="utf-8"))
        conn.executescript(
            """
            INSERT INTO projects (id, name) VALUES ('p1', 'test');
            INSERT INTO media_assets (id, project_id, media_type, rel_path)
                VALUES ('m1', 'p1', 'video', 'clip.mp4');
            INSERT INTO spatial_annotations
                (id, media_id, frame_index, geom_type, geometry_json)
                VALUES ('a1', 'm1', 1500, 'bbox', '{}');
            """
        )
        conn.commit()
        tables = {
            r[0] for r in conn.execute(
                "SELECT name FROM sqlite_master WHERE type='table'"
            )
        }
        self.assertNotIn("sessions", tables)
        conn.close()

        dispose_engine()
        connection.ensure_schema(self.db_path)

        conn = sqlite3.connect(self.db_path)
        tables = {
            r[0] for r in conn.execute(
                "SELECT name FROM sqlite_master WHERE type='table'"
            )
        }
        self.assertIn("sessions", tables)
        self.assertEqual(
            conn.execute("SELECT COUNT(*) FROM spatial_annotations").fetchone()[0], 1,
        )
        self.assertEqual(
            conn.execute("SELECT COUNT(*) FROM media_assets").fetchone()[0], 1,
        )
        # Statut hors catalogue refuse par la contrainte.
        with self.assertRaises(sqlite3.IntegrityError):
            conn.execute(
                "INSERT INTO sessions (id, name, site, session_date, status) "
                "VALUES ('s1', 'x', 'y', '2026-08-20', 'nimporte')"
            )
        conn.close()

    def test_migration_idempotente(self):
        from src.annodb import connection

        init_db(self.db_path, seed=False)
        with session_scope(self.db_path) as db:
            sessions_mod.create_session(
                db, name="S", site="Site", session_date="2026-08-20",
            )
        dispose_engine()
        connection.ensure_schema(self.db_path)
        with session_scope(self.db_path) as db:
            self.assertEqual(len(sessions_mod.list_sessions(db)), 1)


class SessionModelTest(TempDbCase):
    def test_modele_orm_et_table_partagent_le_nom(self):
        self.assertEqual(CaptureSession.__tablename__, "sessions")


if __name__ == "__main__":
    unittest.main()
