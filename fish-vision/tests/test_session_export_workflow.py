"""Parcours utilisateur : une session multi-prises devient un paquet autonome."""

from __future__ import annotations

import json
import shutil
import subprocess
import sys
import uuid
from pathlib import Path

from tests.helpers import TempDbCase, write_test_image

from src.annodb import sessions as sessions_mod
from src.annodb.connection import init_db, session_scope
from src.annodb.models import (
    MediaAsset,
    Project,
    SpatialAnnotation,
    TaxonNode,
    Track,
    TrackSample,
    TemporalEvent,
)
from src.annodb.spatial import make_bbox_geometry
from src.annodb.storage_config import set_data_root


ROOT = Path(__file__).resolve().parents[1]
REPO_ROOT = ROOT.parent


def _json_result(output: str) -> dict:
    decoder = json.JSONDecoder()
    for index, char in enumerate(output):
        if char != "{":
            continue
        try:
            value, _ = decoder.raw_decode(output[index:])
        except json.JSONDecodeError:
            continue
        if isinstance(value, dict) and value.get("output_path"):
            return value
    raise AssertionError(output)


class SessionExportFixture(TempDbCase):
    def setUp(self):
        super().setUp()
        self.data_root = self.tmp_path / "data-root"
        set_data_root(self.data_root)
        init_db(self.db_path, seed=False)
        self.media_ids = [f"pair-media-{index}" for index in range(4)]
        self.paths: list[Path] = []
        with session_scope(self.db_path) as db:
            project = Project(id=str(uuid.uuid4()), name="session-export-test")
            db.add(project)
            db.add(TaxonNode(
                id="species-test", rank="species",
                scientific_name="Aqua testensis",
            ))
            db.flush()
            for index, media_id in enumerate(self.media_ids):
                path = self.tmp_path / "media" / f"prise_{index}.jpg"
                write_test_image(path)
                self.paths.append(path)
                db.add(MediaAsset(
                    id=media_id, project_id=project.id, media_type="image",
                    rel_path=str(path), width=64, height=48,
                ))
            outside = self.tmp_path / "media" / "hors_session.jpg"
            write_test_image(outside)
            db.add(MediaAsset(
                id="outside-media", project_id=project.id, media_type="image",
                rel_path=str(outside), width=64, height=48,
            ))
            db.flush()
            self.session_id = sessions_mod.create_session(
                db, name="Journée récif", site="Récif Nord",
                session_date="2026-09-03",
            ).id
            sessions_mod.attach_media_pair(
                db, self.session_id, left_media_id=self.media_ids[0],
                right_media_id=self.media_ids[1], frame_offset=4,
            )
            sessions_mod.attach_media_pair(
                db, self.session_id, left_media_id=self.media_ids[2],
                right_media_id=self.media_ids[3], frame_offset=-2,
            )
            geometry = make_bbox_geometry(
                10, 8, 40, 32, space="raw", ref_width=64, ref_height=48,
            )
            for ann_id, media_id in (
                ("session-ann-1", self.media_ids[0]),
                ("session-ann-2", self.media_ids[2]),
                ("outside-ann", "outside-media"),
            ):
                db.add(SpatialAnnotation(
                    id=ann_id, media_id=media_id, frame_index=0,
                    frame_ref="absolute", geom_type="bbox",
                    geometry_json=json.dumps(geometry),
                    taxon_node_id="species-test", source="validated",
                    identification_status="identified",
                ))

    def _run(self, output: Path, *extra: str) -> subprocess.CompletedProcess:
        return subprocess.run(
            [
                sys.executable, str(ROOT / "scripts" / "export_session.py"),
                "--session", self.session_id,
                "--db", str(self.db_path),
                "--out", str(output),
                *extra,
            ],
            cwd=ROOT,
            capture_output=True,
            text=True,
        )

