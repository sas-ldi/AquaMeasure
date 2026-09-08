"""Identite d'annotateur : table, annotateur courant, `author` renseigne.

Avant la phase 1, `author` valait `'operator'` en dur sur 100 % des lignes.
"""

from __future__ import annotations

import os
import unittest
import uuid
from datetime import datetime

from annotations.helpers import TempDbCase

from src.annodb import annotators, spatial
from src.annodb.app_settings import ENV_SETTINGS_PATH
from src.annodb.connection import init_db, session_scope
from src.annodb.models import (
    Annotator, CaptureSession, MediaAsset, Project, TemporalEvent, Track,
)


class AnnotatorCase(TempDbCase):
    """Base + fichier de reglages jetables : jamais ceux de l'utilisateur."""

    def setUp(self):
        super().setUp()
        settings = self.tmp_path / "app_settings.json"
        previous = os.environ.get(ENV_SETTINGS_PATH)
        os.environ[ENV_SETTINGS_PATH] = str(settings)
        self.addCleanup(self._restore_env, ENV_SETTINGS_PATH, previous)
        init_db(self.db_path, seed=False)


class AnnotatorTableTest(AnnotatorCase):
    def test_aucune_identite_au_premier_lancement(self):
        with session_scope(self.db_path) as db:
            self.assertTrue(annotators.needs_identity(db))
            self.assertIsNone(annotators.current_annotator(db))
            self.assertIsNone(annotators.current_author_name(db))

    def test_creation_et_memorisation(self):
        with session_scope(self.db_path) as db:
            row = annotators.create_annotator(
                db, display_name="Pierrick Dupont", orcid="0000-0002-1825-0097",
            )
            annotator_id = row.id
        with session_scope(self.db_path) as db:
            annotators.set_current_annotator(db, annotator_id)
        with session_scope(self.db_path) as db:
            self.assertFalse(annotators.needs_identity(db))
            self.assertEqual(annotators.current_author_name(db), "Pierrick Dupont")
            self.assertEqual(
                annotators.current_annotator(db).orcid, "0000-0002-1825-0097",
            )

    def test_nom_obligatoire(self):
        with session_scope(self.db_path) as db:
            with self.assertRaises(ValueError):
                annotators.create_annotator(db, display_name="   ")

    def test_meme_nom_ne_cree_pas_de_doublon(self):
        with session_scope(self.db_path) as db:
            first = annotators.create_annotator(db, display_name="Alice")
            second = annotators.create_annotator(
                db, display_name="Alice", orcid="0000-0002-1825-0097",
            )
            self.assertEqual(first.id, second.id)
            # L'ORCID manquant est complete au passage.
            self.assertEqual(second.orcid, "0000-0002-1825-0097")
        with session_scope(self.db_path) as db:
            self.assertEqual(annotators.count_annotators(db), 1)

    def test_meme_nom_casse_et_espaces_ne_cree_pas_de_doublon(self):
        with session_scope(self.db_path) as db:
            first = annotators.create_annotator(db, display_name="Thomas Lamy")
            second = annotators.create_annotator(
                db, display_name="  thomas   LAMY  ",
            )
            self.assertEqual(first.id, second.id)
            self.assertEqual(second.display_name, "Thomas Lamy")
            self.assertEqual(annotators.count_annotators(db), 1)

    def test_noms_des_sessions_existantes_sont_proposes(self):
        with session_scope(self.db_path) as db:
            annotators.create_annotator(db, display_name="Alice Martin")
            db.add(CaptureSession(
                id=str(uuid.uuid4()), name="Sortie", site="Récif",
                session_date=datetime(2026, 8, 18), operator="  thomas  lamy ",
            ))
            db.add(CaptureSession(
                id=str(uuid.uuid4()), name="Sortie 2", site="Récif",
                session_date=datetime(2026, 8, 19), operator="THOMAS LAMY",
            ))
            db.flush()
            self.assertEqual(
                annotators.list_known_annotator_names(db),
                ["Alice Martin", "thomas lamy"],
            )

            row = annotators.create_annotator(
                db, display_name="  Thomas   LAMY ",
            )
            self.assertEqual(row.display_name, "thomas lamy")
            self.assertEqual(annotators.count_annotators(db), 2)

    def test_orcid_normalise_ou_refuse(self):
        self.assertIsNone(annotators.normalize_orcid("  "))
        self.assertEqual(
            annotators.normalize_orcid("https://orcid.org/0000-0002-1825-0097"),
            "0000-0002-1825-0097",
        )
        self.assertEqual(
            annotators.normalize_orcid("0000000218250097"), "0000-0002-1825-0097",
        )
        self.assertEqual(annotators.normalize_orcid("0000-0002-1825-009x"),
                         "0000-0002-1825-009X")
        with self.assertRaises(ValueError):
            annotators.normalize_orcid("Pierrick")

    def test_identite_unique_retenue_sans_reglage(self):
        """Une seule identite en base : inutile de demander laquelle."""
        with session_scope(self.db_path) as db:
            annotators.create_annotator(db, display_name="Solo")
        with session_scope(self.db_path) as db:
            self.assertFalse(annotators.needs_identity(db))
            self.assertEqual(annotators.current_author_name(db), "Solo")


class AuthorOnWritesTest(AnnotatorCase):
    """`author` doit porter l'annotateur courant, plus jamais 'operator'."""

    def setUp(self):
        super().setUp()
        with session_scope(self.db_path) as db:
            proj = Project(id=str(uuid.uuid4()), name="test")
            db.add(proj)
            db.flush()
            media = MediaAsset(
                id=str(uuid.uuid4()), project_id=proj.id, media_type="video",
                rel_path="clip.mp4",
            )
            db.add(media)
            db.flush()
            self.media_id = media.id
            track = Track(id=str(uuid.uuid4()), media_id=media.id, external_track_id=1)
            db.add(track)
            db.flush()
            self.track_id = track.id
            annotators.create_annotator(db, display_name="Pierrick")

    def test_annotation_signee_par_l_annotateur_courant(self):
        with session_scope(self.db_path) as db:
            author = annotators.current_author_name(db)
            ann = spatial.add_spatial_annotation(
                db, media_id=self.media_id, geom_type="bbox",
                geometry=spatial.make_bbox_geometry(0, 0, 10, 10),
                author=author, source="manual",
            )
            ann_id = ann.id
        with session_scope(self.db_path) as db:
            from src.annodb.models import SpatialAnnotation

            self.assertEqual(db.get(SpatialAnnotation, ann_id).author, "Pierrick")

    def test_evenement_signe_par_l_annotateur_courant(self):
        from src.annodb.events import add_temporal_event

        with session_scope(self.db_path) as db:
            ev = add_temporal_event(
                db, track_id=self.track_id, frame_start=10, frame_end=20,
                source="manual", author=annotators.current_author_name(db),
            )
            ev_id = ev.id
        with session_scope(self.db_path) as db:
            self.assertEqual(db.get(TemporalEvent, ev_id).author, "Pierrick")

    def test_annotateur_inconnu_laisse_author_vide(self):
        """Pas d'identite = `author` NULL, jamais une identite inventee."""
        with session_scope(self.db_path) as db:
            for row in db.query(Annotator).all():
                db.delete(row)
        with session_scope(self.db_path) as db:
            self.assertIsNone(annotators.current_author_name(db))


if __name__ == "__main__":
    unittest.main()
