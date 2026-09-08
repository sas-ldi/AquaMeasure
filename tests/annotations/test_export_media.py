"""Matérialisation d'une frame d'export : index absolu, rectification, espace déclaré."""

from __future__ import annotations

import unittest
import uuid
from pathlib import Path

from annotations.helpers import (
    TempDbCase,
    set_camera_params_env,
    write_fake_calibration,
    write_sync_frames,
    write_test_image,
)

from src.annodb import rectify
from src.annodb.connection import init_db, session_scope
from src.annodb.export_media import (
    REASON_FICHIER_INTROUVABLE,
    REASON_OFFSET_INDETERMINABLE,
    ExportExclusions,
    ExportFrame,
    collect_export_frames,
    materialize_frame_image,
)
from src.annodb.models import MediaAsset, Project, SpatialAnnotation
from src.annodb.spatial import make_bbox_geometry

WIDTH, HEIGHT = 64, 48


def _write_video(path: Path, frames: int = 12, width: int = WIDTH, height: int = HEIGHT):
    """Petite vidéo dont chaque frame porte une valeur reconnaissable."""
    import cv2
    import numpy as np

    writer = cv2.VideoWriter(
        str(path), cv2.VideoWriter_fourcc(*"MJPG"), 10.0, (width, height),
    )
    if not writer.isOpened():
        return False
    for i in range(frames):
        frame = np.full((height, width, 3), (i * 20) % 256, dtype=np.uint8)
        frame[height // 3: 2 * height // 3, width // 3: 2 * width // 3] = 255
        writer.write(frame)
    writer.release()
    return path.is_file()


class ExportFrameCollectionTest(TempDbCase):
    """Regroupement des annotations : tout ressort en index absolu."""

    def setUp(self):
        super().setUp()
        init_db(self.db_path, seed=False)
        self.media_id = str(uuid.uuid4())
        with session_scope(self.db_path) as session:
            proj = Project(id=str(uuid.uuid4()), name="test")
            session.add(proj)
            session.flush()
            session.add(MediaAsset(
                id=self.media_id, project_id=proj.id, media_type="video",
                rel_path=str(self.tmp_path / "absente.mp4"),
                width=WIDTH, height=HEIGHT,
            ))
            session.flush()
            geom = make_bbox_geometry(
                1, 2, 10, 12, space="stereo_rectified_left",
                ref_width=WIDTH, ref_height=HEIGHT,
            )
            for frame_index, frame_ref, ann_id in (
                (3940, "absolute", "ann-absolue"),
                (1500, "timeline_legacy", "ann-historique"),
            ):
                session.add(SpatialAnnotation(
                    id=ann_id, media_id=self.media_id, frame_index=frame_index,
                    frame_ref=frame_ref, geom_type="bbox",
                    geometry_json=__import__("json").dumps(geom),
                ))

    def test_ligne_historique_convertie_rejoint_la_ligne_absolue(self):
        cam = self.tmp_path / "camera_parameters"
        write_sync_frames(cam, 2440, 2552)
        set_camera_params_env(self, cam)

        exclusions = ExportExclusions()
        with session_scope(self.db_path) as session:
            frames = collect_export_frames(session, exclusions=exclusions)

        self.assertEqual(len(frames), 1, "1500 + 2440 doit tomber sur 3940")
        frame = frames[0]
        self.assertEqual(frame.frame_index, 3940)
        self.assertEqual(len(frame.annotations), 2)
        self.assertEqual(len(exclusions), 0)

    def test_ligne_historique_exclue_si_offset_inconnu(self):
        cam = self.tmp_path / "camera_parameters"
        cam.mkdir(parents=True, exist_ok=True)
        set_camera_params_env(self, cam)

        exclusions = ExportExclusions()
        with session_scope(self.db_path) as session:
            frames = collect_export_frames(session, exclusions=exclusions)

        self.assertEqual(len(frames), 1)
        self.assertEqual(frames[0].frame_index, 3940)
        self.assertEqual([a.id for a in frames[0].annotations], ["ann-absolue"])

        rows = exclusions.rows()
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["annotation_id"], "ann-historique")
        self.assertEqual(rows[0]["reason"], REASON_OFFSET_INDETERMINABLE)
        self.assertIn("1 annotations exclues", exclusions.summary())


