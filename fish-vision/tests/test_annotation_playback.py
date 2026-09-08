"""Relecture légère des pistes et pictogrammes enregistrés."""

from __future__ import annotations

import sys
import uuid
from pathlib import Path

from tests.helpers import TempDbCase

REPO_ROOT = Path(__file__).resolve().parents[2]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

import fish_annotate  # noqa: E402

from src.annodb.behavior_flags import toggle_flag  # noqa: E402
from src.annodb.connection import init_db, session_scope  # noqa: E402
from src.annodb.event_types import create_event_type  # noqa: E402
from src.annodb.models import (  # noqa: E402
    MediaAsset,
    Project,
    SpatialAnnotation,
    Track,
)
from src.annodb.tracks import add_track_sample  # noqa: E402


class AnnotationPlaybackTest(TempDbCase):
    def setUp(self):
        super().setUp()
        init_db(self.db_path, seed=False)
        self.media_id = "media-playback"
        self.track_id = "track-playback"
        self.annotation_id = "annotation-playback"
        with session_scope(self.db_path) as db:
            project = Project(id=str(uuid.uuid4()), name="madagascar_measure")
            db.add(project)
            db.flush()
            db.add(MediaAsset(
                id=self.media_id,
                project_id=project.id,
                media_type="video",
                rel_path="clip.mp4",
                width=640,
                height=480,
                fps=25.0,
                frame_count=20,
            ))
            db.add(Track(
                id=self.track_id,
                media_id=self.media_id,
                external_track_id=7,
                source="bytetrack",
            ))
            db.flush()
            add_track_sample(
                db,
                track_id=self.track_id,
                frame_index=12,
                cx=0.3,
                cy=0.4,
                bbox=(100.0, 120.0, 220.0, 240.0),
            )
            flash = create_event_type(
                db, label="Flash", scope="instant", symbol="✚", color="#fb7185",
            )
            db.add(SpatialAnnotation(
                id=self.annotation_id,
                media_id=self.media_id,
                frame_index=12,
                frame_ref="absolute",
                geom_type="bbox",
                geometry_json=(
                    '{"x_min": 100, "y_min": 120, "x_max": 220, '
                    '"y_max": 240, "units": "pixels", '
                    '"ref_width": 640, "ref_height": 480}'
                ),
                source="manual",
                track_id=self.track_id,
            ))
            db.flush()
            toggle_flag(
                db, annotation_id=self.annotation_id, event_type=flash.key,
            )

    def test_overlay_groupe_contient_la_piste_et_le_picto(self):
        payload = fish_annotate.list_video_annotation_overlays(
            media_id=self.media_id,
        )

        self.assertEqual(len(payload["trackSamples"]), 1)
        sample = payload["trackSamples"][0]
        self.assertEqual(sample["frameIndexAbs"], 12)
        self.assertEqual(sample["trackDbId"], self.track_id)
        self.assertEqual((sample["x1"], sample["y1"]), (100.0, 120.0))

        self.assertEqual(len(payload["instantAnnotations"]), 1)
        marker = payload["instantAnnotations"][0]
        self.assertEqual(marker["frameIndexAbs"], 12)
        self.assertEqual(marker["behavior"]["symbol"], "✚")
        self.assertEqual(marker["behavior"]["color"], "#fb7185")