class SessionExportWorkflowTest(SessionExportFixture):
    def test_un_seul_paquet_contient_les_deux_prises_et_pas_le_reste(self):
        process = self._run(self.tmp_path / "exports")
        self.assertEqual(process.returncode, 0, process.stdout + process.stderr)
        result = _json_result(process.stdout)
        package = Path(result["output_path"])
        summary = json.loads((package / "session.json").read_text(encoding="utf-8"))
        self.assertEqual(summary["session"]["pair_count"], 2)
        self.assertEqual(summary["session"]["observation_count"], 2)
        self.assertEqual(len(summary["videos_available"]), 4)
        manifest_path = next((package / "coco").rglob("manifest.json"))
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        self.assertEqual(
            set(manifest["source"]["filter"]["media_ids"]),
            set(self.media_ids),
        )
        coco_path = next((package / "coco").rglob("instances_species.json"))
        coco = json.loads(coco_path.read_text(encoding="utf-8"))
        self.assertEqual(len(coco["images"]), 2)
        self.assertEqual(len(coco["annotations"]), 2)
        self.assertNotIn("outside-media", json.dumps(coco))

    def test_video_manquante_bloque_puis_le_choix_explicite_continue(self):
        self.paths[2].unlink()
        output = self.tmp_path / "exports-missing"
        refused = self._run(output)
        self.assertNotEqual(refused.returncode, 0)
        self.assertIn("manquante", (refused.stdout + refused.stderr).lower())
        self.assertFalse(output.exists())

        accepted = self._run(output, "--allow-missing")
        self.assertEqual(accepted.returncode, 0, accepted.stdout + accepted.stderr)
        package = Path(_json_result(accepted.stdout)["output_path"])
        summary = json.loads((package / "session.json").read_text(encoding="utf-8"))
        self.assertEqual(len(summary["videos_skipped"]), 1)
        manifest_path = next((package / "coco").rglob("manifest.json"))
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        self.assertNotIn(
            self.media_ids[2], manifest["source"]["filter"]["media_ids"],
        )

    def test_repointer_une_video_renommee_verifie_son_empreinte(self):
        if str(REPO_ROOT) not in sys.path:
            sys.path.insert(0, str(REPO_ROOT))
        import fish_annotate
        from src.annodb.projects import sha256_for_path

        original = self.paths[0]
        renamed = original.with_name("prise_renommee.jpg")
        shutil.copy2(original, renamed)
        with session_scope(self.db_path) as db:
            media = db.get(MediaAsset, self.media_ids[0])
            media.sha256 = sha256_for_path(db, original, project_id=media.project_id)
        original.unlink()

        result = fish_annotate.repoint_media(self.media_ids[0], str(renamed))
        self.assertEqual(Path(result["path"]), renamed.resolve())
        with session_scope(self.db_path) as db:
            self.assertEqual(
                Path(db.get(MediaAsset, self.media_ids[0]).rel_path),
                renamed.resolve(),
            )

    def test_csv_regroupe_les_observations_meme_si_une_video_a_disparu(self):
        self.paths[2].unlink()
        process = self._run(self.tmp_path / "exports-csv", "--format", "csv")
        self.assertEqual(process.returncode, 0, process.stdout + process.stderr)
        package = Path(_json_result(process.stdout)["output_path"])
        csv_text = (package / "session.csv").read_text(encoding="utf-8-sig")
        self.assertIn("session_name", csv_text)
        self.assertIn("Journée récif", csv_text)
        self.assertIn("session-ann-1", csv_text)
        self.assertIn("session-ann-2", csv_text)
        self.assertNotIn("outside-ann", csv_text)
        summary = json.loads((package / "session.json").read_text(encoding="utf-8"))
        self.assertEqual(summary["export_type"], "csv")
        self.assertEqual(summary["csv_row_count"], 2)
        self.assertEqual(summary["videos_skipped"], [])

    def test_tracking_produit_coco_vid_et_motchallenge(self):
        import cv2
        import numpy as np

        video_path = self.tmp_path / "media" / "tracking.avi"
        writer = cv2.VideoWriter(
            str(video_path), cv2.VideoWriter_fourcc(*"MJPG"), 10.0, (64, 48),
        )
        if not writer.isOpened():
            self.skipTest("cv2.VideoWriter indisponible")
        writer.write(np.zeros((48, 64, 3), dtype=np.uint8))
        writer.release()
        with session_scope(self.db_path) as db:
            media = db.get(MediaAsset, self.media_ids[0])
            media.rel_path = str(video_path)
            media.media_type = "video"
            media.fps = 10.0
            media.frame_count = 1
            track = Track(
                id="track-session", media_id=self.media_ids[0],
                external_track_id=7, taxon_node_id="species-test",
                first_frame=0, last_frame=0, source="manual",
            )
            db.add(track)
            db.add(TrackSample(
                track_id=track.id, frame_index=0, cx=25.0, cy=20.0,
                bbox_json=json.dumps({
                    "x_min": 10, "y_min": 8, "x_max": 40, "y_max": 32,
                }),
            ))
            db.get(SpatialAnnotation, "session-ann-1").track_id = track.id
            db.add(TemporalEvent(id="action-session", track_id=track.id,
                event_type="bite", frame_start=0, frame_end=0,
                frame_ref="absolute", source="manual"))

        process = self._run(
            self.tmp_path / "exports-tracking", "--format", "tracking",
        )
        self.assertEqual(process.returncode, 0, process.stdout + process.stderr)
        package = Path(_json_result(process.stdout)["output_path"])
        self.assertTrue(any(package.rglob("coco_vid.json")))
        self.assertTrue(any(package.rglob("gt.txt")))
        summary = json.loads((package / "session.json").read_text(encoding="utf-8"))
        self.assertEqual(summary["export_type"], "tracking")
        self.assertIsNotNone(summary["tracking_directory"])
        self.assertIsNone(summary["coco_directory"])
        self.assertEqual(summary["tracking_event_count"], 1)
        self.assertEqual(summary["tracking_event_images_missing"], [])
        payload = json.loads(next(package.rglob("coco_vid.json")).read_text(encoding="utf-8"))
        self.assertEqual(payload["events"][0]["id"], "action-session")
        self.assertEqual(payload["events"][0]["frame_index"], 0)
        self.assertTrue(any(package.rglob("track_events.jsonl")))

    def test_crop_fishial_est_conserve_et_compte_separement(self):
        if str(REPO_ROOT) not in sys.path:
            sys.path.insert(0, str(REPO_ROOT))
        import fish_annotate
        import fish_db_stats

        crop_path = Path(fish_annotate.save_annotation_crop(
            "session-ann-1", self.paths[0].read_bytes(),
        ))
        self.assertTrue(crop_path.is_file())
        self.assertTrue(crop_path.is_relative_to(self.data_root))
        with session_scope(self.db_path) as db:
            self.assertEqual(
                Path(db.get(SpatialAnnotation, "session-ann-1").crop_path),
                crop_path,
            )
        rows = fish_db_stats.list_species_summary(project="session-export-test")
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["crop_count"], 1)
        self.assertEqual(rows[0]["annotation_count"], 3)


if __name__ == "__main__":
    import unittest

    unittest.main()
