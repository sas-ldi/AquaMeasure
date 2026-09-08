"""Matrice de cohérence du statut d'identification et du repli legacy."""

from __future__ import annotations

import json
import sys
import unittest
from unittest.mock import patch

from annotations.helpers import FV_ROOT, TempDbCase, write_test_image

REPO_ROOT = FV_ROOT.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from src.annodb.connection import init_db, session_scope
from src.annodb.export_core import _has_authoritative_identification
from src.annodb.identification import (
    authoritative_identification_clause,
    is_authoritative_identification,
)
from src.annodb.models import MediaAsset, Project, SpatialAnnotation, TaxonNode
from src.annodb.spatial import make_bbox_geometry


class IdentificationPolicyMatrixTest(TempDbCase):
    def setUp(self):
        super().setUp()
        init_db(self.db_path, seed=False)
        self.image = self.tmp_path / "matrix.jpg"
        write_test_image(self.image, width=64, height=48)
        geometry = json.dumps(make_bbox_geometry(
            8, 8, 48, 40, ref_width=64, ref_height=48,
        ))
        self.expected: dict[str, bool] = {}
        with session_scope(self.db_path) as session:
            session.add(Project(id="project-matrix", name="madagascar_measure"))
            session.add(TaxonNode(
                id="species-matrix", rank="species",
                scientific_name="Acanthurus lineatus",
            ))
            session.flush()
            session.add(MediaAsset(
                id="media-matrix", project_id="project-matrix",
                media_type="image", rel_path=str(self.image),
                width=64, height=48,
            ))
            session.flush()
            for status in (None, "identified", "unreviewed"):
                for source in ("validated", "manual", "cvat", "model"):
                    status_label = status or "null"
                    ann_id = f"{status_label}-{source}"
                    authoritative = (
                        status == "identified"
                        or (status is None and source in {"validated", "manual", "cvat"})
                    )
                    self.expected[ann_id] = authoritative
                    session.add(SpatialAnnotation(
                        id=ann_id, media_id="media-matrix", frame_index=0,
                        frame_ref="absolute", geom_type="bbox",
                        geometry_json=geometry, taxon_node_id="species-matrix",
                        source=source, identification_status=status,
                    ))

    def test_registry_stats_export_and_gallery_share_the_same_matrix(self):
        import fish_annotate as fa
        import fish_db_stats as fdb
        import fishial_gallery as fg
        import numpy as np

        with session_scope(self.db_path) as session:
            rows = session.query(SpatialAnnotation).all()
            python_decisions = {
                row.id: is_authoritative_identification(row) for row in rows
            }
            export_decisions = {
                row.id: _has_authoritative_identification(row, session)
                for row in rows
            }
            sql_ids = {
                row.id for row in session.query(SpatialAnnotation).filter(
                    authoritative_identification_clause(SpatialAnnotation)
                )
            }

        self.assertEqual(python_decisions, self.expected)
        self.assertEqual(export_decisions, self.expected)
        self.assertEqual(sql_ids, {
            ann_id for ann_id, expected in self.expected.items() if expected
        })

        # Le registre doit encore voir les vrais NULL : il ne les transforme
        # plus en statut explicite ``unreviewed``.
        registry = {
            row["ann_id"]: row["identification_status"]
            for row in fa.list_observations(media_id="media-matrix")
        }
        self.assertIsNone(registry["null-cvat"])
        self.assertEqual(registry["unreviewed-cvat"], "unreviewed")

        expected_count = sum(self.expected.values())
        self.assertEqual(fdb.count_validated_crops("species-matrix"), expected_count)
        summary = fdb.list_species_summary()
        self.assertEqual(summary[0]["annotation_count"], expected_count)
        # Aucun crop n'a été matérialisé par ce jeu de données. Le résumé
        # distingue désormais les observations des images conservées.
        self.assertEqual(summary[0]["crop_count"], 0)

        added = fg.build_references_for_taxon(
            "species-matrix",
            embedder=lambda _crop: np.ones(4, dtype=np.float32),
        )
        self.assertEqual(added, expected_count)

    def test_family_and_genus_are_excluded_from_species_summary_and_promotion(self):
        import fish_db_stats as fdb
        import fishial_gallery as fg
        import numpy as np
        from src.annodb.models import TaxonReferenceEmbedding

        geometry = json.dumps(make_bbox_geometry(
            8, 8, 48, 40, ref_width=64, ref_height=48,
        ))
        with session_scope(self.db_path) as session:
            session.add_all([
                TaxonNode(
                    id="family-only", rank="family", scientific_name="Scaridae",
                ),
                TaxonNode(
                    id="genus-only", rank="genus", scientific_name="Scarus",
                ),
            ])
            session.flush()
            for rank in ("family", "genus"):
                session.add(SpatialAnnotation(
                    id=f"ann-{rank}", media_id="media-matrix", frame_index=1,
                    frame_ref="absolute", geom_type="bbox",
                    geometry_json=geometry, taxon_node_id=f"{rank}-only",
                    source="manual", identification_status="identified",
                ))

        summary_ids = {
            row["taxon_node_id"] for row in fdb.list_species_summary()
        }
        self.assertNotIn("family-only", summary_ids)
        self.assertNotIn("genus-only", summary_ids)
        for taxon_id in ("family-only", "genus-only"):
            self.assertFalse(fdb.species_eligible_for_promotion(taxon_id, min_refs=1))
            result = fg._build_project_references_report(
                taxon_id,
                embedder=lambda _crop: np.ones(4, dtype=np.float32),
            )
            self.assertFalse(result["ok"])
            self.assertIn("rang espèce", result["error"])
        with session_scope(self.db_path) as session:
            self.assertEqual(session.query(TaxonReferenceEmbedding).count(), 0)

    def test_project_promotion_eligibility_reports_threshold_catalog_and_success(self):
        import fish_db_stats as fdb

        with patch.object(fdb, "_fishial_catalog_names", return_value=set()):
            threshold = fdb.species_promotion_eligibility(
                "species-matrix", min_refs=8,
            )
            eligible = fdb.species_promotion_eligibility(
                "species-matrix", min_refs=7,
            )
        self.assertFalse(threshold["eligible"])
        self.assertEqual(threshold["crop_count"], 7)
        self.assertEqual(threshold["min_refs"], 8)
        self.assertIn("Seuil insuffisant", threshold["reason"])
        self.assertTrue(eligible["eligible"])

        with patch.object(
            fdb, "_fishial_catalog_names", return_value={"acanthurus lineatus"},
        ):
            catalog = fdb.species_promotion_eligibility(
                "species-matrix", min_refs=1,
            )
        # Une espèce déjà connue accepte aussi de nouvelles références.
        self.assertTrue(catalog["eligible"])
        self.assertTrue(catalog["in_fishial_catalog"])


if __name__ == "__main__":
    unittest.main()
