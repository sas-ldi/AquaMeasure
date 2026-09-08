"""Statistiques de session : lecture des index historiques et des géométries."""

from __future__ import annotations

import json
import unittest
import uuid

from annotations.helpers import (
    TempDbCase,
    set_camera_params_env,
    write_sync_frames,
)

from src.annodb.connection import init_db, session_scope
from src.annodb.models import (
    MediaAsset,
    Project,
    SpatialAnnotation,
    TaxonNode,
    Track,
    TrackSample,
)
from src.annodb.session_stats import (
    TIMELINE_CSV_FIELDS,
    compute_session_stats,
    iter_session_timeline_rows,
)
from src.annodb.spatial import make_bbox_geometry

WIDTH, HEIGHT = 1920, 1080
OFFSET = 2440


class TimelineRowsTest(TempDbCase):
    def setUp(self):
        super().setUp()
        init_db(self.db_path, seed=False)
        cam = self.tmp_path / "camera_parameters"
        write_sync_frames(cam, OFFSET, OFFSET + 112)
        set_camera_params_env(self, cam)

        self.media_id = str(uuid.uuid4())
        geom = json.dumps(make_bbox_geometry(
            100, 200, 300, 400, space="stereo_rectified_left",
            ref_width=WIDTH, ref_height=HEIGHT,
        ))
        legacy_geom = json.dumps(
            {"x_min": 100.0, "y_min": 200.0, "x_max": 300.0, "y_max": 400.0}
        )
        with session_scope(self.db_path) as session:
            proj = Project(id=str(uuid.uuid4()), name="test")
            session.add(proj)
            session.flush()
            session.add(MediaAsset(
                id=self.media_id, project_id=proj.id, media_type="video",
                rel_path="clip.mp4", width=WIDTH, height=HEIGHT, fps=30.0,
                frame_count=9000,
            ))
            session.flush()
            session.add_all([
                SpatialAnnotation(
                    id="ann-abs", media_id=self.media_id, frame_index=3940,
                    frame_ref="absolute", geom_type="bbox", geometry_json=geom,
                ),
                SpatialAnnotation(
                    id="ann-legacy", media_id=self.media_id, frame_index=1500,
                    frame_ref="timeline_legacy", geom_type="bbox",
                    geometry_json=legacy_geom,
                ),
            ])

    def test_index_sortis_en_absolu_avec_referentiel(self):
        with session_scope(self.db_path) as session:
            media = session.get(MediaAsset, self.media_id)
            rows = {r["frame_ref"]: r for r in iter_session_timeline_rows(session, media)}

        self.assertEqual(rows["absolute"]["frame_index"], 3940)
        # 1500 (timeline) + 2440 = 3940 : les deux lignes tombent sur la même image.
        self.assertEqual(rows["timeline_legacy"]["frame_index"], 3940)

    def test_colonnes_bbox_remplies_pour_les_deux_formats(self):
        with session_scope(self.db_path) as session:
            media = session.get(MediaAsset, self.media_id)
            rows = {r["frame_ref"]: r for r in iter_session_timeline_rows(session, media)}

        for row in rows.values():
            self.assertAlmostEqual(float(row["bbox_x1"]), 100.0, places=2)
            self.assertAlmostEqual(float(row["bbox_y2"]), 400.0, places=2)
            self.assertAlmostEqual(float(row["cx"]), 200.0, places=2)
        self.assertEqual(rows["absolute"]["geometry_space"], "stereo_rectified_left")
        self.assertEqual(rows["timeline_legacy"]["geometry_space"], "")

    def test_colonnes_csv_declarees(self):
        self.assertIn("frame_ref", TIMELINE_CSV_FIELDS)
        self.assertIn("geometry_space", TIMELINE_CSV_FIELDS)

    def test_stats_signalent_les_lignes_historiques(self):
        with session_scope(self.db_path) as session:
            media = session.get(MediaAsset, self.media_id)
            stats = compute_session_stats(session, media)

        self.assertEqual(stats["frame_index_convention"], "absolute")
        self.assertEqual(stats["legacy_frame_ref_counts"]["spatial_annotations"], 1)
        self.assertIn("index timeline historique", stats["frame_ref_warning"])


