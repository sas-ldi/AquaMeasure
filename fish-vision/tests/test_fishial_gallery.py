"""Galerie Fishial : la chaine image -> crop -> embedding doit fonctionner.

`build_references_for_taxon` appelait `materialize_frame_image` et
`crop_from_geometry` avec de **mauvaises signatures** : la chaine levait un
TypeError avale plus haut, et `taxon_reference_embeddings` est resté à 0 ligne
depuis toujours. On teste ici la chaine reelle avec un embedder factice — le
modele Fishial n'est pas installe dans l'environnement de test.
"""

from __future__ import annotations

import sqlite3
import sys
import tempfile
import threading
import unittest
import uuid
from datetime import datetime
from pathlib import Path

from tests.helpers import (
    FV_ROOT,
    TempDbCase,
    set_camera_params_env,
    write_fake_calibration,
    write_test_image,
)

# `fishial_gallery.py` vit a la racine du depot, a cote de fish_annotate.py.
_REPO_ROOT = FV_ROOT.parent
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from src.annodb import spatial
from src.annodb.connection import init_db, session_scope
from src.annodb.models import (
    Calibration,
    CaptureSession,
    MediaAsset,
    Project,
    SpatialAnnotation,
    TaxonNode,
    TaxonReferenceEmbedding,
)


class _FakeEmbedder:
    """Embedder factice : retient les crops recus, renvoie un vecteur stable."""

    def __init__(self):
        self.crops = []

    def __call__(self, crop):
        import numpy as np

        self.crops.append(crop)
        return np.full(8, 0.25, dtype=np.float32)


class GalleryCropChainTest(TempDbCase):
    def setUp(self):
        super().setUp()
        init_db(self.db_path, seed=False)
        # Photo importee : jamais rectifiee (elle n'appartient pas au banc).
        self.image_path = self.tmp_path / "media" / "poisson.jpg"
        write_test_image(self.image_path, width=64, height=48)
        set_camera_params_env(self, None)

        with session_scope(self.db_path) as db:
            proj = Project(id=str(uuid.uuid4()), name="madagascar_measure")
            db.add(proj)
            db.add(TaxonNode(id="sp-1", rank="species", scientific_name="Acanthurus lineatus"))
            db.flush()
            media = MediaAsset(
                id=str(uuid.uuid4()), project_id=proj.id, media_type="image",
                rel_path=str(self.image_path), width=64, height=48,
            )
            db.add(media)
            db.flush()
            self.media_id = media.id
            spatial.add_spatial_annotation(
                db, media_id=media.id, geom_type="bbox",
                geometry=spatial.make_bbox_geometry(
                    16, 12, 48, 36, ref_width=64, ref_height=48,
                ),
                taxon_node_id="sp-1", source="validated",
                identification_status="identified",
            )
            spatial.add_spatial_annotation(
                db, media_id=media.id, geom_type="bbox",
                geometry=spatial.make_bbox_geometry(
                    18, 14, 46, 34, ref_width=64, ref_height=48,
                ),
                taxon_node_id="sp-1", source="manual",
                identification_status="unreviewed",
            )

    def test_chaine_crop_produit_un_embedding(self):
        import fishial_gallery as fg

        embedder = _FakeEmbedder()
        added = fg.build_references_for_taxon("sp-1", embedder=embedder)
        self.assertEqual(added, 1, "la ligne manuelle non relue doit être exclue")
        self.assertEqual(len(embedder.crops), 1)
        crop = embedder.crops[0]
        # Le crop est bien un morceau d'image, pas l'image entiere ni un None.
        self.assertGreater(crop.size, 0)
        self.assertLess(crop.shape[0], 48)
        self.assertLess(crop.shape[1], 64)

    def test_embedding_enregistre_en_base(self):
        import fishial_gallery as fg

        fg.build_references_for_taxon("sp-1", embedder=_FakeEmbedder())
        with session_scope(self.db_path) as db:
            rows = db.query(TaxonReferenceEmbedding).all()
            self.assertEqual(len(rows), 1)
            self.assertEqual(rows[0].taxon_node_id, "sp-1")
            self.assertEqual(rows[0].media_id, self.media_id)
            self.assertTrue(rows[0].embedding)

    def test_taxon_sans_cliche_ne_produit_rien(self):
        import fishial_gallery as fg

        self.assertEqual(
            fg.build_references_for_taxon("sp-inconnu", embedder=_FakeEmbedder()), 0,
        )

    def test_rebuild_excludes_historical_unreviewed_and_unsafe_legacy(self):
        import numpy as np
        import fishial_gallery as fg

        with session_scope(self.db_path) as db:
            identified = db.query(SpatialAnnotation).filter_by(
                identification_status="identified"
            ).one()
            unreviewed = db.query(SpatialAnnotation).filter_by(
                identification_status="unreviewed"
            ).one()
            vectors = [
                ("ref-identified", identified.id, "validated", [1.0, 0.0]),
                ("ref-unreviewed", unreviewed.id, "validated", [0.0, 1.0]),
                # Sans annotation liée, seule une source humaine legacy sûre
                # peut encore contribuer.
                ("ref-legacy-safe", None, "cvat", [1.0, 0.0]),
                ("ref-legacy-model", None, "model", [0.0, 1.0]),
            ]
            for ref_id, ann_id, source, vector in vectors:
                db.add(TaxonReferenceEmbedding(
                    id=ref_id, taxon_node_id="sp-1",
                    spatial_annotation_id=ann_id, media_id=self.media_id,
                    embedding=np.asarray(vector, dtype=np.float32).tobytes(),
                    source=source,
                ))

        from unittest.mock import patch

        with patch("src.annodb.app_settings.get_setting", return_value=1):
            centroids = fg.rebuild_centroids()
        np.testing.assert_allclose(
            centroids["sp-1"], np.asarray([1.0, 0.0], dtype=np.float32),
        )


