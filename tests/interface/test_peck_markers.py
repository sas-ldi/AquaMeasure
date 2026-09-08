"""Marqueurs ponctuels (bouchees) : base, controleur et export AVA.

Module sans moteur QML — un second QQmlApplicationEngine dans la suite la rend
instable. Les gestes d'interface sont couverts dans
`test_measure_qml_runtime.py`, qui a deja son harnais.
"""

from __future__ import annotations

import csv
import os
import sys
import tempfile
import unittest
from pathlib import Path

from PySide6.QtCore import QCoreApplication, QObject, Signal

APP_ROOT = Path(__file__).resolve().parents[2] / "src" / "interface"
REPO_ROOT = APP_ROOT.parent
FV_ROOT = REPO_ROOT / "annotations"
for candidate in (APP_ROOT, REPO_ROOT, FV_ROOT):
    if str(candidate) not in sys.path:
        sys.path.insert(0, str(candidate))

# Meme montage du namespace ``src`` que main.py, sans demarrer l'interface.
import src  # noqa: E402

for namespace_part in (APP_ROOT / "src", FV_ROOT / "src"):
    if str(namespace_part) not in src.__path__:
        src.__path__.append(str(namespace_part))

from src.annodb.connection import init_db, session_scope  # noqa: E402
from src.annodb.models import TemporalEvent  # noqa: E402
from src.annodb.tracks import (  # noqa: E402
    add_track_sample,
    get_or_create_track,
    refresh_track_bounds,
)
from src.controllers.peck_controller import PeckController  # noqa: E402
from src.imaging.rect_mapping import RectMapping  # noqa: E402

# La timeline du harnais demarre a la frame absolue 2 : les bornes ecrites
# doivent rester des index ABSOLUS, pas des index d'affichage.
TIMELINE_OFFSET = 2


class _MeasureStub(QObject):
    frameIndexChanged = Signal()
    leftVideoChanged = Signal()

    def __init__(self, media: Path, frame_count: int = 40):
        super().__init__()
        self.leftVideo = str(media)
        self.frameIndex = 0
        self.frameCount = frame_count
        self.playing = False
        self.toggle_calls = 0
        # Conversion rectifie -> image affichee. Par defaut l'identite : sans
        # calibration, la vue montre la meme image a l'arret et en lecture.
        self.mapping = RectMapping.identity()
        self.mapping_token = 0
        self.remap_active = False

    def leftAbsFrameAt(self, index: int) -> int:
        return TIMELINE_OFFSET + max(0, int(index))

    def alignedIndexFromLeftAbs(self, abs_frame: int) -> int:
        return max(0, min(self.frameCount - 1, int(abs_frame) - TIMELINE_OFFSET))

    def togglePlay(self):
        self.toggle_calls += 1
        self.playing = not self.playing

    # ── Surface lue par PeckController pour recaler l'overlay ──────────

    def rect_mapping(self, left: bool = True) -> RectMapping:
        return self.mapping

    def rect_mapping_token(self) -> int:
        return self.mapping_token

    def overlay_remap_active(self) -> bool:
        return bool(self.remap_active) and not self.mapping.is_identity

    def set_mapping(self, mapping: RectMapping) -> None:
        self.mapping = mapping
        self.mapping_token += 1

    def seek(self, abs_frame: int) -> None:
        """Deplace la timeline comme le fait le lecteur pendant la lecture."""
        self.frameIndex = self.alignedIndexFromLeftAbs(abs_frame)
        self.frameIndexChanged.emit()


class _DataStub:
    def __init__(self, media_id: str = ""):
        self.mediaId = media_id
        self.reloads = 0

    def loadEventTypes(self):
        self.reloads += 1


class _FishStub:
    def __init__(self):
        self.invalidations = 0
        self.focused: list[tuple] = []
        self.pinned: list[str] = []

    def invalidateGrazingCache(self):
        self.invalidations += 1

    def focusAnnotationBox(self, track_id, frame, x1, y1, x2, y2, label):
        self.focused.append((track_id, frame, x1, y1, x2, y2, label))

    def pinTrackTrail(self, track_id):
        self.pinned.append(str(track_id))


class PeckMarkerTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QCoreApplication.instance() or QCoreApplication([])

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory(prefix="peck_markers_")
        self.addCleanup(self._tmp.cleanup)
        self.tmp_path = Path(self._tmp.name)
        self._set_env("FISH_VISION_DB", str(self.tmp_path / "annotations.db"))
        self._set_env(
            "AQUAMEASURE_STORAGE_CONFIG", str(self.tmp_path / "storage.json")
        )
        # Sans cette isolation, choisir un annotateur écrirait l'identité
        # courante dans src/annotations/data/app_settings.json du dépôt.
        self._set_env(
            "FISH_VISION_SETTINGS", str(self.tmp_path / "app_settings.json")
        )

        from src.annodb import storage_config

        storage_config.invalidate_cache()
        self.addCleanup(storage_config.invalidate_cache)
        init_db(self.tmp_path / "annotations.db", seed=True)

        import cv2
        import numpy as np

        self.media = self.tmp_path / "sequence.avi"
        writer = cv2.VideoWriter(
            str(self.media), cv2.VideoWriter_fourcc(*"MJPG"), 10.0, (100, 80),
        )
        self.assertTrue(writer.isOpened())
        for index in range(40):
            frame = np.zeros((80, 100, 3), dtype=np.uint8)
            frame[:, :, 0] = index * 5
            writer.write(frame)
        writer.release()

        import fish_annotate as fa

        self.fa = fa
        self.media_id = fa.resolve_media_id(str(self.media), create=True)
        self.assertTrue(self.media_id)

        # Une piste suivie de la frame absolue 10 a 30, une position par frame.
        with session_scope() as session:
            track = get_or_create_track(
                session, media_id=self.media_id, external_track_id=7,
            )
            self.track_id = track.id
            for frame in range(10, 31):
                add_track_sample(
                    session,
                    track_id=track.id,
                    frame_index=frame,
                    cx=50.0,
                    cy=40.0,
                    bbox=(20.0, 20.0, 80.0, 60.0),
                )
            refresh_track_bounds(session, track.id)

    def tearDown(self):
        from src.annodb import connection

        if connection._engine is not None:
            connection._engine.dispose()
            connection._engine = None
            connection._SessionLocal = None
        connection._migrated_paths.clear()
        super().tearDown()

    def _set_env(self, name: str, value: str):
        previous = os.environ.get(name)
        os.environ[name] = value

        def restore():
            if previous is None:
                os.environ.pop(name, None)
            else:
                os.environ[name] = previous

        self.addCleanup(restore)

    def _make_point_type(self, label: str = "Bouchée", scope: str = "point") -> dict:
        return self.fa.create_behavior_type(label, scope, "●")

    def _controller(self):
        measure = _MeasureStub(self.media)
        data = _DataStub(self.media_id)
        fish = _FishStub()
        pecks = PeckController(measure, data, fish)
        pecks.refresh()
        return pecks, measure, data, fish

    # ── Catalogue ──────────────────────────────────────────────────────

    def test_registry_counts_events_on_own_track_including_legacy_types(self):
        from src.annodb.events import add_temporal_event
        from src.annodb.models import EventType
        from sqlalchemy import select
        kind = self._make_point_type()
        observation = self.fa.add_observation(
            str(self.media), 10, {"x1": 20, "y1": 20, "x2": 80, "y2": 60},
            source="manual", track_id=self.track_id, frame_ref="absolute")
        bare = self.fa.add_observation(
            str(self.media), 10, {"x1": 1, "y1": 1, "x2": 10, "y2": 10},
            source="manual", frame_ref="absolute")
        ids = [self.fa.add_behavior_point(self.track_id, frame, event_type=kind["key"])
               for frame in (12, 18, 25)]
        self.fa.toggle_observation_behavior(observation["ann_id"], kind["key"])
        with session_scope() as session:
            other = get_or_create_track(session, media_id=self.media_id, external_track_id=8)
            add_temporal_event(session, track_id=other.id, frame_start=12,
                               frame_end=12, event_type=kind["key"])
            add_temporal_event(session, track_id=self.track_id, frame_start=11,
                               frame_end=29, event_type="legacy-interval")
            session.scalar(select(EventType).where(EventType.key == kind["key"])).is_active = False
        def rows():
            return {r["ann_id"]: r for r in self.fa.list_observations(media_path=str(self.media))}
        row = rows()[observation["ann_id"]]
        self.assertEqual({e["key"]: e["count"] for e in row["track_events"]},
                         {kind["key"]: 3, "legacy-interval": 1})
        self.assertEqual(len(row["behaviors"]), 1)
        self.assertEqual(rows()[bare["ann_id"]]["track_events"], [])
        self.fa.delete_behavior_point(ids[0])
        self.assertEqual(next(e["count"] for e in rows()[observation["ann_id"]]["track_events"]
                              if e["key"] == kind["key"]), 2)

    def test_pop_crosses_skipped_frame_and_uses_cached_raw_position(self):
        import numpy as np
        from unittest.mock import patch
        from src.imaging.rect_mapping import RectMapping
        kind = self._make_point_type()
        self.fa.add_behavior_point(self.track_id, 12, event_type=kind["key"])
        pecks, measure, _, fish = self._controller()
        pecks.selectTrack(self.track_id)
        self.assertEqual(fish.pinned[-1], self.track_id)
        yy, xx = np.indices((80, 100), dtype=np.float32)
        mapping = RectMapping(xx + 7, yy - 3)
        measure.overlay_remap_active = lambda: measure.playing
        measure.rect_mapping = lambda left=True: mapping
        measure.rect_mapping_token = lambda: 1
        pops = []
        pecks.peckPopped.connect(lambda *args: pops.append(args))
        measure.playing = True
        with patch.object(RectMapping, "map_boxes", wraps=mapping.map_boxes) as convert:
            measure.frameIndex = 13 - TIMELINE_OFFSET
            measure.frameIndexChanged.emit()
            self.assertEqual(len(pops), 1)
            self.assertEqual(pops[0][:2], (57.0, 37.0))
            for _ in range(60):
                self.assertTrue(pecks.trackBox["valid"])
            self.assertEqual(convert.call_count, 1)
        measure.playing = False
        self.assertEqual(pecks.trackBox["x1"], 20.0)

    def test_point_scope_of_the_spec_is_stored_as_the_scope_of_the_base(self):
        created = self._make_point_type()
        # Le cahier des charges dit « point », la base ecrit « instant » depuis
        # le premier jour : un seul mot doit finir en base.
        self.assertEqual(created["scope"], "instant")
        keys = [row["key"] for row in self.fa.list_point_event_types()]
        self.assertEqual(set(keys), {"bite", created["key"]})
        # Le type integre est un intervalle : il ne doit pas s'y trouver.
        self.assertNotIn("grazing", keys)

    def test_shortcut_can_be_written_and_cleared_from_the_catalogue(self):
        created = self._make_point_type()
        self.assertEqual(created["shortcut"], "")
        updated = self.fa.set_behavior_shortcut(created["id"], "b")
        self.assertEqual(updated["shortcut"], "B")
        cleared = self.fa.set_behavior_shortcut(created["id"], "")
        self.assertEqual(cleared["shortcut"], "")

    # ── Ecriture en base ───────────────────────────────────────────────

    def test_interval_writer_still_refuses_a_point_type(self):
        created = self._make_point_type()
        with self.assertRaises(ValueError):
            self.fa.add_grazing_interval(
                self.track_id, 12, 12, event_type=created["key"],
            )

    def test_point_writer_refuses_an_interval_type(self):
        with self.assertRaises(ValueError):
            self.fa.add_behavior_point(self.track_id, 12, event_type="grazing")

    def test_three_points_are_single_frame_manual_events_on_the_track(self):
        created = self._make_point_type()
        # L'auteur enregistre doit etre l'annotateur courant, jamais une valeur
        # en dur : c'est la regle deja tenue par l'intervalle de broutage.
        # create_annotator retient déjà l'identité comme annotateur courant.
        self.fa.create_annotator("Thomas Lamy")
        self.assertEqual(self.fa.current_author(), "Thomas Lamy")
        ids = [
            self.fa.add_behavior_point(
                self.track_id, frame, event_type=created["key"],
            )
            for frame in (12, 18, 25)
        ]
        self.assertEqual(len(set(ids)), 3)

        with session_scope() as session:
            rows = session.query(TemporalEvent).order_by(
                TemporalEvent.frame_start
            ).all()
            self.assertEqual(len(rows), 3)
            for row, frame in zip(rows, (12, 18, 25)):
                self.assertEqual(row.frame_start, frame)
                self.assertEqual(row.frame_end, frame)
                self.assertEqual(row.source, "manual")
                self.assertEqual(row.frame_ref, "absolute")
                self.assertEqual(row.track_id, self.track_id)
                self.assertEqual(row.event_type, created["key"])
                self.assertEqual(row.author, "Thomas Lamy")

    def test_points_and_intervals_stay_in_separate_lists(self):
        created = self._make_point_type()
        self.fa.add_grazing_interval(self.track_id, 11, 20)
        self.fa.add_behavior_point(self.track_id, 15, event_type=created["key"])
        # Une bouchee isolee, hors de tout intervalle : le chercheur doit
        # pouvoir l'enregistrer.
        self.fa.add_behavior_point(self.track_id, 28, event_type=created["key"])

        intervals = self.fa.list_grazing_intervals(str(self.media))
        self.assertEqual([row["event_type"] for row in intervals], ["grazing"])

        points = self.fa.list_behavior_points(str(self.media))
        self.assertEqual([row["frame_abs"] for row in points], [15, 28])
        self.assertEqual(points[0]["external_track_id"], 7)

    def test_removing_a_point_never_removes_an_interval(self):
        created = self._make_point_type()
        interval_id = self.fa.add_grazing_interval(self.track_id, 11, 20)
        point_id = self.fa.add_behavior_point(
            self.track_id, 15, event_type=created["key"],
        )
        with self.assertRaises(ValueError):
            self.fa.delete_behavior_point(interval_id)
        self.assertTrue(self.fa.delete_behavior_point(point_id))
        self.assertEqual(self.fa.list_behavior_points(str(self.media)), [])
        self.assertEqual(len(self.fa.list_grazing_intervals(str(self.media))), 1)

    # ── Controleur ─────────────────────────────────────────────────────

    def test_controller_marks_by_click_and_by_shortcut_then_removes(self):
        created = self._make_point_type()
        self.fa.set_behavior_shortcut(created["id"], "b")
        pecks, measure, _data, fish = self._controller()

        self.assertTrue(pecks.hasPointType)
        self.assertEqual(pecks.trackCount, 1)
        self.assertFalse(pecks.armed)

        pecks.selectTrack(self.track_id)
        self.assertTrue(pecks.armed)
        self.assertEqual(pecks.selectedFirstFrame, 10)
        self.assertEqual(pecks.selectedLastFrame, 30)

        # 1. clavier
        measure.frameIndex = 10  # frame absolue 12
        self.assertTrue(pecks.handleShortcut("b"))
        # 2. clic dans la bbox de la piste
        measure.frameIndex = 16  # frame absolue 18
        measure.frameIndexChanged.emit()
        self.assertTrue(pecks.markFromOverlay(50.0, 40.0))
        # 3. bouton du panneau
        measure.frameIndex = 23  # frame absolue 25
        measure.frameIndexChanged.emit()
        self.assertTrue(pecks.markAtCurrentFrame())

        self.assertEqual(pecks.markerCount, 3)
        self.assertEqual([m["frameAbs"] for m in pecks.markers], [12, 18, 25])
        # Index timeline pour la barre de marqueurs, decale de l'offset.
        self.assertEqual([m["frame"] for m in pecks.markers], [10, 16, 23])
        self.assertEqual(pecks.tracks[0]["markerCount"], 3)
        self.assertEqual(pecks.videoMarkerCount, 3)
        self.assertGreaterEqual(fish.invalidations, 3)

        # Deux fois la meme image : refuse, jamais un doublon silencieux.
        self.assertFalse(pecks.markAtCurrentFrame())
        self.assertEqual(pecks.markerCount, 3)

        # Retrait au clavier (Maj) sur l'image affichee. La touche Maj arrive
        # en parametre : selon la disposition clavier et l'outil qui envoie
        # l'evenement, `event.text` reste minuscule, la casse ne prouve rien.
        self.assertTrue(pecks.handleShortcut("B", True))
        self.assertEqual(pecks.markerCount, 2)
        self.assertEqual([m["frameAbs"] for m in pecks.markers], [12, 18])

    def test_a_click_outside_the_track_box_is_not_a_marker(self):
        created = self._make_point_type()
        pecks, measure, _data, _fish = self._controller()
        pecks.selectTrack(self.track_id)
        measure.frameIndex = 10
        measure.frameIndexChanged.emit()

        self.assertTrue(pecks.trackBox["valid"])
        self.assertFalse(pecks.markFromOverlay(5.0, 5.0))
        self.assertEqual(pecks.markerCount, 0)
        self.assertTrue(pecks.markFromOverlay(50.0, 40.0))
        self.assertEqual(pecks.markerCount, 1)
        self.assertEqual(created["scope"], "instant")

    def test_no_track_box_outside_the_followed_range(self):
        self._make_point_type()
        pecks, measure, _data, _fish = self._controller()
        pecks.selectTrack(self.track_id)

        measure.frameIndex = 35  # frame absolue 37, hors de la piste
        measure.frameIndexChanged.emit()
        self.assertFalse(pecks.trackBox.get("valid"))
        self.assertFalse(pecks.markFromOverlay(50.0, 40.0))

    def test_selecting_a_track_seeks_onto_it_and_frames_the_fish(self):
        self._make_point_type()
        pecks, measure, _data, fish = self._controller()
        measure.frameIndex = 0

        pecks.selectTrack(self.track_id)
        # La plage commence a la frame absolue 10, soit l'index timeline 8.
        self.assertEqual(measure.frameIndex, 8)
        self.assertEqual(pecks.rangeStart, 8)
        self.assertEqual(pecks.rangeEnd, 28)
        self.assertTrue(fish.focused)

        pecks.replayTrack()
        self.assertEqual(measure.frameIndex, 8)
        self.assertTrue(measure.playing)

    def test_marker_navigation_walks_the_track(self):
        created = self._make_point_type()
        for frame in (12, 18, 25):
            self.fa.add_behavior_point(
                self.track_id, frame, event_type=created["key"],
            )
        pecks, measure, _data, _fish = self._controller()
        pecks.selectTrack(self.track_id)

        measure.frameIndex = 8  # frame absolue 10
        pecks.stepToMarker(1)
        self.assertEqual(measure.frameIndex, 10)  # frame absolue 12
        pecks.stepToMarker(1)
        self.assertEqual(measure.frameIndex, 16)  # frame absolue 18
        pecks.stepToMarker(-1)
        self.assertEqual(measure.frameIndex, 10)

    def test_without_a_point_type_the_controller_refuses_and_explains(self):
        from src.annodb.models import EventType
        with session_scope() as db:
            db.query(EventType).filter_by(key="bite").one().is_active = False
        pecks, measure, _data, _fish = self._controller()
        self.assertFalse(pecks.hasPointType)
        pecks.selectTrack(self.track_id)
        self.assertFalse(pecks.armed)
        self.assertFalse(pecks.markAtCurrentFrame())
        self.assertIn("Bouchée", pecks.statusText)

    # ── Export ─────────────────────────────────────────────────────────

    def test_three_points_reach_events_csv_with_their_action_id(self):
        created = self._make_point_type()
        for frame in (12, 18, 25):
            self.fa.add_behavior_point(
                self.track_id, frame, event_type=created["key"],
            )

        from src.annodb.export_behavior import (
            active_action_table,
            build_ava_rows,
            collect_events,
            write_ava,
        )

        staging = self.tmp_path / "export"
        with session_scope() as session:
            events, _media, _tracks = collect_events(session)
            actions, action_of_key = active_action_table(
                session, {row["event_type"] for row in events},
            )
            rows, report = build_ava_rows(
                session, events, action_of_key, {self.track_id: 1},
            )
            write_ava(
                staging, rows=rows, actions=actions, action_of_key=action_of_key,
            )

        self.assertEqual(report["events_kept"], 3)
        self.assertEqual(report["rows_exact"], 3)

        with (staging / "events.csv").open(encoding="utf-8", newline="") as handle:
            lines = list(csv.reader(handle))
        self.assertEqual(lines[0][0], "video_id")
        body = lines[1:]
        self.assertEqual(len(body), 3)
        action_id = str(action_of_key[created["key"]])
        self.assertEqual({row[6] for row in body}, {action_id})
        # fps = 10 : les frames 12, 18 et 25 tombent a 1.2 s, 1.8 s et 2.5 s.
        self.assertEqual([row[1] for row in body], ["1.200", "1.800", "2.500"])
        self.assertEqual({row[7] for row in body}, {"1"})

        with (staging / "actions.csv").open(encoding="utf-8", newline="") as handle:
            actions_rows = list(csv.reader(handle))
        by_key = {row[1]: row for row in actions_rows[1:]}
        self.assertEqual(by_key[created["key"]][3], "instant")


if __name__ == "__main__":
    unittest.main()
