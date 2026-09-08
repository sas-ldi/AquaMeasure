"""Catalogue d'evenements : seed au demarrage, evenements types, CSV.

La table `event_types` existait avec son module (171 lignes) mais n'avait
aucun appelant : elle restait vide et la broute etait une chaine codee en dur.
"""

from __future__ import annotations

import unittest
import uuid
import sqlite3
import threading

from tests.helpers import TempDbCase, dispose_engine

from src.annodb import connection, event_types
from src.annodb.behavior_flags import list_flags, toggle_flag
from src.annodb.connection import init_db, session_scope
from src.annodb.events import add_temporal_event
from src.annodb.models import (
    EventType,
    MediaAsset,
    Project,
    SpatialAnnotation,
    SpatialBehaviorFlag,
    Track,
)
from src.annodb.session_stats import TIMELINE_CSV_FIELDS, iter_session_timeline_rows
from src.annodb.tracks import add_track_sample


class SeedEventTypesTest(TempDbCase):
    def test_bouchee_ponctuelle_par_defaut_et_seed_idempotent(self):
        init_db(self.db_path, seed=False)
        with session_scope(self.db_path) as db:
            bite = event_types.get_by_key(db, "bite")
            self.assertEqual(bite.label, "Bouchée")
            self.assertEqual(bite.scope, "instant")
            self.assertTrue(bite.is_active)
            self.assertEqual(event_types.ensure_builtin_types(db), 0)

    def test_ancienne_bouchee_intervalle_conservee(self):
        init_db(self.db_path, seed=False)
        with session_scope(self.db_path) as db:
            db.delete(event_types.get_by_key(db, "bite"))
            old = event_types.create_event_type(db, label="Bouchée", scope="interval")
            self.assertEqual(event_types.ensure_builtin_types(db), 1)
            self.assertEqual(old.scope, "interval")
            self.assertEqual(event_types.get_by_key(db, "bite").scope, "instant")

    def test_bouchee_ponctuelle_personnalisee_sans_doublon(self):
        init_db(self.db_path, seed=False)
        with session_scope(self.db_path) as db:
            db.delete(event_types.get_by_key(db, "bite"))
            custom = event_types.create_event_type(db, label="Bouchée", scope="point", shortcut="C")
            self.assertEqual(event_types.ensure_builtin_types(db), 0)
            self.assertEqual(custom.shortcut, "C")
            self.assertIsNone(event_types.get_by_key(db, "bite"))

    def test_broute_presente_apres_creation_de_la_base(self):
        init_db(self.db_path, seed=False)
        with session_scope(self.db_path) as db:
            rows = event_types.list_event_types(db, active_only=True)
        keys = {r["key"] for r in rows}
        self.assertIn("grazing", keys)
        grazing = next(r for r in rows if r["key"] == "grazing")
        self.assertEqual(grazing["label"], "Broutage")
        self.assertTrue(grazing["isActive"])
        self.assertTrue(grazing["isBuiltin"])

    def test_seed_idempotent_sur_une_base_existante(self):
        init_db(self.db_path, seed=False)
        dispose_engine()
        connection.ensure_schema(self.db_path)
        with session_scope(self.db_path) as db:
            rows = db.query(EventType).filter(EventType.key == "grazing").all()
        self.assertEqual(len(rows), 1)

    def test_type_integre_ne_peut_pas_etre_desactive(self):
        init_db(self.db_path, seed=False)
        with session_scope(self.db_path) as db:
            grazing = db.query(EventType).filter_by(key="grazing").one()
            self.assertFalse(event_types.delete_event_type(db, grazing.id))
            self.assertTrue(grazing.is_active)

    def test_migration_ancienne_base_ajoute_les_flags_idempotents(self):
        init_db(self.db_path, seed=False)
        dispose_engine()
        with sqlite3.connect(self.db_path) as db:
            db.execute("DROP TABLE spatial_behavior_flags")
        connection._migrated_paths.clear()

        connection.ensure_schema(self.db_path)
        dispose_engine()
        connection._migrated_paths.clear()
        connection.ensure_schema(self.db_path)

        with sqlite3.connect(self.db_path) as db:
            table = db.execute(
                "SELECT name FROM sqlite_master WHERE type='table' "
                "AND name='spatial_behavior_flags'"
            ).fetchone()
            foreign_keys = db.execute(
                "PRAGMA foreign_key_list(spatial_behavior_flags)"
            ).fetchall()
        self.assertEqual(table, ("spatial_behavior_flags",))
        self.assertEqual({row[2] for row in foreign_keys}, {
            "spatial_annotations", "event_types",
        })