class IncrementalProjectGalleryTest(TempDbCase):
    def setUp(self):
        super().setUp()
        init_db(self.db_path, seed=False)
        set_camera_params_env(self, None)
        with session_scope(self.db_path) as db:
            db.add(Project(id="incremental-project", name="madagascar_measure"))
            db.add_all([
                TaxonNode(id="known", rank="species", scientific_name="Acanthurus lineatus"),
                TaxonNode(id="new", rank="species", scientific_name="Novafish exemplaris"),
                TaxonNode(id="few", rank="species", scientific_name="Rarafish exemplaris"),
            ])
            db.flush()
            for index in range(3):
                path = self.tmp_path / "media" / f"session-{index}.jpg"
                write_test_image(path, width=64, height=48)
                media_id = f"incremental-media-{index}"
                db.add(MediaAsset(id=media_id, project_id="incremental-project",
                                  media_type="image", rel_path=str(path), width=64, height=48))
                db.flush()
                db.add(CaptureSession(id=f"incremental-session-{index}", name=f"Sortie {index}",
                                      site="Bassin test", session_date=datetime(2026, 9, 8),
                                      left_media_id=media_id))
        import fishial_gallery as fg
        fg.invalidate_cache()

    def add_observations(self, taxon_id, count, session_index=0, *, reviewed=True):
        with session_scope(self.db_path) as db:
            for _ in range(count):
                spatial.add_spatial_annotation(
                    db, media_id=f"incremental-media-{session_index}", geom_type="bbox",
                    geometry=spatial.make_bbox_geometry(16, 12, 48, 36, ref_width=64, ref_height=48),
                    taxon_node_id=taxon_id, source="manual",
                    identification_status="identified" if reviewed else "unreviewed",
                )

    def test_twenty_then_sixty_across_three_sessions_without_replacing_references(self):
        from unittest.mock import patch
        import fishial_gallery as fg
        import fish_db_stats as fdb

        self.add_observations("known", 20)
        self.add_observations("few", 4)
        self.add_observations("new", 8, reviewed=False)
        embedder = _FakeEmbedder()
        with patch("fishial_gallery._resolve_embedder", return_value=embedder), patch(
            "fish_db_stats.get_app_settings", return_value={"fishial_min_refs": 5},
        ), patch("fish_db_stats._fishial_catalog_names", return_value={"acanthurus lineatus"}):
            first = fg.promote_all_new_references()
            self.assertTrue(first["ok"])
            self.assertEqual((first["added"], first["species_below_threshold"]), (20, 1))
            with session_scope(self.db_path) as db:
                original = {row.id: row.embedding for row in db.query(TaxonReferenceEmbedding)}
            self.add_observations("known", 20, 1)
            self.add_observations("known", 20, 2)
            self.add_observations("new", 5, 2)
            rows = {row["taxon_node_id"]: row for row in fdb.list_species_summary()}
            self.assertTrue(rows["known"]["in_fishial_catalog"])
            self.assertTrue(rows["known"]["promotion_eligible"])
            self.assertEqual(rows["known"]["pending_reference_count"], 40)
            progress = []
            second = fg.promote_all_new_references(progress=lambda *item: progress.append(item))
            self.assertTrue(second["ok"])
            self.assertEqual((second["added"], second["existing"]), (45, 20))
            self.assertEqual(len(progress), 2)
            self.assertEqual(len(embedder.crops), 65)
            with session_scope(self.db_path) as db:
                final = {row.id: row.embedding for row in db.query(TaxonReferenceEmbedding)}
                self.assertEqual(db.query(TaxonReferenceEmbedding).filter_by(taxon_node_id="known").count(), 60)
                self.assertEqual(db.query(TaxonReferenceEmbedding).filter_by(taxon_node_id="few").count(), 0)
            self.assertEqual({key: final[key] for key in original}, original)
            third = fg.promote_all_new_references()
            self.assertTrue(third["ok"])
            self.assertEqual((third["added"], third["species_total"]), (0, 0))
            self.assertEqual(len(embedder.crops), 65)

    def test_partial_failure_preserves_successes_and_leaves_failed_species_pending(self):
        from unittest.mock import patch
        import fishial_gallery as fg
        import fish_db_stats as fdb

        self.add_observations("known", 5)
        self.add_observations("new", 5, 1)
        promote = fg.promote_taxon_to_gallery

        def fail_one(taxon_id):
            if taxon_id == "known":
                raise OSError("Image inaccessible")
            return promote(taxon_id)

        with patch("fishial_gallery._resolve_embedder", return_value=_FakeEmbedder()), patch(
            "fish_db_stats.get_app_settings", return_value={"fishial_min_refs": 5},
        ), patch("fishial_gallery.promote_taxon_to_gallery", side_effect=fail_one):
            result = fg.promote_all_new_references()
            self.assertFalse(result["ok"])
            self.assertEqual((result["added"], result["species_processed"]), (5, 2))
            self.assertIn("Image inaccessible", result["error"])
            rows = {row["taxon_node_id"]: row for row in fdb.list_species_summary()}
            self.assertEqual(rows["known"]["pending_reference_count"], 5)
            self.assertEqual(rows["new"]["pending_reference_count"], 0)

    def test_legacy_reference_does_not_hide_a_new_observation(self):
        import numpy as np
        import fish_db_stats as fdb

        self.add_observations("known", 5)
        with session_scope(self.db_path) as db:
            db.add(TaxonReferenceEmbedding(
                id="legacy-local-reference", taxon_node_id="known",
                media_id="incremental-media-0", spatial_annotation_id=None,
                embedding=np.ones(8, dtype=np.float32).tobytes(), source="validated",
            ))
        row = next(row for row in fdb.list_species_summary() if row["taxon_node_id"] == "known")
        self.assertEqual(row["gallery_ref_count"], 1)
        self.assertEqual(row["pending_reference_count"], 5)


