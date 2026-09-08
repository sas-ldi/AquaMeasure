"""Provenance du modele et statut d'identification (phase 1).

Deux regles se verifient ici :

- la provenance est **immuable par ajout** : valider une prediction ne doit
  jamais effacer le modele qui l'a proposee ni sa confiance ;
- le statut d'identification decrit ce qu'un humain a fait, pas ce que le
  modele a devine.
"""

from __future__ import annotations

import sqlite3
import unittest
import uuid

from tests.helpers import TempDbCase, dispose_engine

from src.annodb import spatial
from src.annodb.connection import init_db, session_scope
from src.annodb.models import (
    STATUS_IDENTIFIED,
    STATUS_UNIDENTIFIABLE,
    STATUS_UNREVIEWED,
    MediaAsset,
    Project,
    SpatialAnnotation,
    TaxonNode,
)


def _seed_media(db_path):
    with session_scope(db_path) as db:
        proj = Project(id=str(uuid.uuid4()), name="test")
        db.add(proj)
        db.flush()
        media = MediaAsset(
            id=str(uuid.uuid4()), project_id=proj.id, media_type="video",
            rel_path="clip.mp4", width=1920, height=1080,
        )
        db.add(media)
        db.add_all([
            TaxonNode(id="taxon-fish-generic", rank="provisional", scientific_name="fish"),
            TaxonNode(id="fam-1", rank="family", scientific_name="Acanthuridae"),
        ])
        db.flush()
        return media.id


class ProvenanceWriteTest(TempDbCase):
    def setUp(self):
        super().setUp()
        init_db(self.db_path, seed=False)
        self.media_id = _seed_media(self.db_path)

    def _add(self, **kwargs):
        with session_scope(self.db_path) as db:
            ann = spatial.add_spatial_annotation(
                db,
                media_id=self.media_id,
                geom_type="bbox",
                geometry=spatial.make_bbox_geometry(10, 10, 60, 60, ref_width=1920, ref_height=1080),
                frame_index=1200,
                source="model",
                confidence=0.87,
                model_id="aquameasure-family",
                model_sha256="a" * 64,
                model_conf_threshold=0.25,
                **kwargs,
            )
            return ann.id

    def test_provenance_ecrite_a_l_insertion(self):
        ann_id = self._add()
        with session_scope(self.db_path) as db:
            ann = db.get(SpatialAnnotation, ann_id)
            self.assertEqual(ann.model_id, "aquameasure-family")
            self.assertEqual(ann.model_sha256, "a" * 64)
            self.assertAlmostEqual(ann.model_conf_threshold, 0.25)
            self.assertAlmostEqual(ann.confidence, 0.87)

    def test_nouvelle_ligne_non_relue_par_defaut(self):
        ann_id = self._add()
        with session_scope(self.db_path) as db:
            ann = db.get(SpatialAnnotation, ann_id)
            self.assertEqual(ann.identification_status, STATUS_UNREVIEWED)
            self.assertIsNone(ann.reviewed_by)
            self.assertIsNone(ann.reviewed_at)
            self.assertIsNotNone(ann.updated_at)

    def test_validation_preserve_la_provenance(self):
        """Regle d'or : la relecture AJOUTE, elle n'efface jamais."""
        ann_id = self._add()
        with session_scope(self.db_path) as db:
            ann = db.get(SpatialAnnotation, ann_id)
            ann.taxon_node_id = "fam-1"
            spatial.mark_reviewed(ann, status=STATUS_IDENTIFIED, reviewed_by="Pierrick")
        with session_scope(self.db_path) as db:
            ann = db.get(SpatialAnnotation, ann_id)
            self.assertEqual(ann.identification_status, STATUS_IDENTIFIED)
            self.assertEqual(ann.reviewed_by, "Pierrick")
            self.assertIsNotNone(ann.reviewed_at)
            # Ce qui suit est l'objet meme du test.
            self.assertEqual(ann.source, "model")
            self.assertEqual(ann.model_id, "aquameasure-family")
            self.assertEqual(ann.model_sha256, "a" * 64)
            self.assertAlmostEqual(ann.model_conf_threshold, 0.25)
            self.assertAlmostEqual(ann.confidence, 0.87)

    def test_statut_inconnu_refuse(self):
        ann_id = self._add()
        with session_scope(self.db_path) as db:
            ann = db.get(SpatialAnnotation, ann_id)
            with self.assertRaises(ValueError):
                spatial.mark_reviewed(ann, status="valide")