class TypedEventTest(TempDbCase):
    def setUp(self):
        super().setUp()
        init_db(self.db_path, seed=False)
        self.media_id = str(uuid.uuid4())
        with session_scope(self.db_path) as db:
            proj = Project(id=str(uuid.uuid4()), name="test")
            db.add(proj)
            db.flush()
            db.add(MediaAsset(
                id=self.media_id, project_id=proj.id, media_type="video",
                rel_path="clip.mp4", width=1920, height=1080, fps=30.0,
                frame_count=900,
            ))
            db.flush()
            self.track_id = str(uuid.uuid4())
            db.add(Track(
                id=self.track_id, media_id=self.media_id, external_track_id=7,
                source="bytetrack",
            ))
            db.flush()
            for frame in (10, 20, 60):
                add_track_sample(
                    db, track_id=self.track_id, frame_index=frame,
                    cx=0.5, cy=0.5, bbox=(10.0, 20.0, 30.0, 40.0),
                )

    def test_type_par_defaut_retrocompatible(self):
        with session_scope(self.db_path) as db:
            ev = add_temporal_event(
                db, track_id=self.track_id, frame_start=5, frame_end=25,
            )
        self.assertEqual(ev.event_type, "grazing")

    def test_evenement_d_un_autre_type(self):
        with session_scope(self.db_path) as db:
            fuite = event_types.create_event_type(db, label="Fuite")
            ev = add_temporal_event(
                db, track_id=self.track_id, frame_start=50, frame_end=70,
                event_type=fuite.key,
            )
        self.assertEqual(ev.event_type, "fuite")

    def test_csv_timeline_declare_le_type(self):
        with session_scope(self.db_path) as db:
            fuite = event_types.create_event_type(db, label="Fuite")
            add_temporal_event(
                db, track_id=self.track_id, frame_start=5, frame_end=25,
            )
            add_temporal_event(
                db, track_id=self.track_id, frame_start=50, frame_end=70,
                event_type=fuite.key,
            )
        with session_scope(self.db_path) as db:
            media = db.get(MediaAsset, self.media_id)
            rows = {r["frame_index"]: r for r in iter_session_timeline_rows(db, media)}

        self.assertIn("event_type", TIMELINE_CSV_FIELDS)
        self.assertEqual(rows[10]["event_type"], "grazing")
        self.assertEqual(rows[10]["is_grazing"], 1)
        # Un autre comportement est nomme, mais ne compte pas comme broute.
        self.assertEqual(rows[60]["event_type"], "fuite")
        self.assertEqual(rows[60]["is_grazing"], 0)

    def test_flag_ponctuel_sans_piste_ni_longueur_est_unique_et_reversible(self):
        annotation_id = str(uuid.uuid4())
        with session_scope(self.db_path) as db:
            fuite = event_types.create_event_type(
                db, label="Fuite éclair", scope="instant"
            )
            db.add(SpatialAnnotation(
                id=annotation_id,
                media_id=self.media_id,
                frame_index=12,
                geom_type="bbox",
                geometry_json='{"x_min": 1, "y_min": 2, "x_max": 3, "y_max": 4}',
                source="manual",
                track_id=None,
                measurement_mm=None,
            ))
            db.flush()
            self.assertTrue(toggle_flag(
                db, annotation_id=annotation_id, event_type=fuite.key
            ))
            flags = list_flags(db, [annotation_id])[annotation_id]
            self.assertEqual(len(flags), 1)
            self.assertEqual(flags[0]["symbol"], "●")
            self.assertEqual(flags[0]["color"], "#f59e0b")
            self.assertFalse(toggle_flag(
                db, annotation_id=annotation_id, event_type=fuite.key
            ))
            self.assertEqual(list_flags(db, [annotation_id])[annotation_id], [])

    def test_deux_connexions_toggle_serialisees_par_verrou_ecriture(self):
        annotation_id = str(uuid.uuid4())
        with session_scope(self.db_path) as db:
            behavior = event_types.create_event_type(
                db, label="Virage concurrent", scope="instant"
            )
            behavior_key = behavior.key
            db.add(SpatialAnnotation(
                id=annotation_id,
                media_id=self.media_id,
                frame_index=13,
                geom_type="bbox",
                geometry_json='{"x_min": 1, "y_min": 2, "x_max": 3, "y_max": 4}',
                source="manual",
            ))

        barrier = threading.Barrier(2)
        results: list[bool] = []
        errors: list[Exception] = []

        def toggle_from_connection():
            try:
                with session_scope(self.db_path) as db:
                    barrier.wait(timeout=2)
                    results.append(toggle_flag(
                        db,
                        annotation_id=annotation_id,
                        event_type=behavior_key,
                    ))
            except Exception as exc:
                errors.append(exc)

        threads = [
            threading.Thread(target=toggle_from_connection) for _ in range(2)
        ]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join(timeout=5)

        self.assertTrue(all(not thread.is_alive() for thread in threads))
        self.assertEqual(errors, [])
        self.assertEqual(sorted(results), [False, True])
        with session_scope(self.db_path) as db:
            self.assertEqual(list_flags(db, [annotation_id])[annotation_id], [])

    def test_type_ponctuel_utilise_est_desactive_et_cascade_a_la_suppression(self):
        annotation_id = str(uuid.uuid4())
        with session_scope(self.db_path) as db:
            posture = event_types.create_event_type(
                db, label="Posture verticale", scope="instant"
            )
            db.add(SpatialAnnotation(
                id=annotation_id,
                media_id=self.media_id,
                frame_index=15,
                geom_type="bbox",
                geometry_json='{"x_min": 1, "y_min": 2, "x_max": 3, "y_max": 4}',
                source="manual",
            ))
            db.flush()
            toggle_flag(db, annotation_id=annotation_id, event_type=posture.key)
            self.assertFalse(event_types.delete_event_type(db, posture.id))
            self.assertFalse(posture.is_active)
            self.assertEqual(
                list_flags(db, [annotation_id])[annotation_id][0]["key"], posture.key
            )
            db.delete(db.get(SpatialAnnotation, annotation_id))
            db.flush()
            self.assertIsNone(db.get(SpatialBehaviorFlag, (annotation_id, posture.key)))

    def test_scopes_ne_sont_pas_interchangeables(self):
        annotation_id = str(uuid.uuid4())
        with session_scope(self.db_path) as db:
            instant = event_types.create_event_type(db, label="Flash", scope="instant")
            db.add(SpatialAnnotation(
                id=annotation_id,
                media_id=self.media_id,
                frame_index=20,
                geom_type="bbox",
                geometry_json='{"x_min": 1, "y_min": 2, "x_max": 3, "y_max": 4}',
                source="manual",
            ))
            db.flush()
            with self.assertRaisesRegex(ValueError, "ponctuel"):
                toggle_flag(db, annotation_id=annotation_id, event_type="grazing")
            with self.assertRaisesRegex(ValueError, "type"):
                event_types.create_event_type(db, label="Invalide", scope="manual")

    def test_pictogramme_personnalise_est_conserve(self):
        with session_scope(self.db_path) as db:
            event = event_types.create_event_type(
                db, label="Ponte", scope="interval", symbol="★",
            )
            as_dict = event_types.as_dict(event)
        self.assertEqual(as_dict["symbol"], "★")


if __name__ == "__main__":
    unittest.main()