class MissingSyncTest(TempDbCase):
    def test_avertissement_quand_la_conversion_est_impossible(self):
        init_db(self.db_path, seed=False)
        cam = self.tmp_path / "camera_parameters"
        cam.mkdir(parents=True, exist_ok=True)
        set_camera_params_env(self, cam)

        media_id = str(uuid.uuid4())
        with session_scope(self.db_path) as session:
            proj = Project(id=str(uuid.uuid4()), name="test")
            session.add(proj)
            session.flush()
            session.add(MediaAsset(
                id=media_id, project_id=proj.id, media_type="video",
                rel_path="clip.mp4", width=WIDTH, height=HEIGHT, fps=30.0,
            ))
            session.flush()
            session.add(SpatialAnnotation(
                id="ann-legacy", media_id=media_id, frame_index=1500,
                frame_ref="timeline_legacy", geom_type="bbox", geometry_json="{}",
            ))

        with session_scope(self.db_path) as session:
            media = session.get(MediaAsset, media_id)
            with self.assertLogs("src.annodb.session_stats", level="WARNING") as caught:
                rows = list(iter_session_timeline_rows(session, media))
            stats = compute_session_stats(session, media)

        self.assertEqual(rows[0]["frame_index"], 1500)
        self.assertEqual(rows[0]["frame_ref"], "timeline_legacy")
        self.assertTrue(any("offset" in m or "timeline" in m for m in caught.output))
        self.assertIn("offset de sync indisponible", stats["frame_ref_warning"])