class GalleryRectifiedCropTest(TempDbCase):
    """Les crops de reference d'une VIDEO doivent etre rectifies.

    La classification en direct travaille sur `rect_l` : une galerie construite
    sur des images brutes se comparait a des requetes rectifiees.
    """

    def setUp(self):
        super().setUp()
        init_db(self.db_path, seed=True)
        self.params = self.tmp_path / "camera_parameters"
        write_fake_calibration(self.params, width=64, height=48)
        set_camera_params_env(self, self.params)
        self.video_path = self._write_video(self.tmp_path / "media" / "clip.mp4")
        self.right_video_path = self._write_video(
            self.tmp_path / "media" / "clip-right.mp4"
        )
        self.right_image_path = self.tmp_path / "media" / "right-raw.jpg"
        write_test_image(self.right_image_path, width=64, height=48)

        with session_scope(self.db_path) as db:
            from src.annodb.rectify import load_calibration_profile

            proj = Project(id=str(uuid.uuid4()), name="madagascar_measure")
            db.add(proj)
            db.add(TaxonNode(id="sp-1", rank="species", scientific_name="Acanthurus lineatus"))
            profile = load_calibration_profile()
            calibration = Calibration(
                id="calibration-gallery", profile_name=profile.name,
                sha256=profile.sha256,
            )
            db.add(calibration)
            db.flush()
            left = MediaAsset(
                id="video-left", project_id=proj.id, media_type="video",
                rel_path=str(self.video_path), width=64, height=48,
                calibration_id=calibration.id,
            )
            right = MediaAsset(
                id="video-right", project_id=proj.id, media_type="video",
                rel_path=str(self.right_video_path), width=64, height=48,
                calibration_id=calibration.id,
            )
            db.add_all((left, right))
            db.flush()
            db.add(CaptureSession(
                id="capture-calibrated", name="Calibration", site="Banc",
                session_date=datetime(2026, 8, 18), status="active",
                left_media_id=left.id, right_media_id=right.id,
                calibration_id=calibration.id,
                calibration_profile=profile.name,
                calibration_sha256=profile.sha256,
            ))
            right_image = MediaAsset(
                id="image-right", project_id=proj.id, media_type="image",
                rel_path=str(self.right_image_path), width=64, height=48,
            )
            db.add(right_image)
            db.flush()
            db.add(CaptureSession(
                id="capture-image-right", name="Image droite", site="Banc",
                session_date=datetime(2026, 8, 18), status="active",
                right_media_id=right_image.id,
            ))
            self.raw_ann_id = spatial.add_spatial_annotation(
                db, media_id=left.id, geom_type="bbox",
                geometry=spatial.make_bbox_geometry(
                    16, 12, 48, 36, space="raw",
                    ref_width=64, ref_height=48,
                ),
                frame_index=3, frame_ref="absolute",
                taxon_node_id="sp-1", source="validated",
                identification_status="identified",
                ann_id="ann-raw",
            ).id
            self.rectified_ann_id = spatial.add_spatial_annotation(
                db, media_id=left.id, geom_type="bbox",
                geometry=spatial.make_bbox_geometry(
                    16, 12, 48, 36, space="stereo_rectified_left",
                    ref_width=64, ref_height=48,
                ),
                frame_index=3, frame_ref="absolute",
                taxon_node_id="sp-1", source="validated",
                identification_status="identified", ann_id="ann-rectified",
            ).id
            self.right_ann_id = spatial.add_spatial_annotation(
                db, media_id=right.id, geom_type="bbox",
                geometry=spatial.make_bbox_geometry(
                    16, 12, 48, 36, space="raw",
                    ref_width=64, ref_height=48,
                ),
                frame_index=3, frame_ref="absolute",
                taxon_node_id="sp-1", source="validated",
                identification_status="identified", ann_id="ann-right",
            ).id
            self.right_image_ann_id = spatial.add_spatial_annotation(
                db, media_id=right_image.id, geom_type="bbox",
                geometry=spatial.make_bbox_geometry(
                    16, 12, 48, 36, space="raw", ref_width=64, ref_height=48,
                ),
                taxon_node_id="sp-1", source="validated",
                identification_status="identified", ann_id="ann-right-image",
            ).id

    @staticmethod
    def _write_video(path: Path, frames: int = 6) -> Path:
        import cv2
        import numpy as np

        path.parent.mkdir(parents=True, exist_ok=True)
        writer = cv2.VideoWriter(
            str(path), cv2.VideoWriter_fourcc(*"mp4v"), 10.0, (64, 48),
        )
        for i in range(frames):
            frame = np.full((48, 64, 3), 20 * i, dtype=np.uint8)
            frame[12:36, 16:48] = (255, 255, 255)
            writer.write(frame)
        writer.release()
        return path

    def test_raw_rectified_left_and_right_media_are_strictly_separated(self):
        import fishial_gallery as fg
        from src.annodb.rectify import load_left_rectifier
        from unittest.mock import patch

        base_rectifier = load_left_rectifier()
        self.assertIsNotNone(base_rectifier)

        class _VisibleRectifier:
            calibration_sha256 = base_rectifier.calibration_sha256

            def __call__(self, frame):
                import numpy as np

                return np.full_like(frame, 7)

        visible = _VisibleRectifier()
        with session_scope(self.db_path) as db, tempfile.TemporaryDirectory() as tmp:
            anns = {ann.id: ann for ann in db.query(SpatialAnnotation).all()}
            raw_crop, raw_reason, _ = fg._materialize_annotation_crop(
                db, anns[self.raw_ann_id], db.get(MediaAsset, "video-left"),
                Path(tmp), visible,
            )
            rect_crop, rect_reason, _ = fg._materialize_annotation_crop(
                db, anns[self.rectified_ann_id], db.get(MediaAsset, "video-left"),
                Path(tmp), visible,
            )
            right_crop, right_reason, _ = fg._materialize_annotation_crop(
                db, anns[self.right_ann_id], db.get(MediaAsset, "video-right"),
                Path(tmp), visible,
            )
        self.assertEqual(raw_reason, "")
        self.assertGreater(float(raw_crop.mean()), 7.0)
        self.assertEqual(rect_reason, "")
        self.assertAlmostEqual(float(rect_crop.mean()), 7.0, delta=0.5)
        self.assertIsNone(right_crop)
        self.assertEqual(right_reason, "media_droit_non_pris_en_charge")

        embedder = _FakeEmbedder()
        with patch("src.annodb.rectify.load_left_rectifier", return_value=visible):
            result = fg.build_session_references(
                self.raw_ann_id, capture_session_id="capture-calibrated",
                embedder=embedder,
            )
        self.assertEqual((result["added"], result["rejected"]), (2, 1))
        self.assertEqual(
            result["rejection_reasons"],
            {"media_droit_non_pris_en_charge": 1},
        )
        with session_scope(self.db_path) as db:
            self.assertEqual(db.query(TaxonReferenceEmbedding).count(), 2)
            self.assertEqual(
                db.query(TaxonReferenceEmbedding).filter_by(
                    spatial_annotation_id=self.right_ann_id,
                ).count(),
                0,
            )

    def test_right_raw_image_is_rejected_without_reference(self):
        import fishial_gallery as fg

        with session_scope(self.db_path) as db, tempfile.TemporaryDirectory() as tmp:
            ann = db.get(SpatialAnnotation, self.right_image_ann_id)
            media = db.get(MediaAsset, "image-right")
            crop, reason, _ = fg._materialize_annotation_crop(
                db, ann, media, Path(tmp),
            )
        self.assertIsNone(crop)
        self.assertEqual(reason, "media_droit_non_pris_en_charge")

        result = fg.build_session_references(
            self.right_image_ann_id,
            capture_session_id="capture-image-right",
            embedder=_FakeEmbedder(),
        )
        self.assertFalse(result["ok"])
        self.assertEqual(result["added"], 0)
        self.assertEqual(
            result["rejection_reasons"], {"media_droit_non_pris_en_charge": 1},
        )
        with session_scope(self.db_path) as db:
            self.assertEqual(db.query(TaxonReferenceEmbedding).count(), 0)