class IdentificationBackfillTest(TempDbCase):
    """Backfill des lignes anterieures a l'ajout de `identification_status`."""

    def _legacy_db(self) -> sqlite3.Connection:
        from src.annodb import connection

        conn = sqlite3.connect(self.db_path)
        conn.executescript(connection._SCHEMA.read_text(encoding="utf-8"))
        conn.executescript(
            """
            INSERT INTO projects (id, name) VALUES ('p1', 'test');
            INSERT INTO media_assets (id, project_id, media_type, rel_path)
                VALUES ('m1', 'p1', 'video', 'clip.mp4');
            INSERT INTO taxon_nodes (id, rank, scientific_name)
                VALUES ('taxon-fish-generic', 'provisional', 'fish');
            INSERT INTO taxon_nodes (id, rank, scientific_name)
                VALUES ('fam-1', 'family', 'Acanthuridae');
            """
        )
        return conn

    def test_backfill_par_source_et_taxon(self):
        conn = self._legacy_db()
        rows = [
            # (id, source, taxon, statut attendu)
            ("a_val", "validated", "fam-1", "identified"),
            ("a_val_na", "validated", "taxon-fish-generic", "unidentifiable"),
            ("a_model", "model", "fam-1", "unreviewed"),
            ("a_heur", "heuristic", "fam-1", "unreviewed"),
            ("a_manual", "manual", "fam-1", "identified"),
            ("a_manual_gen", "manual", "taxon-fish-generic", "unreviewed"),
            ("a_cvat", "cvat", "fam-1", "identified"),
            ("a_manual_null", "manual", None, "unreviewed"),
        ]
        for ann_id, source, taxon, _ in rows:
            conn.execute(
                "INSERT INTO spatial_annotations "
                "(id, media_id, frame_index, geom_type, geometry_json, source, "
                " taxon_node_id, created_at) "
                "VALUES (?, 'm1', 100, 'bbox', '{}', ?, ?, '2026-01-02 03:04:05')",
                (ann_id, source, taxon),
            )
        conn.commit()
        cols = {r[1] for r in conn.execute("PRAGMA table_info(spatial_annotations)")}
        self.assertNotIn("identification_status", cols)
        self.assertNotIn("model_id", cols)
        conn.close()

        dispose_engine()
        from src.annodb import connection

        connection.ensure_schema(self.db_path)

        conn = sqlite3.connect(self.db_path)
        # Aucune ligne perdue.
        self.assertEqual(
            conn.execute("SELECT COUNT(*) FROM spatial_annotations").fetchone()[0],
            len(rows),
        )
        got = dict(conn.execute(
            "SELECT id, identification_status FROM spatial_annotations"
        ))
        for ann_id, _source, _taxon, expected in rows:
            self.assertEqual(got[ann_id], expected, ann_id)
        rank_na = conn.execute(
            "SELECT family_is_na, genus_is_na, species_is_na "
            "FROM spatial_annotations WHERE id='a_val_na'"
        ).fetchone()
        self.assertEqual(rank_na, (1, 1, 1))
        unknown_rank_na = conn.execute(
            "SELECT family_is_na, genus_is_na, species_is_na "
            "FROM spatial_annotations WHERE id='a_model'"
        ).fetchone()
        self.assertEqual(unknown_rank_na, (None, None, None))

        # reviewed_at = created_at pour les lignes relues, faute de mieux :
        # la date de validation n'a jamais ete enregistree jusqu'ici.
        reviewed = dict(conn.execute(
            "SELECT id, reviewed_at FROM spatial_annotations "
            "WHERE identification_status IN ('identified', 'unidentifiable')"
        ))
        self.assertTrue(reviewed)
        for ann_id, value in reviewed.items():
            self.assertEqual(value, "2026-01-02 03:04:05", ann_id)
        # Les lignes non relues n'inventent pas de date de relecture.
        self.assertIsNone(conn.execute(
            "SELECT reviewed_at FROM spatial_annotations WHERE id='a_model'"
        ).fetchone()[0])
        conn.close()

    def test_backfill_non_rejoue_au_demarrage_suivant(self):
        """Un second `ensure_schema` ne doit pas ecraser les statuts poses depuis."""
        conn = self._legacy_db()
        conn.execute(
            "INSERT INTO spatial_annotations "
            "(id, media_id, frame_index, geom_type, geometry_json, source, taxon_node_id) "
            "VALUES ('a1', 'm1', 100, 'bbox', '{}', 'model', 'fam-1')"
        )
        conn.commit()
        conn.close()

        dispose_engine()
        from src.annodb import connection

        connection.ensure_schema(self.db_path)
        with session_scope(self.db_path) as db:
            ann = db.get(SpatialAnnotation, "a1")
            spatial.mark_reviewed(ann, status=STATUS_UNIDENTIFIABLE, reviewed_by="Alice")

        dispose_engine()
        connection.ensure_schema(self.db_path)
        with session_scope(self.db_path) as db:
            ann = db.get(SpatialAnnotation, "a1")
            self.assertEqual(ann.identification_status, STATUS_UNIDENTIFIABLE)
            self.assertEqual(ann.reviewed_by, "Alice")

    def test_migration_idempotente(self):
        init_db(self.db_path, seed=False)
        dispose_engine()
        from src.annodb import connection

        connection.ensure_schema(self.db_path)
        conn = sqlite3.connect(self.db_path)
        cols = [r[1] for r in conn.execute("PRAGMA table_info(spatial_annotations)")]
        for col in ("model_id", "model_sha256", "model_conf_threshold",
                    "identification_status", "family_is_na", "genus_is_na",
                    "species_is_na", "reviewed_by", "reviewed_at", "updated_at"):
            self.assertEqual(cols.count(col), 1, col)
        conn.close()


if __name__ == "__main__":
    unittest.main()