class IdentificationAuthorityStatsTest(TempDbCase):
    def test_unreviewed_taxon_never_enters_species_track_or_maximum(self):
        init_db(self.db_path, seed=False)
        media_id = "media-authority"
        geom = json.dumps(make_bbox_geometry(10, 10, 40, 40))
        with session_scope(self.db_path) as session:
            project = Project(id="project-authority", name="authority")
            session.add(project)
            session.add(TaxonNode(
                id="species-authority", rank="species",
                scientific_name="Acanthurus lineatus",
            ))
            session.add(TaxonNode(
                id="family-authority", rank="family",
                scientific_name="Acanthuridae",
            ))
            session.flush()
            session.add(MediaAsset(
                id=media_id, project_id=project.id, media_type="video",
                rel_path="authority.mp4", fps=25.0, frame_count=100,
            ))
            session.flush()
            session.add_all([
                Track(
                    id="track-identified", media_id=media_id,
                    external_track_id=1, source="bytetrack",
                    taxon_node_id="species-authority",
                ),
                Track(
                    id="track-unreviewed", media_id=media_id,
                    external_track_id=2, source="bytetrack",
                    taxon_node_id="species-authority",
                ),
                Track(
                    id="track-family", media_id=media_id,
                    external_track_id=3, source="bytetrack",
                    taxon_node_id="family-authority",
                ),
            ])
            session.flush()
            session.add_all([
                SpatialAnnotation(
                    id="ann-identified", media_id=media_id, frame_index=5,
                    geom_type="bbox", geometry_json=geom,
                    track_id="track-identified",
                    taxon_node_id="species-authority", source="model",
                    identification_status="identified",
                ),
                SpatialAnnotation(
                    id="ann-unreviewed", media_id=media_id, frame_index=5,
                    geom_type="bbox", geometry_json=geom,
                    track_id="track-unreviewed",
                    taxon_node_id="species-authority", source="model",
                    identification_status="unreviewed",
                ),
                SpatialAnnotation(
                    id="ann-family", media_id=media_id, frame_index=5,
                    geom_type="bbox", geometry_json=geom,
                    track_id="track-family", taxon_node_id="family-authority",
                    source="manual", identification_status="identified",
                    genus_is_na=True, species_is_na=True,
                ),
                TrackSample(
                    track_id="track-identified", frame_index=5,
                    cx=0.2, cy=0.2, bbox_json=geom,
                ),
                TrackSample(
                    track_id="track-unreviewed", frame_index=5,
                    cx=0.6, cy=0.6, bbox_json=geom,
                ),
                TrackSample(
                    track_id="track-family", frame_index=5,
                    cx=0.8, cy=0.8, bbox_json=geom,
                ),
            ])

        with session_scope(self.db_path) as session:
            media = session.get(MediaAsset, media_id)
            stats = compute_session_stats(session, media)

        self.assertEqual(stats["species_count"], 1)
        self.assertEqual(len(stats["max_per_species"]), 1)
        self.assertEqual(
            stats["max_per_species"][0]["taxon_node_id"], "species-authority",
        )
        self.assertEqual(stats["max_per_species"][0]["max_concurrent"], 1)

    def test_linked_track_authority_matrix_rejects_conflicts_with_or_without_sample(self):
        init_db(self.db_path, seed=False)
        geom = json.dumps(make_bbox_geometry(10, 10, 40, 40))
        cases = (
            ("explicit-with-sample", "ambiguous", True),
            ("explicit-without-sample", "ambiguous", False),
            ("legacy-with-sample", None, True),
            ("legacy-without-sample", None, False),
        )
        with session_scope(self.db_path) as session:
            session.add(Project(id="project-track-authority", name="authority-matrix"))
            session.add_all([
                TaxonNode(
                    id="species-a", rank="species", scientific_name="Scarus ghobban",
                ),
                TaxonNode(
                    id="species-b", rank="species", scientific_name="Scarus niger",
                ),
            ])
            session.flush()
            for index, (label, status, with_sample) in enumerate(cases):
                media_id = f"media-{label}"
                track_id = f"track-{label}"
                session.add(MediaAsset(
                    id=media_id,
                    project_id="project-track-authority",
                    media_type="video",
                    rel_path=f"{label}.mp4",
                    fps=25.0,
                    frame_count=20,
                ))
                session.flush()
                session.add(Track(
                    id=track_id,
                    media_id=media_id,
                    external_track_id=index + 1,
                    source="manual",
                    taxon_node_id="species-a",
                    identification_status=status,
                ))
                session.flush()
                session.add_all([
                    SpatialAnnotation(
                        id=f"ann-{label}-a",
                        media_id=media_id,
                        frame_index=1,
                        frame_ref="absolute",
                        geom_type="bbox",
                        geometry_json=geom,
                        track_id=track_id,
                        taxon_node_id="species-a",
                        source="manual",
                        identification_status="identified",
                    ),
                    SpatialAnnotation(
                        id=f"ann-{label}-b",
                        media_id=media_id,
                        frame_index=2,
                        frame_ref="absolute",
                        geom_type="bbox",
                        geometry_json=geom,
                        track_id=track_id,
                        taxon_node_id="species-b",
                        source="manual",
                        identification_status="identified",
                    ),
                ])
                if with_sample:
                    session.add(TrackSample(
                        track_id=track_id,
                        frame_index=1,
                        cx=0.25,
                        cy=0.25,
                        bbox_json=geom,
                    ))

        with session_scope(self.db_path) as session:
            for label, _status, with_sample in cases:
                with self.subTest(label=label):
                    media = session.get(MediaAsset, f"media-{label}")
                    stats = compute_session_stats(session, media)
                    timeline = list(iter_session_timeline_rows(session, media))
                    self.assertEqual(stats["species_count"], 0)
                    self.assertEqual(stats["max_per_species"], [])
                    self.assertEqual(len(timeline), 1 if with_sample else 2)
                    self.assertTrue(all(
                        [row[rank] for rank in ("family", "genus", "species")]
                        == ["NA", "NA", "NA"]
                        for row in timeline
                    ))


if __name__ == "__main__":
    unittest.main()