class GalleryWithoutFishialTest(unittest.TestCase):
    def test_message_clair_quand_l_embedder_manque(self):
        """Sans Fishial, echouer proprement — pas un « 0 embedding » trompeur."""
        import fishial_gallery as fg

        try:
            import fishial_classify  # noqa: F401
        except ImportError:
            with self.assertRaises(fg.FishialUnavailableError) as ctx:
                fg._resolve_embedder()
            self.assertIn("Fishial", str(ctx.exception))
        else:
            self.assertTrue(callable(fg._resolve_embedder()))


class SessionGalleryPromotionTest(TempDbCase):
    """Le lot est limité à la CaptureSession et idempotent par annotation."""

    def setUp(self):
        super().setUp()
        init_db(self.db_path, seed=True)
        self.image_path = self.tmp_path / "media" / "session.jpg"
        write_test_image(self.image_path, width=96, height=72)
        set_camera_params_env(self, None)
        with session_scope(self.db_path) as db:
            project = Project(id="project-session", name="madagascar_measure")
            other = TaxonNode(
                id="species-other", rank="species",
                scientific_name="Aqua altera",
            )
            family = TaxonNode(
                id="family-only", rank="family", scientific_name="Aquidae",
            )
            db.add_all((project, other, family))
            db.flush()
            media = MediaAsset(
                id="media-session", project_id=project.id, media_type="image",
                rel_path=str(self.image_path), width=96, height=72,
            )
            db.add(media)
            db.flush()
            db.add(CaptureSession(
                id="capture-session", name="Plongée test", site="Lagon",
                session_date=datetime(2026, 8, 18), status="active",
                left_media_id=media.id,
            ))
            annotation_ids = []
            for index in range(14):
                ann = spatial.add_spatial_annotation(
                    db, media_id=media.id, geom_type="bbox",
                    geometry=spatial.make_bbox_geometry(
                        8 + index, 10, 42 + index, 54,
                        ref_width=96, ref_height=72,
                    ),
                    taxon_node_id=None, source="manual",
                    identification_status="unreviewed",
                )
                annotation_ids.append(ann.id)
                if index == 0:
                    self.selected_ann_id = ann.id
            self.family_ann_id = spatial.add_spatial_annotation(
                db, media_id=media.id, geom_type="bbox",
                geometry=spatial.make_bbox_geometry(
                    4, 4, 30, 30, ref_width=96, ref_height=72,
                ),
                taxon_node_id=family.id, source="manual",
                identification_status="identified",
            ).id
            self.unreviewed_ann_id = spatial.add_spatial_annotation(
                db, media_id=media.id, geom_type="bbox",
                geometry=spatial.make_bbox_geometry(
                    50, 4, 80, 30, ref_width=96, ref_height=72,
                ),
                taxon_node_id=None, source="model",
                identification_status="unreviewed",
            ).id

        # Parcours réellement utilisé par l'UI : saisie libre du binôme puis
        # validation de chaque observation, sans mutation artificielle du
        # drapeau de l'annotation.
        import fish_annotate as fa

        self.species_id = None
        for ann_id in annotation_ids:
            resolved = fa.update_observation(
                ann_id,
                family_text="Aquidae",
                genus_text="Aqua",
                species_text="Aqua testensis",
                reviewed_by="Pierrick Test",
            )
            self.species_id = resolved["species_id"]
        with session_scope(self.db_path) as db:
            node = db.get(TaxonNode, self.species_id)
            self.assertTrue(node.is_provisional)

    def test_session_14_then_14_then_zero_and_provisional_species(self):
        import fishial_gallery as fg

        preview = fg.inspect_session_references(
            self.selected_ann_id, capture_session_id="capture-session",
        )
        self.assertTrue(preview["ok"])
        self.assertTrue(preview["is_provisional"])
        self.assertEqual(preview["candidates"], 14)
        self.assertEqual(preview["exploitable"], 14)
        self.assertEqual(preview["existing"], 0)
        self.assertEqual(preview["rejected"], 0)

        first = fg.build_session_references(
            self.selected_ann_id, capture_session_id="capture-session",
            embedder=_FakeEmbedder(),
        )
        self.assertEqual((first["added"], first["existing"]), (14, 0))
        second = fg.build_session_references(
            self.selected_ann_id, capture_session_id="capture-session",
            embedder=_FakeEmbedder(),
        )
        self.assertEqual((second["added"], second["existing"]), (0, 14))
        with session_scope(self.db_path) as db:
            refs = db.query(TaxonReferenceEmbedding).all()
            self.assertEqual(len(refs), 14)
            self.assertEqual(len({ref.spatial_annotation_id for ref in refs}), 14)

    def test_existing_session_and_project_references_do_not_require_media_or_embedder(self):
        import fishial_gallery as fg
        from unittest.mock import patch

        first = fg.build_session_references(
            self.selected_ann_id, capture_session_id="capture-session",
            embedder=_FakeEmbedder(),
        )
        self.assertEqual((first["added"], first["existing"]), (14, 0))

        with patch(
            "fishial_gallery._materialize_annotation_crop",
            side_effect=AssertionError("une référence valide ne relit pas son média"),
        ), patch(
            "fishial_gallery._resolve_embedder",
            side_effect=fg.FishialUnavailableError("MODEL ABSENT"),
        ):
            preview = fg.inspect_session_references(
                self.selected_ann_id, capture_session_id="capture-session",
            )
            second = fg.build_session_references(
                self.selected_ann_id, capture_session_id="capture-session",
            )
            project = fg._build_project_references_report(
                self.species_id,
            )

        self.assertTrue(preview["eligible"])
        self.assertEqual(
            (preview["exploitable"], preview["existing"], preview["rejected"]),
            (0, 14, 0),
        )
        self.assertTrue(second["ok"])
        self.assertEqual(
            (second["added"], second["existing"], second["rejected"]),
            (0, 14, 0),
        )
        self.assertTrue(project["ok"])
        self.assertEqual(
            (project["added"], project["existing"], project["rejected"]),
            (0, 14, 0),
        )

    def test_minimum_filters_proposals_and_cache_tracks_50_to_1_to_50(self):
        import types
        from unittest.mock import patch

        import numpy as np
        import fishial_gallery as fg

        with session_scope(self.db_path) as db:
            db.add_all((
                TaxonNode(
                    id="species-fifty", rank="species",
                    scientific_name="Aqua quinquaginta",
                ),
                TaxonNode(
                    id="species-one", rank="species",
                    scientific_name="Aqua minima",
                ),
            ))
            db.flush()
            for index in range(50):
                db.add(TaxonReferenceEmbedding(
                    id=f"ref-fifty-{index}", taxon_node_id="species-fifty",
                    media_id="media-session", frame_index=index,
                    embedding=np.asarray([1.0, 0.0], dtype=np.float32).tobytes(),
                    source="cvat",
                ))
            db.add(TaxonReferenceEmbedding(
                id="ref-one", taxon_node_id="species-one",
                media_id="media-session", frame_index=0,
                embedding=np.asarray([0.0, 1.0], dtype=np.float32).tobytes(),
                source="cvat",
            ))

        settings = {
            "fishial_min_refs": 50,
            "gallery_similarity_threshold": 0.55,
        }

        def get_setting(key, default=None):
            return settings.get(key, default)

        classifier = types.SimpleNamespace(
            embed_crop=lambda value, _box=None: np.asarray(
                [1.0, 0.0] if value == "fifty" else [0.0, 1.0],
                dtype=np.float32,
            ),
            classify_crop=lambda _value, _box=None: {
                "species_name": None,
                "species_conf": 0.0,
                "species_top3": [],
            },
        )
        with patch.dict(sys.modules, {"fishial_classify": classifier}), patch(
            "src.annodb.app_settings.get_setting", side_effect=get_setting,
        ):
            centroids = fg.rebuild_centroids()
            self.assertIn("species-fifty", centroids)
            self.assertNotIn("species-one", centroids)
            self.assertTrue(fg.classify_with_gallery("fifty")["gallery_match"])
            self.assertFalse(fg.classify_with_gallery("one")["gallery_match"])

            settings["fishial_min_refs"] = 1
            self.assertTrue(fg.classify_with_gallery("one")["gallery_match"])

            settings["fishial_min_refs"] = 50
            self.assertFalse(fg.classify_with_gallery("one")["gallery_match"])

    def test_project_count_uses_the_same_active_references_as_centroids(self):
        import fish_db_stats as fdb
        import fishial_gallery as fg

        first = fg.build_session_references(
            self.selected_ann_id, capture_session_id="capture-session",
            embedder=_FakeEmbedder(),
        )
        self.assertEqual((first["added"], first["existing"]), (14, 0))
        before = {
            row["taxon_node_id"]: row for row in fdb.list_species_summary()
        }
        self.assertEqual(before[self.species_id]["gallery_ref_count"], 14)

        with session_scope(self.db_path) as db:
            db.get(SpatialAnnotation, self.selected_ann_id).taxon_node_id = "species-other"
        fg.invalidate_cache()

        with session_scope(self.db_path) as db:
            self.assertEqual(fg.active_reference_counts(db)[self.species_id], 13)
        after = {
            row["taxon_node_id"]: row for row in fdb.list_species_summary()
        }
        self.assertEqual(after[self.species_id]["gallery_ref_count"], 13)
        self.assertIn(self.species_id, fg.rebuild_centroids())

    def test_partial_and_unreviewed_taxa_are_refused(self):
        import fishial_gallery as fg

        partial = fg.inspect_session_references(
            self.family_ann_id, capture_session_id="capture-session",
        )
        self.assertFalse(partial["ok"])
        self.assertIn("rang espèce", partial["error"])
        unreviewed = fg.inspect_session_references(
            self.unreviewed_ann_id, capture_session_id="capture-session",
        )
        self.assertFalse(unreviewed["ok"])
        self.assertIn("non relue", unreviewed["error"])

    def test_reclassification_and_deletion_revoke_references_and_cache(self):
        import fish_annotate as fa
        import fishial_gallery as fg

        fg.build_session_references(
            self.selected_ann_id, capture_session_id="capture-session",
            embedder=_FakeEmbedder(),
        )
        from unittest.mock import patch

        with patch("src.annodb.app_settings.get_setting", return_value=1):
            self.assertIn(self.species_id, fg.rebuild_centroids())
        # Chemin réellement utilisé par le registre QML.
        fa.update_observation(
            self.selected_ann_id, taxon_node_id="species-other",
        )
        with session_scope(self.db_path) as db:
            self.assertEqual(
                db.query(TaxonReferenceEmbedding).filter_by(
                    spatial_annotation_id=self.selected_ann_id,
                ).count(),
                0,
            )
        # Les treize autres références conservent l'ancien centroïde ; la
        # bbox reclassée ne doit plus y contribuer ni migrer implicitement.
        self.assertIn(self.species_id, fg.rebuild_centroids())

        remaining_id = None
        with session_scope(self.db_path) as db:
            remaining_id = db.query(TaxonReferenceEmbedding).first().spatial_annotation_id
        fa.delete_observation(remaining_id)
        with session_scope(self.db_path) as db:
            self.assertEqual(
                db.query(TaxonReferenceEmbedding).filter_by(
                    spatial_annotation_id=remaining_id,
                ).count(),
                0,
            )

    def test_empty_embedding_is_not_existing_and_is_repaired(self):
        import numpy as np
        import fishial_gallery as fg

        with session_scope(self.db_path) as db:
            db.add(TaxonReferenceEmbedding(
                id="empty-reference", taxon_node_id=self.species_id,
                spatial_annotation_id=self.selected_ann_id,
                media_id="media-session", frame_index=0,
                embedding=b"", source="validated",
            ))
        preview = fg.inspect_session_references(
            self.selected_ann_id, capture_session_id="capture-session",
        )
        self.assertEqual(preview["existing"], 0)
        result = fg.build_session_references(
            self.selected_ann_id, capture_session_id="capture-session",
            embedder=_FakeEmbedder(),
        )
        self.assertEqual((result["added"], result["existing"]), (14, 0))
        with session_scope(self.db_path) as db:
            repaired = db.query(TaxonReferenceEmbedding).filter_by(
                spatial_annotation_id=self.selected_ann_id,
            ).one()
            self.assertIsNotNone(fg._embedding_vector(repaired.embedding))
            self.assertFalse(np.array_equal(
                fg._embedding_vector(repaired.embedding), np.zeros(8),
            ))
        self.assertIn(self.species_id, fg.rebuild_centroids())

    def test_all_rejected_crops_and_embeddings_never_report_success(self):
        import numpy as np
        import fishial_gallery as fg
        from unittest.mock import patch

        with patch(
            "fishial_gallery._materialize_annotation_crop",
            return_value=(None, "crop_invalide", None),
        ):
            preview = fg.inspect_session_references(
                self.selected_ann_id, capture_session_id="capture-session",
            )
            result = fg.build_session_references(
                self.selected_ann_id, capture_session_id="capture-session",
                embedder=_FakeEmbedder(),
            )
        self.assertTrue(preview["ok"])
        self.assertFalse(preview["eligible"])
        self.assertEqual((preview["exploitable"], preview["rejected"]), (0, 14))
        self.assertFalse(result["ok"])
        self.assertEqual((result["added"], result["existing"]), (0, 0))

        invalid_vectors = (
            np.zeros(8, dtype=np.float64),
            np.asarray([np.nan] + [1.0] * 7, dtype=np.float64),
            np.asarray([np.inf] + [1.0] * 7, dtype=np.float64),
        )
        for vector in invalid_vectors:
            session_result = fg.build_session_references(
                self.selected_ann_id, capture_session_id="capture-session",
                embedder=lambda _crop, value=vector: value,
            )
            project_result = fg._build_project_references_report(
                self.species_id,
                embedder=lambda _crop, value=vector: value,
            )
            self.assertFalse(session_result["ok"])
            self.assertEqual(session_result["added"], 0)
            self.assertEqual(project_result["added"], 0)
            with session_scope(self.db_path) as db:
                self.assertEqual(db.query(TaxonReferenceEmbedding).count(), 0)

    def test_dimension_canonique_rejects_8_16_before_session_and_project_insert(self):
        import numpy as np
        import fishial_gallery as fg

        calls = 0

        def mixed(_crop):
            nonlocal calls
            calls += 1
            dimension = 8 if calls == 1 else 16
            return np.ones(dimension, dtype=np.float64)

        session_result = fg.build_session_references(
            self.selected_ann_id, capture_session_id="capture-session",
            embedder=mixed,
        )
        self.assertEqual((session_result["added"], session_result["rejected"]), (1, 13))
        project_result = fg._build_project_references_report(
            self.species_id,
            embedder=lambda _crop: np.ones(16, dtype=np.float32),
        )
        self.assertEqual(project_result["added"], 0)
        self.assertEqual(project_result["existing"], 1)
        with session_scope(self.db_path) as db:
            refs = db.query(TaxonReferenceEmbedding).all()
            self.assertEqual(len(refs), 1)
            self.assertEqual(len(refs[0].embedding), 8 * 4)
        from unittest.mock import patch

        with patch("src.annodb.app_settings.get_setting", return_value=1):
            self.assertIn(self.species_id, fg.rebuild_centroids())

    def test_rebuild_skips_legacy_dimension_mismatch_without_stack_failure(self):
        import numpy as np
        import fishial_gallery as fg

        with session_scope(self.db_path) as db:
            annotations = db.query(SpatialAnnotation).filter_by(
                taxon_node_id=self.species_id,
            ).order_by(SpatialAnnotation.id).limit(2).all()
            for index, (ann, dimension) in enumerate(zip(annotations, (8, 16))):
                db.add(TaxonReferenceEmbedding(
                    id=f"legacy-dimension-{dimension}",
                    taxon_node_id=self.species_id,
                    spatial_annotation_id=ann.id,
                    media_id=ann.media_id,
                    embedding=np.ones(dimension, dtype=np.float32).tobytes(),
                    source="validated",
                ))
        from unittest.mock import patch

        with patch("src.annodb.app_settings.get_setting", return_value=1):
            centroid = fg.rebuild_centroids()[self.species_id]
        self.assertEqual(centroid.shape, (8,))

    def test_two_concurrent_promotions_insert_each_annotation_once(self):
        import numpy as np
        import fishial_gallery as fg

        barrier = threading.Barrier(2)
        results = []
        errors = []

        class _ConcurrentEmbedder:
            def __init__(self):
                self.first = True

            def __call__(self, _crop):
                if self.first:
                    self.first = False
                    barrier.wait(timeout=5)
                return np.full(8, 0.25, dtype=np.float32)

        def promote():
            try:
                results.append(fg.build_session_references(
                    self.selected_ann_id,
                    capture_session_id="capture-session",
                    embedder=_ConcurrentEmbedder(),
                ))
            except Exception as exc:
                errors.append(exc)

        threads = [threading.Thread(target=promote) for _ in range(2)]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join(20)
            self.assertFalse(thread.is_alive())
        self.assertEqual(errors, [])
        self.assertEqual(len(results), 2)
        self.assertEqual(sum(row["added"] for row in results), 14)
        self.assertEqual(sum(row["existing"] for row in results), 14)
        with session_scope(self.db_path) as db:
            refs = db.query(TaxonReferenceEmbedding).all()
            self.assertEqual(len(refs), 14)
            self.assertEqual(len({row.spatial_annotation_id for row in refs}), 14)

    def test_reference_unicity_migration_is_idempotent(self):
        import numpy as np
        from src.annodb import connection

        connection.get_engine(self.db_path).dispose()
        connection._engine = None
        connection._SessionLocal = None
        with sqlite3.connect(self.db_path) as db:
            db.execute("DROP INDEX ux_taxon_ref_embeddings_annotation")
            values = (
                self.species_id, self.selected_ann_id, "media-session",
            )
            db.execute(
                "INSERT INTO taxon_reference_embeddings "
                "(id,taxon_node_id,spatial_annotation_id,media_id,frame_index,embedding,source) "
                "VALUES ('legacy-nan',?,?,?,?,?, 'validated')",
                (*values, 0, np.asarray([np.nan] + [1.0] * 7, dtype=np.float32).tobytes()),
            )
            db.execute(
                "INSERT INTO taxon_reference_embeddings "
                "(id,taxon_node_id,spatial_annotation_id,media_id,frame_index,embedding,source) "
                "VALUES ('legacy-zero',?,?,?,?,?, 'validated')",
                (*values, 0, np.zeros(8, dtype=np.float32).tobytes()),
            )
            db.execute(
                "INSERT INTO taxon_reference_embeddings "
                "(id,taxon_node_id,spatial_annotation_id,media_id,frame_index,embedding,source) "
                "VALUES ('legacy-dim16',?,?,?,?,?, 'validated')",
                (*values, 0, np.ones(16, dtype=np.float32).tobytes()),
            )
            db.execute(
                "INSERT INTO taxon_reference_embeddings "
                "(id,taxon_node_id,spatial_annotation_id,media_id,frame_index,embedding,source) "
                "VALUES ('legacy-valid',?,?,?,?,?, 'validated')",
                (*values, 0, np.ones(8, dtype=np.float32).tobytes()),
            )
            db.commit()

        for _ in range(2):
            connection._migrated_paths.clear()
            connection.ensure_schema(self.db_path)
        with sqlite3.connect(self.db_path) as db:
            rows = db.execute(
                "SELECT id, length(embedding) FROM taxon_reference_embeddings "
                "WHERE spatial_annotation_id = ?",
                (self.selected_ann_id,),
            ).fetchall()
            index_sql = db.execute(
                "SELECT sql FROM sqlite_master WHERE type='index' "
                "AND name='ux_taxon_ref_embeddings_annotation'"
            ).fetchone()[0]
        self.assertEqual(rows, [("legacy-valid", 32)])
        self.assertIn("UNIQUE INDEX", index_sql.upper())
        self.assertIn("WHERE spatial_annotation_id IS NOT NULL", index_sql)


if __name__ == "__main__":
    unittest.main()