class MaterializeFrameTest(TempDbCase):
    """L'image écrite doit être celle sur laquelle les boîtes ont été tracées."""

    def setUp(self):
        super().setUp()
        init_db(self.db_path, seed=False)
        self.video_path = self.tmp_path / "clip.avi"
        self.has_video = _write_video(self.video_path)
        self.media_id = str(uuid.uuid4())
        with session_scope(self.db_path) as session:
            proj = Project(id=str(uuid.uuid4()), name="test")
            session.add(proj)
            session.flush()
            session.add(MediaAsset(
                id=self.media_id, project_id=proj.id, media_type="video",
                rel_path=str(self.video_path), width=WIDTH, height=HEIGHT,
            ))

    def _frame(self, index: int = 5) -> ExportFrame:
        return ExportFrame(
            media_id=self.media_id, frame_index=index, media_type="video",
        )

    def test_sans_calibration_image_brute_mais_declaree(self):
        if not self.has_video:
            self.skipTest("cv2.VideoWriter indisponible dans cet environnement")
        cam = self.tmp_path / "camera_parameters"
        cam.mkdir(parents=True, exist_ok=True)
        set_camera_params_env(self, cam)

        ef = self._frame()
        dest = self.tmp_path / "out" / "frame.jpg"
        with session_scope(self.db_path) as session:
            dims = materialize_frame_image(session, ef, dest, transform=None)

        self.assertEqual(dims, (WIDTH, HEIGHT))
        self.assertTrue(dest.is_file())
        self.assertEqual(ef.image_space, rectify.IMAGE_SPACE_RAW)
        self.assertEqual(ef.meta()["image_space"], "raw")

    def test_avec_calibration_image_rectifiee_et_provenance(self):
        if not self.has_video:
            self.skipTest("cv2.VideoWriter indisponible dans cet environnement")
        cam = self.tmp_path / "camera_parameters"
        write_fake_calibration(cam, width=WIDTH, height=HEIGHT, profile="classic")
        set_camera_params_env(self, cam)

        rectifier = rectify.load_left_rectifier()
        self.assertIsNotNone(rectifier)

        ef = self._frame()
        dest = self.tmp_path / "out" / "frame_rect.jpg"
        with session_scope(self.db_path) as session:
            dims = materialize_frame_image(
                session, ef, dest, transform=rectifier,
            )

        self.assertEqual(dims, (WIDTH, HEIGHT))
        self.assertEqual(ef.image_space, rectify.IMAGE_SPACE_RECTIFIED_LEFT)
        meta = ef.meta()
        self.assertEqual(meta["image_space"], "stereo_rectified_left")
        self.assertEqual(meta["calibration"]["profile_name"], "classic")
        self.assertEqual(len(meta["calibration"]["calibration_sha256"]), 64)

    def test_fichier_absent_signale(self):
        with session_scope(self.db_path) as session:
            media = session.get(MediaAsset, self.media_id)
            media.rel_path = str(self.tmp_path / "jamais_ecrit.avi")
            session.flush()

            ef = self._frame()
            ef.annotations = [SpatialAnnotation(
                id="ann-perdue", media_id=self.media_id, frame_index=5,
                geom_type="bbox", geometry_json="{}",
            )]
            exclusions = ExportExclusions()
            dims = materialize_frame_image(
                session, ef, self.tmp_path / "out" / "x.jpg", exclusions=exclusions,
            )

        self.assertIsNone(dims)
        self.assertEqual(len(exclusions), 1)
        self.assertEqual(exclusions.rows()[0]["reason"], REASON_FICHIER_INTROUVABLE)
        self.assertEqual(exclusions.rows()[0]["annotation_id"], "ann-perdue")


class ImageMediaTest(TempDbCase):
    """Une photo importée n'appartient pas au banc stéréo : jamais rectifiée."""

    def test_photo_reste_en_espace_brut(self):
        init_db(self.db_path, seed=False)
        img_path = self.tmp_path / "photo.jpg"
        write_test_image(img_path, WIDTH, HEIGHT)
        media_id = str(uuid.uuid4())
        with session_scope(self.db_path) as session:
            proj = Project(id=str(uuid.uuid4()), name="test")
            session.add(proj)
            session.flush()
            session.add(MediaAsset(
                id=media_id, project_id=proj.id, media_type="image",
                rel_path=str(img_path), width=WIDTH, height=HEIGHT,
            ))

        cam = self.tmp_path / "camera_parameters"
        write_fake_calibration(cam, width=WIDTH, height=HEIGHT, profile="classic")
        set_camera_params_env(self, cam)

        ef = ExportFrame(media_id=media_id, frame_index=0, media_type="image")
        with session_scope(self.db_path) as session:
            dims = materialize_frame_image(
                session, ef, self.tmp_path / "out" / "photo.jpg",
                transform=rectify.load_left_rectifier(),
            )
        self.assertEqual(dims, (WIDTH, HEIGHT))
        self.assertEqual(ef.image_space, rectify.IMAGE_SPACE_RAW)


if __name__ == "__main__":
    unittest.main()
