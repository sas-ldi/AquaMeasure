"""L'offset fige de la session prime sur le `sync_frames.npy` courant.

Le decalage de synchro change des qu'une nouvelle synchro est faite : relire
les lignes historiques avec le fichier du jour les ferait pointer sur une autre
image. La session grave son offset a l'activation.
"""

from __future__ import annotations

import unittest
import uuid

from tests.helpers import TempDbCase, set_camera_params_env, write_sync_frames

from src.annodb import sessions as sessions_mod
from src.annodb.connection import init_db, session_scope
from src.annodb.models import MediaAsset, Project, SpatialAnnotation
from src.annodb.session_stats import _frame_offset, iter_session_timeline_rows

# Synchro du jour (sync_frames.npy) et synchro d'origine de la session.
CURRENT_SYNC = 100
FROZEN_OFFSET = 2440
LEGACY_FRAME = 1500


class FrozenOffsetTest(TempDbCase):
    def setUp(self):
        super().setUp()
        init_db(self.db_path, seed=False)
        cam = self.tmp_path / "camera_parameters"
        write_sync_frames(cam, CURRENT_SYNC, CURRENT_SYNC + 12)
        set_camera_params_env(self, cam)

        self.media_id = str(uuid.uuid4())
        with session_scope(self.db_path) as db:
            proj = Project(id=str(uuid.uuid4()), name="test")
            db.add(proj)
            db.flush()
            db.add(MediaAsset(
                id=self.media_id, project_id=proj.id, media_type="video",
                rel_path="clip.mp4", width=1920, height=1080, fps=30.0,
                frame_count=9000,
            ))
            db.flush()
            db.add(SpatialAnnotation(
                id="ann-legacy", media_id=self.media_id, frame_index=LEGACY_FRAME,
                frame_ref="timeline_legacy", geom_type="bbox", geometry_json="{}",
            ))

    def _attach(self, offset: int | None) -> None:
        with session_scope(self.db_path) as db:
            session_id = sessions_mod.create_session(
                db, name="S", site="Site", session_date="2026-08-20",
            ).id
            sessions_mod.attach_media_pair(
                db, session_id, left_media_id=self.media_id, frame_offset=offset,
            )

    def test_sans_session_l_offset_courant_s_applique(self):
        with session_scope(self.db_path) as db:
            self.assertEqual(_frame_offset(db, self.media_id), CURRENT_SYNC)

    def test_l_offset_fige_de_la_session_prime(self):
        self._attach(FROZEN_OFFSET)
        with session_scope(self.db_path) as db:
            self.assertEqual(_frame_offset(db, self.media_id), FROZEN_OFFSET)

    def test_repli_sur_sync_frames_quand_la_session_n_a_pas_fige(self):
        self._attach(None)
        with session_scope(self.db_path) as db:
            self.assertEqual(_frame_offset(db, self.media_id), CURRENT_SYNC)

    def test_conversion_legacy_utilise_l_offset_fige(self):
        self._attach(FROZEN_OFFSET)
        with session_scope(self.db_path) as db:
            media = db.get(MediaAsset, self.media_id)
            rows = list(iter_session_timeline_rows(db, media))
        self.assertEqual(len(rows), 1)
        # 1500 + 2440 (session) et non 1500 + 100 (sync_frames.npy du jour).
        self.assertEqual(rows[0]["frame_index"], LEGACY_FRAME + FROZEN_OFFSET)
        self.assertEqual(rows[0]["frame_ref"], "timeline_legacy")

    def test_export_utilise_l_offset_fige(self):
        from src.annodb.export_media import collect_export_frames

        self._attach(FROZEN_OFFSET)
        with session_scope(self.db_path) as db:
            frames = collect_export_frames(db)
        self.assertEqual(len(frames), 1)
        self.assertEqual(frames[0].frame_index, LEGACY_FRAME + FROZEN_OFFSET)


if __name__ == "__main__":
    unittest.main()
