from __future__ import annotations

import os
import sys
import threading
import unittest
from pathlib import Path
from unittest.mock import patch

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import QObject, Signal
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication


APP_ROOT = Path(__file__).resolve().parents[2] / "src" / "interface"
if str(APP_ROOT) not in sys.path:
    sys.path.insert(0, str(APP_ROOT))

from src.controllers.fish_controller import ASSIST_WAITING, FishController  # noqa: E402


class _MeasureStub(QObject):
    frameIndexChanged = Signal()
    playingChanged = Signal()
    leftVideoChanged = Signal()
    rightVideoChanged = Signal()
    framesUpdated = Signal()

    def __init__(self):
        super().__init__()
        self.frameIndex = 7
        self.playing = False
        self.frameWidth = 640
        self.frameHeight = 480
        self.leftVideo = ""
        self.rightVideo = ""
        self.leftAbsFrame = 7
        self.frameCount = 100

    def leftAbsFrameAt(self, index: int) -> int:
        return int(index)

    def overlay_remap_active(self):
        return False

    def seekFromLeftAbsFrame(self, frame: int):
        self.leftAbsFrame = int(frame)
        self.frameIndex = int(frame)
        self.frameIndexChanged.emit()

    def togglePlay(self):
        self.playing = not self.playing
        self.playingChanged.emit()


class _TrackerStub:
    def __init__(self):
        self.requests = []
        self.cancelled = 0
        self.generation = 0

    def request_follow(self, box, start, end, logical_track_id=None):
        self.generation += 1
        self.requests.append((dict(box), start, end, logical_track_id))
        return self.generation

    def cancel(self):
        self.cancelled += 1

    def db_track_id(self, external_track_id):
        return "track-db-7" if int(external_track_id) == 7 else ""

    def cached_boxes(self, _frame):
        return None

    def trails(self, _frame):
        return []


class _DataStub:
    selectedBoxIndex = -1
    eventTypeScope = "interval"
    eventTypeKey = "fuite"
    eventTypeLabel = "Fuite"
    selectedAnnId = "ann-1"

    def __init__(self):
        self.events = []
        self.behavior_events = []
        self.attached = []
        self.refocused = 0

    def selectBoxIndex(self, index):
        self.selectedBoxIndex = int(index)

    def focusSelectedObservation(self):
        self.refocused += 1

    def attachObservationToTrack(self, ann_id, track_db_id):
        self.attached.append((str(ann_id), str(track_db_id)))
        return "linked"

    # Les deux entrées d'écriture d'un intervalle restent branchées sur le
    # stub, non pour être appelées mais pour prouver le contraire : depuis le
    # retrait du cycle « Début / Fin et analyser », un suivi ne doit plus
    # jamais écrire de TemporalEvent.
    def completeAssistedGrazing(self, external, db_id, start, end):
        self.events.append((external, db_id, start, end))
        return True

    def completeAssistedBehavior(
        self, external, db_id, start, end, event_type, event_label
    ):
        self.events.append((external, db_id, start, end))
        self.behavior_events.append(
            (external, db_id, start, end, event_type, event_label)
        )
        return True


class ManualBoxEditingTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self):
        self.measure = _MeasureStub()
        self.fish = FishController(self.measure)
        self.fish._last_boxes = [
            {
                "x1": 100.0,
                "y1": 100.0,
                "x2": 200.0,
                "y2": 200.0,
                "track_id": 4,
                "cls_name": "fish",
                "conf": 0.8,
            }
        ]
        self.fish._publish_overlay()

    def tearDown(self):
        self.fish._auto_detect_timer.stop()

    def test_manual_box_can_be_moved_resized_and_clamped(self):
        self.fish.addManualBox(10, 20, 80, 90)

        self.assertEqual(self.fish.lastBoxCount, 2)
        self.assertTrue(self.fish.isManualBoxIndex(1))
        self.assertEqual(self.fish.selectedFishIndex, 1)

        changed = self.fish.updateManualBox(1, -12, 30, 700, 520)

        self.assertTrue(changed)
        edited = self.fish.lastBoxAtIndex(1)
        self.assertEqual(
            (edited["x1"], edited["y1"], edited["x2"], edited["y2"]),
            (0.0, 30.0, 640.0, 480.0),
        )
        self.assertEqual(self.fish._manual_by_frame[7][0]["x2"], 640.0)

    def test_only_manual_boxes_can_be_updated_or_removed(self):
        self.fish.addManualBox(10, 20, 80, 90)

        self.assertFalse(self.fish.updateManualBox(0, 0, 0, 30, 30))
        self.assertFalse(self.fish.removeManualBox(0))
        self.assertEqual(self.fish.lastBoxCount, 2)

    def test_auto_resize_preserves_species_confidences_and_track_without_classifying(self):
        original = {**self.fish._last_boxes[0],
                    "species_name": "Chromis viridis", "species_conf": 0.87,
                    "db_track_id": "saved-track", "species_candidates": [{"name": "Chromis viridis"}]}
        self.fish._last_boxes = [original]
        with patch.object(self.fish, "_classify_manual_box") as classify:
            self.assertTrue(self.fish.updateBox(0, -20, 30, 720, 520))
            self.assertTrue(self.fish.updateBox(0, 20, 40, 600, 450))
        classify.assert_not_called()
        edited = self.fish._last_boxes[0]
        for key in ("species_name", "species_conf", "conf", "track_id", "db_track_id", "species_candidates"):
            self.assertEqual(edited[key], original[key], key)
        self.assertNotIn("manual_draw", edited)
        self.assertEqual(self.fish.selectedFishIndex, 0)
        self.assertEqual(original["x1"], 100., "le cache du moteur ne doit pas être modifié")

    def test_auto_correction_survives_cached_overlay_and_redetection(self):
        original = dict(self.fish._last_boxes[0], species_name="Chromis viridis", species_conf=0.87)
        self.fish._last_boxes = [original]
        self.assertTrue(self.fish.updateBox(0, 90, 95, 210, 205))
        expected = dict(self.fish._last_boxes[0])
        self.fish._replace_last_boxes([dict(original, species_name="Autre poisson")])
        self.assertEqual(self.fish._last_boxes[0], expected)
        self.fish._tracker = _TrackerStub()
        self.fish._tracking = True
        self.fish.setSelectedFishIndex(0)
        with patch.object(self.fish._tracker, "cached_boxes", return_value=[original]):
            self.fish._publish_overlay()
        self.assertEqual(self.fish.lastBoxAtIndex(0)["x1"], 90.)
        self.assertEqual(self.fish.lastBoxAtIndex(0)["speciesName"], "Chromis viridis")
        self.assertEqual(self.fish.selectedFishIndex, 0)

    def test_delete_auto_box_keeps_manual_boxes_and_masks_cached_proposal(self):
        original = dict(self.fish._last_boxes[0])
        self.fish.addManualBox(10, 20, 80, 90)
        self.assertTrue(self.fish.removeBox(0))
        self.assertEqual(self.fish.lastBoxCount, 1)
        self.assertTrue(self.fish.isManualBoxIndex(0))
        self.fish._replace_last_boxes([original])
        self.fish._publish_overlay()
        self.assertEqual(self.fish.lastBoxCount, 1)
        self.assertEqual(self.fish.lastBoxAtIndex(0)["x1"], 10.)

    def test_auto_edits_are_scoped_to_the_frame_and_reset_for_another_video(self):
        original = dict(self.fish._last_boxes[0])
        self.assertTrue(self.fish.removeBox(0))
        self.measure.frameIndex = 8
        self.fish._replace_last_boxes([original])
        self.assertEqual(len(self.fish._last_boxes), 1)
        self.measure.frameIndex = 7
        self.fish._replace_last_boxes([original])
        self.assertEqual(self.fish._last_boxes, [])
        self.fish._on_videos_changed()
        self.fish._replace_last_boxes([original])
        self.assertEqual(len(self.fish._last_boxes), 1)

    def test_auto_edit_rejects_playback_invalid_index_and_invalid_geometry(self):
        original = list(self.fish._last_boxes)
        self.measure.playing = True
        self.assertFalse(self.fish.updateBox(0, 1, 1, 50, 50))
        self.assertFalse(self.fish.removeBox(0))
        self.measure.playing = False
        for index in (-1, 2):
            self.assertFalse(self.fish.updateBox(index, 1, 1, 50, 50))
            self.assertFalse(self.fish.removeBox(index))
        for coords in ((1, 1, 2, 2), (float('nan'), 1, 50, 50), (1, 1, float('inf'), 50)):
            self.assertFalse(self.fish.updateBox(0, *coords))
        self.assertEqual(self.fish._last_boxes, original)
        self.assertEqual(self.fish._auto_box_edits, {})

    def test_selected_box_honors_explicit_index_except_during_waiting_resume(self):
        self.fish.addManualBox(10, 20, 80, 90)

        self.fish.setSelectedFishIndex(0)
        self.assertEqual(self.fish._selected_box()["x1"], 100.0)
        self.fish.setSelectedFishIndex(1)
        self.assertEqual(self.fish._selected_box()["x1"], 10.0)

        self.fish._set_assist(ASSIST_WAITING, "réencadrez")
        self.fish.setSelectedFishIndex(0)
        self.assertEqual(self.fish._selected_box()["x1"], 10.0)

    def test_removal_updates_overlay_and_current_frame_storage(self):
        self.fish.addManualBox(10, 20, 80, 90)
        self.fish.addManualBox(120, 140, 200, 220)

        self.assertTrue(self.fish.removeManualBox(1))

        self.assertEqual(self.fish.lastBoxCount, 2)
        self.assertEqual(self.fish.selectedFishIndex, -1)
        remaining = self.fish.lastBoxAtIndex(1)
        self.assertTrue(remaining["manualDraw"])
        self.assertEqual((remaining["x1"], remaining["y1"]), (120.0, 140.0))
        self.assertEqual(len(self.fish._manual_by_frame[7]), 1)

    def test_resume_clicked_box_keeps_track_and_observation_despite_species(self):
        self.fish.addManualBox(10, 20, 80, 90)
        tracker = _TrackerStub()
        data = _DataStub()
        self.fish._tracker = tracker
        self.fish.set_data_controller(data)
        self.fish._assist_start = 3
        self.fish._assist_end = 12
        self.fish._assist_track_external_id = 7
        self.fish._graze_seed_ann_id = "ann-1"
        self.fish._last_boxes[0]["species_name"] = "Chromis viridis"
        self.fish._set_assist(ASSIST_WAITING)
        with patch.object(data, "selectBoxIndex") as select:
            self.assertTrue(self.fish.resumeAssistFromBox(0))
        select.assert_not_called()
        self.assertEqual(tracker.requests, [(self.fish._last_boxes[0], 7, 12, 7)])
        self.assertEqual(self.fish._graze_seed_ann_id, "ann-1")
        self.assertEqual(data.selectedAnnId, "ann-1")
        self.assertEqual(self.fish._assist_corrections, 1)
        self.assertEqual(self.fish.assistState, "running")
        self.assertEqual(len(self.fish._manual_boxes), 1)
        self.assertFalse(self.fish.resumeAssistFromBox(0))
        self.assertEqual(len(tracker.requests), 1)

    def test_resume_rejects_invalid_index_playback_busy_and_outside_range(self):
        self.fish._tracker = _TrackerStub()
        self.fish._assist_start = 3
        self.fish._assist_end = 12
        self.fish._set_assist(ASSIST_WAITING)
        for index in (-2, 1, 99):
            self.assertFalse(self.fish.resumeAssistFromBox(index))
        self.measure.playing = True
        self.assertFalse(self.fish.resumeAssistFromBox(0))
        self.measure.playing = False
        self.fish._set_busy(True)
        self.assertFalse(self.fish.resumeAssistFromBox(0))
        self.fish._set_busy(False)
        for frame in (2, 13):
            self.measure.leftAbsFrame = frame
            self.assertFalse(self.fish.resumeAssistFromBox(0))
        self.assertEqual(self.fish._tracker.requests, [])
        self.assertEqual(self.fish._assist_corrections, 0)

    def test_resume_accepts_an_existing_manual_box_by_index(self):
        self.fish.addManualBox(10, 20, 80, 90)
        self.fish.addManualBox(120, 140, 200, 220)
        self.fish._tracker = _TrackerStub()
        self.fish._assist_start = 3
        self.fish._assist_end = 12
        self.fish._set_assist(ASSIST_WAITING)
        self.assertTrue(self.fish.resumeAssistFromBox(1))
        self.assertEqual(self.fish._tracker.requests[0][0]["x1"], 10.0)

    def test_play_cancels_tracking_and_stale_detection(self):
        tracker = _TrackerStub()
        self.fish._tracker = tracker
        old_boxes = list(self.fish._last_boxes)
        self.fish._detect_seq = 8

        self.fish._on_detect_ok(
            [{"x1": 1, "y1": 1, "x2": 2, "y2": 2}], 7, (7, 7),
        )
        self.assertEqual(self.fish._last_boxes, old_boxes)

        self.measure.playing = True
        self.measure.playingChanged.emit()
        self.assertEqual(tracker.cancelled, 1)
        self.assertEqual(self.fish.lastBoxCount, 0)

        self.measure.playing = False
        self.measure.leftVideo = "clip.mp4"
        self.measure.playingChanged.emit()
        self.assertTrue(self.fish._auto_detect_timer.isActive())
        # Plusieurs demandes avant l'expiration recalent le même single-shot.
        # Une expiration produit exactement un lancement de worker.
        with patch.object(self.fish, "detectCurrentFrame") as detect:
            self.fish._on_measure_frames_updated()
            self.fish._on_measure_frames_updated()
            self.assertTrue(self.fish._auto_detect_timer.isSingleShot())
            self.fish._auto_detect_timer.stop()
            self.fish._run_auto_detect()
            detect.assert_called_once_with()

    def test_playback_reuses_cached_tracking_and_interval_logo_only(self):
        self.measure.leftVideo = "clip.mp4"
        self.measure.playing = True
        # Simule même un état résiduel incohérent : Play doit rester un
        # transport pur et ne jamais reprendre le calcul.
        self.fish._tracking = True
        self.fish._tracker = _TrackerStub()
        self.fish._annotation_overlay_cache_path = "clip.mp4"
        self.fish._annotation_overlay_cache = {
            "tracksByFrame": {
                7: [{
                    "x1": 100.0, "y1": 100.0, "x2": 200.0, "y2": 200.0,
                    "track_id": 7, "db_track_id": "track-db-7",
                    "cls_name": "fish", "conf": 0.0, "replay_track": True,
                }],
            },
            "instantByFrame": {},
            "trackPoints": {
                "track-db-7": {
                    "trackId": 7,
                    "frames": [5, 6, 7],
                    "points": [
                        {"x": 140.0, "y": 145.0},
                        {"x": 145.0, "y": 148.0},
                        {"x": 150.0, "y": 150.0},
                    ],
                },
            },
        }
        self.fish._grazing_cache_path = "clip.mp4"
        self.fish._grazing_cache = [{
            "track_db_id": "track-db-7",
            "external_track_id": 7,
            "frame_start_abs": 6,
            "frame_end_abs": 9,
            "event_type": "fuite",
            "event_label": "Fuite",
            "event_symbol": "★",
            "event_color": "#60c8ff",
        }]

        with (
            patch.object(self.fish, "_run_track_frame") as calculate_tracking,
            patch.object(self.fish, "_read_left_frame") as decode_analysis_frame,
        ):
            self.fish._on_frame_changed()

        calculate_tracking.assert_not_called()
        decode_analysis_frame.assert_not_called()
        self.assertEqual(len(self.fish.overlayBoxes), 1)
        self.assertTrue(self.fish.overlayBoxes[0]["replayTrack"])
        self.assertEqual(
            self.fish.overlayBoxes[0]["behaviorBadges"][0]["symbol"], "★",
        )
        self.assertEqual(len(self.fish.overlayTrails), 1)

    def test_instant_logo_appears_on_its_exact_frame_without_tracking(self):
        self.measure.leftVideo = "clip.mp4"
        self.fish._last_boxes = []
        self.fish._manual_boxes = []
        self.fish._annotation_overlay_cache_path = "clip.mp4"
        self.fish._annotation_overlay_cache = {
            "tracksByFrame": {},
            "trackPoints": {},
            "instantByFrame": {
                7: [{
                    "annotationId": "ann-flash",
                    "frameIndexAbs": 7,
                    "trackDbId": "",
                    "trackId": -1,
                    "geometry": {
                        "x_min": 0.25, "y_min": 0.25,
                        "x_max": 0.5, "y_max": 0.5,
                        "units": "normalized",
                    },
                    "behavior": {
                        "key": "flash", "label": "Flash", "scope": "instant",
                        "symbol": "✚", "color": "#fb7185",
                    },
                }, {
                    "annotationId": "ann-flash",
                    "frameIndexAbs": 7,
                    "trackDbId": "",
                    "trackId": -1,
                    "geometry": {
                        "x_min": 0.25, "y_min": 0.25,
                        "x_max": 0.5, "y_max": 0.5,
                        "units": "normalized",
                    },
                    "behavior": {
                        "key": "peur", "label": "Peur", "scope": "instant",
                        "symbol": "★", "color": "#60c8ff",
                    },
                }],
            },
        }
        self.fish._grazing_cache_path = "clip.mp4"
        self.fish._grazing_cache = []

        self.fish._publish_overlay()

        self.assertEqual(self.fish.overlayBoxes, [])
        self.assertEqual(len(self.fish.overlayBehaviorMarkers), 1)
        marker = self.fish.overlayBehaviorMarkers[0]
        self.assertEqual((marker["x1"], marker["y1"]), (160.0, 120.0))
        self.assertEqual(
            [badge["symbol"] for badge in marker["behaviorBadges"]],
            ["✚", "★"],
        )

    def test_playback_loads_annotation_data_once_not_on_each_frame(self):
        self.measure.leftVideo = "clip.mp4"
        self.measure.playing = True
        self.fish._tracker = None
        self.fish._annotation_overlay_cache = None
        self.fish._annotation_overlay_cache_path = ""
        self.fish._grazing_cache = None
        self.fish._grazing_cache_path = ""
        payload = {
            "trackSamples": [
                {
                    "frameIndexAbs": frame,
                    "trackDbId": "track-db-7",
                    "trackId": 7,
                    "x1": 100.0,
                    "y1": 100.0,
                    "x2": 200.0,
                    "y2": 200.0,
                }
                for frame in (7, 8)
            ],
            "instantAnnotations": [],
        }

        with (
            patch(
                "fish_annotate.list_video_annotation_overlays",
                return_value=payload,
            ) as load_overlays,
            patch(
                "fish_annotate.list_grazing_intervals", return_value=[],
            ) as load_intervals,
        ):
            self.fish._on_frame_changed()
            self.measure.leftAbsFrame = 8
            self.measure.frameIndex = 8
            self.fish._on_frame_changed()

        load_overlays.assert_called_once_with("clip.mp4")
        load_intervals.assert_called_once_with("clip.mp4")

    def test_track_follow_survives_a_loss_and_writes_no_interval(self):
        self._check_track_follow_survives_loss(reuse_box=False)

    def test_track_follow_reuses_detected_box_and_attaches_original_observation(self):
        self._check_track_follow_survives_loss(reuse_box=True)

    def _check_track_follow_survives_loss(self, *, reuse_box):
        """Le seul geste de suivi restant, décrochage compris.

        Il remplace `test_grazing_begin_loss_rebox_resume_and_event`, qui
        exerçait le cycle « Début / Fin et analyser » retiré. Ce que ce test
        prouvait du SUIVI - amorce, perte, réencadrement, reprise sur le même
        numéro logique, arrivée à Out - doit continuer d'être prouvé ; ce
        qu'il prouvait de l'écriture d'un intervalle ne doit plus arriver.
        """
        tracker = _TrackerStub()
        data = _DataStub()
        self.fish._tracker = tracker
        self.fish.set_data_controller(data)
        # Le suivi se rattache à une fiche : la bbox de l'overlay et la ligne
        # du registre doivent désigner le même poisson, sinon le rattachement
        # est refusé - c'est justement ce que garantit `_behavior_seed`.
        self.fish.focusAnnotationBox(
            "ann-1", 7, 100.0, 100.0, 200.0, 200.0, "Poisson",
        )
        self.fish.setSelectedFishIndex(0)

        self.fish.beginTrackFollow()
        self.assertEqual(self.fish.grazingWorkflowState, "marked")
        self.assertEqual(self.fish.grazingStartFrame, 7)
        self.assertTrue(self.fish.trackFollowStartMarked)
        self.assertTrue(self.fish.trackFollowActive)

        self.measure.leftAbsFrame = 12
        self.measure.frameIndex = 12
        self.fish.finishTrackFollow()
        self.assertEqual(tracker.requests[0][1:3], (7, 12))
        self.assertTrue(self.fish.trackFollowActive)
        generation = self.fish._active_follow_generation

        self.fish._on_follow_progress(generation, 8, {
            "x1": 100.0, "y1": 100.0, "x2": 200.0, "y2": 200.0,
            "track_id": 7, "cls_name": "fish", "conf": 0.9,
        })
        self.fish._on_follow_lost(generation, 9, "occlusion")
        self.assertEqual(self.fish.grazingWorkflowState, "waiting")
        self.assertTrue(self.fish.trackFollowActive)
        self.measure.leftAbsFrame = 9
        self.measure.frameIndex = 9
        if reuse_box:
            self.fish._last_boxes = [{
                "x1": 102.0, "y1": 101.0, "x2": 202.0, "y2": 201.0,
                "track_id": 99, "cls_name": "fish", "conf": 0.66,
                "species_name": "Chromis viridis",
            }]
            self.assertTrue(self.fish.resumeAssistFromBox(0))
            self.assertEqual(self.fish._manual_boxes, [])
        else:
            self.fish.addManualBox(102, 101, 202, 201)
        self.app.processEvents()
        self.assertGreaterEqual(len(tracker.requests), 2)
        self.assertEqual(tracker.requests[-1][3], 7)
        generation = self.fish._active_follow_generation

        self.fish._follow_lost_frame = -1
        self.fish._on_follow_progress(generation, 12, {
            "x1": 110.0, "y1": 105.0, "x2": 210.0, "y2": 205.0,
            "track_id": 7, "cls_name": "fish", "conf": 0.9,
        })
        self.fish._on_follow_finished(generation, 4, 12)

        # La piste rejoint la fiche du poisson - et rien d'autre n'est écrit.
        self.assertEqual(data.attached, [("ann-1", "track-db-7")])
        self.assertEqual(data.events, [])
        self.assertEqual(data.behavior_events, [])
        self.assertEqual(self.fish.grazingWorkflowState, "success")
        self.assertIn("piste #7", self.fish.grazingWorkflowMessage)
        self.assertFalse(self.fish.trackFollowActive)
        self.assertFalse(self.fish.trackFollowStartMarked)

    def test_detection_rejects_old_frame_and_relaunches_current_exactly_once(self):
        class _DetectStub(QObject):
            finished_ok = Signal(list, object)
            failed = Signal(str)
            finished = Signal()
            instances = []

            def __init__(self, frame, confidence, parent=None):
                super().__init__(parent)
                self.frame = frame
                self.confidence = confidence
                self.instances.append(self)

            def start(self):
                pass

        self.measure.leftVideo = "clip.mp4"
        self.measure.frameIndex = 10
        self.measure.leftAbsFrame = 110
        self.fish._fish_ia = True
        with (
            patch("src.controllers.fish_controller._DetectWorker", _DetectStub),
            patch.object(self.fish, "_read_left_frame", return_value=object()),
        ):
            self.fish.detectCurrentFrame()
            first = _DetectStub.instances[0]
            self.measure.frameIndex = 11
            self.measure.leftAbsFrame = 111
            self.measure.frameIndexChanged.emit()
            self.fish._on_measure_frames_updated()

            first.finished_ok.emit(
                [{"x1": 1, "y1": 1, "x2": 2, "y2": 2}], object(),
            )
            first.failed.emit("ancienne erreur")
            first.finished.emit()
            self.assertEqual(self.fish.lastBoxCount, 0)
            self.assertEqual(self.fish.fishCount, 0)
            self.assertFalse(self.fish.frameCountReady)
            self.assertNotEqual(self.fish.statusText, "ancienne erreur")
            self.assertTrue(self.fish._auto_detect_timer.isActive())

            # Simule l'unique expiration du single-shot reprogrammé.
            self.fish._auto_detect_timer.stop()
            self.fish._run_auto_detect()
            self.assertEqual(len(_DetectStub.instances), 2)
            second = _DetectStub.instances[1]
            second.finished_ok.emit(
                [{"x1": 3, "y1": 3, "x2": 4, "y2": 4}], object(),
            )
            second.finished.emit()
            self.assertEqual(self.fish._last_boxes[0]["x1"], 3)
            self.assertFalse(self.fish._auto_detect_timer.isActive())

    def test_cancelled_follow_generation_is_never_rearmed(self):
        from src.backend.tracking_worker import TrackingWorker

        worker = TrackingWorker()
        entered = threading.Event()
        release = threading.Event()
        progress = []
        finished = []
        worker.followProgress.connect(
            lambda generation, frame, box: progress.append((generation, frame, box))
        )
        worker.followFinished.connect(
            lambda generation, frames, last: finished.append((generation, frames, last))
        )
        box = {
            "x1": 0.0, "y1": 0.0, "x2": 10.0, "y2": 10.0,
            "track_id": 7,
        }

        def blocked_process(_frame, *, persist=False, generation=0):
            entered.set()
            release.wait(2.0)
            return [dict(box)]

        worker._process = blocked_process
        old_generation = worker.request_follow(box, 1, 1)
        old_command = worker._queue.get_nowait()
        old_thread = threading.Thread(target=worker._dispatch, args=(old_command,))
        old_thread.start()
        self.assertTrue(entered.wait(1.0))
        worker.cancel()
        new_generation = worker.request_follow(box, 2, 2)
        self.assertGreater(new_generation, old_generation)
        release.set()
        old_thread.join(2.0)
        self.assertFalse(old_thread.is_alive())
        self.assertEqual(progress, [])
        self.assertEqual(finished, [])

        worker._process = (
            lambda _frame, *, persist=False, generation=0: [dict(box)]
        )
        worker._dispatch(worker._queue.get_nowait())
        self.assertEqual([item[0] for item in progress], [new_generation])
        self.assertEqual([item[0] for item in finished], [new_generation])

    def test_queued_frame_cannot_divert_cancel_from_running_follow(self):
        from src.backend.tracking_worker import TrackingWorker

        worker = TrackingWorker()
        entered = threading.Event()
        release = threading.Event()
        progress = []
        persisted = []
        box = {
            "x1": 0.0, "y1": 0.0, "x2": 10.0, "y2": 10.0,
            "track_id": 7,
        }
        worker.followProgress.connect(lambda *args: progress.append(args))
        worker._persist_db = True
        worker._queue_persist = lambda *args: persisted.append(args)

        def blocked_process(_frame, *, persist=False, generation=0):
            entered.set()
            release.wait(2.0)
            return [dict(box)]

        worker._process = blocked_process
        generation_a = worker.request_follow(box, 1, 2)
        command_a = worker._queue.get_nowait()
        running = threading.Thread(target=worker._dispatch, args=(command_a,))
        running.start()
        self.assertTrue(entered.wait(1.0))
        generation_b = worker.request_frame(9)
        self.assertGreater(generation_b, generation_a)
        worker.cancel()
        release.set()
        running.join(2.0)

        self.assertFalse(running.is_alive())
        self.assertEqual(progress, [])
        self.assertEqual(persisted, [])
        self.assertEqual(worker._queue.get_nowait()[1], generation_b)

    def test_request_range_cancelled_during_process_commits_nothing(self):
        from src.backend.tracking_worker import TrackingWorker

        worker = TrackingWorker()
        worker._persist_db = True
        entered = threading.Event()
        release = threading.Event()
        progress = []
        finished = []
        worker.rangeProgress.connect(lambda *args: progress.append(args))
        worker.rangeFinished.connect(lambda *args: finished.append(args))
        box = {
            "x1": 0.0, "y1": 0.0, "x2": 10.0, "y2": 10.0,
            "cx": 0.5, "cy": 0.5, "track_id": 7,
        }

        def blocked_process(_frame, *, persist=False, generation=0):
            self.assertFalse(persist)
            entered.set()
            release.wait(2.0)
            return [dict(box)]

        worker._process = blocked_process
        generation = worker.request_range(10, 10)
        command = worker._queue.get_nowait()
        running = threading.Thread(target=worker._dispatch, args=(command,))
        running.start()
        self.assertTrue(entered.wait(1.0))
        worker.cancel()
        release.set()
        running.join(2.0)

        self.assertFalse(running.is_alive())
        self.assertEqual(worker._frame_tracks, {})
        self.assertEqual(worker._pending_rows, [])
        self.assertEqual(progress, [])
        self.assertEqual(finished, [])
        self.assertGreater(generation, 0)

    def test_flush_now_failure_keeps_batch_then_retry_persists_it(self):
        from src.backend.tracking_worker import TrackingWorker

        worker = TrackingWorker()
        rows = [
            (1, 7, 0.2, 0.3, (1.0, 2.0, 3.0, 4.0)),
            (2, 7, 0.3, 0.4, (2.0, 3.0, 4.0, 5.0)),
        ]
        worker._persist_db = True
        worker._video_path = "video.mp4"
        worker._pending_rows = list(rows)
        worker._pending_generation = 11
        worker.isRunning = lambda: True
        attempts = []

        def write(batch):
            attempts.append(list(batch))
            if len(attempts) == 1:
                raise RuntimeError("base verrouillée")

        worker._write_rows = write

        results = []
        first = threading.Thread(target=lambda: results.append(worker.flush_now(1.0)))
        first.start()
        with self.assertRaisesRegex(RuntimeError, "base verrouillée"):
            worker._dispatch(worker._queue.get(timeout=1.0))
        first.join(1.0)
        self.assertEqual(results, [False])
        self.assertEqual(worker._pending_rows, rows)

        second = threading.Thread(target=lambda: results.append(worker.flush_now(1.0)))
        second.start()
        worker._dispatch(worker._queue.get(timeout=1.0))
        second.join(1.0)
        self.assertEqual(results, [False, True])
        self.assertEqual(worker._pending_rows, [])
        self.assertEqual(attempts, [rows, rows])

    def test_frame_success_then_idle_flush_error_is_visible_and_retry_closes_it(self):
        from src.backend.tracking_worker import TrackingWorker

        worker = TrackingWorker()
        worker._persist_db = True
        worker._video_path = "video.mp4"
        worker._pending_rows = [
            (7, 3, 0.2, 0.3, (1.0, 2.0, 3.0, 4.0)),
        ]
        worker._pending_generation = 11
        attempts = 0

        def write(_rows):
            nonlocal attempts
            attempts += 1
            if attempts == 1:
                raise RuntimeError("base verrouillée")

        worker._write_rows = write
        worker.persistenceFinished.connect(self.fish._on_persistence_finished)
        self.fish._tracking = True
        self.fish._active_worker_generation = 11
        self.fish._active_persistence_generation = 11
        self.fish._on_frame_tracked(11, self.measure.leftAbsFrame, [{
            "x1": 1.0, "y1": 2.0, "x2": 3.0, "y2": 4.0,
            "track_id": 3,
        }])
        self.assertIn("piste(s) suivie(s)", self.fish.statusText)

        try:
            worker._flush_pending()
        except RuntimeError as exc:
            self.fish._on_track_failed(exc.generation, str(exc))
        self.assertEqual(self.fish.statusText, "Ecriture pistes : base verrouillée")
        self.assertEqual(self.fish._active_persistence_generation, 11)

        worker._flush_pending()
        self.assertEqual(self.fish.statusText, "Pistes enregistrées")
        self.assertEqual(self.fish._active_persistence_generation, -1)

    def test_follow_loss_emits_no_second_finished_terminal(self):
        from src.backend.tracking_worker import TrackingWorker

        worker = TrackingWorker()
        lost = []
        finished = []
        worker.followLost.connect(
            lambda generation, frame, reason: lost.append(
                (generation, frame, reason)
            )
        )
        worker.followFinished.connect(
            lambda generation, frames, last: finished.append(
                (generation, frames, last)
            )
        )
        worker._process = lambda _frame, *, persist=False, generation=0: []
        box = {"x1": 0.0, "y1": 0.0, "x2": 10.0, "y2": 10.0, "track_id": 7}
        generation = worker.request_follow(box, 1, 1)
        worker._dispatch(worker._queue.get_nowait())

        self.assertEqual(len(lost), 1)
        self.assertEqual(lost[0][0], generation)
        self.assertEqual(finished, [])

    def test_follow_flush_failure_emits_no_lost_or_finished_terminal(self):
        from src.backend.tracking_worker import TrackingWorker

        worker = TrackingWorker()
        lost = []
        finished = []
        worker.followLost.connect(lambda *args: lost.append(args))
        worker.followFinished.connect(lambda *args: finished.append(args))
        worker._process = lambda _frame, *, persist=False, generation=0: []
        worker._flush_pending = lambda generation=None: (_ for _ in ()).throw(
            RuntimeError("flush impossible")
        )
        box = {"x1": 0.0, "y1": 0.0, "x2": 10.0, "y2": 10.0, "track_id": 7}
        worker.request_follow(box, 1, 1)
        with self.assertRaisesRegex(RuntimeError, "flush impossible"):
            worker._dispatch(worker._queue.get_nowait())
        self.assertEqual(lost, [])
        self.assertEqual(finished, [])

    def test_cancel_and_follow_loss_leave_controller_non_busy(self):
        class _Tracker:
            def __init__(self):
                self.cancelled = False

            def cancel(self):
                self.cancelled = True

            def cached_boxes(self, _frame):
                return None

            def trails(self, _frame):
                return []

        tracker = _Tracker()
        self.fish._tracker = tracker
        self.fish._active_worker_generation = 11
        self.fish._active_range_generation = 11
        self.fish._set_busy(True)
        self.fish.cancelTracking()
        self.assertTrue(tracker.cancelled)
        self.assertFalse(self.fish.busy)
        self.assertEqual(self.fish._active_range_generation, -1)

        self.fish._active_follow_generation = 12
        self.fish._active_worker_generation = 12
        self.fish._following = True
        self.fish._set_busy(True)
        self.fish._on_follow_lost(12, 9, "occlusion")
        self.assertFalse(self.fish.following)
        self.assertFalse(self.fish.busy)
        self.assertEqual(self.fish._active_follow_generation, -1)

        self.fish._assist_state = "running"
        self.fish._assist_end = 20
        self.fish._active_follow_generation = 13
        self.fish._active_worker_generation = 13
        self.fish._following = True
        self.fish._set_busy(True)
        self.fish._on_follow_lost(13, 10, "perdu")
        self.assertEqual(self.fish.assistState, "waiting")
        self.assertFalse(self.fish.following)
        self.assertFalse(self.fish.busy)

    def test_late_failed_signal_cannot_corrupt_new_follow_generation(self):
        from src.backend.tracking_worker import TrackingWorker

        worker = TrackingWorker()
        entered = threading.Event()
        release = threading.Event()
        box = {"x1": 0.0, "y1": 0.0, "x2": 10.0, "y2": 10.0, "track_id": 7}

        def blocked_failure(_frame, *, persist=False, generation=0):
            entered.set()
            release.wait(2.0)
            worker.failed.emit(generation, "échec génération 1")
            return None

        worker._process = blocked_failure
        worker.failed.connect(self.fish._on_track_failed)
        old_generation = worker.request_follow(box, 1, 1)
        old_command = worker._queue.get_nowait()
        old_thread = threading.Thread(target=worker._dispatch, args=(old_command,))
        old_thread.start()
        self.assertTrue(entered.wait(1.0))

        worker.cancel()
        new_generation = worker.request_follow(box, 2, 2)
        self.fish._active_follow_generation = new_generation
        self.fish._active_worker_generation = new_generation
        self.fish._following = True
        self.fish._set_busy(True)
        self.fish._set_status("génération 2 active")
        release.set()
        old_thread.join(2.0)
        self.app.processEvents()

        self.assertGreater(new_generation, old_generation)
        self.assertEqual(self.fish._active_follow_generation, new_generation)
        self.assertTrue(self.fish.busy)
        self.assertEqual(self.fish.statusText, "génération 2 active")

    def test_every_command_has_positive_identity_and_model_error_is_single_terminal(self):
        from src.backend.tracking_worker import TrackingWorker

        worker = TrackingWorker()
        failures = []
        failed = threading.Event()

        def on_failed(generation, msg):
            failures.append((generation, msg))
            failed.set()

        worker.failed.connect(on_failed)

        def unavailable(_generation=0):
            raise RuntimeError("Poids FishTrack absents")

        worker._ensure_model = unavailable
        worker.start()
        generation = worker.request_frame(12)
        for _ in range(50):
            if failed.is_set():
                break
            QTest.qWait(20)
        self.assertTrue(failed.is_set())
        worker.stop()
        worker.wait(2000)
        self.app.processEvents()

        self.assertGreater(generation, 0)
        self.assertEqual(failures, [(generation, "Poids FishTrack absents")])

    def test_controller_rejects_stale_follow_signals(self):
        self.fish._active_follow_generation = 4
        self.fish._following = True
        before = list(self.fish._last_boxes)

        self.fish._on_follow_progress(3, 8, {
            "x1": 1, "y1": 1, "x2": 2, "y2": 2, "track_id": 99,
        })
        self.fish._on_follow_lost(3, 9, "ancien signal")
        self.fish._on_follow_finished(3, 10, 9)

        self.assertEqual(self.fish._last_boxes, before)
        self.assertEqual(self.fish._active_follow_generation, 4)
        self.assertTrue(self.fish._following)

    def _assert_final_flush_failure_is_retryable(self, kind: str):
        from src.backend.tracking_worker import TrackingWorker

        generation = 21 if kind == "range" else 22
        worker = TrackingWorker()
        worker._persist_db = True
        worker._video_path = "video.mp4"
        worker.persistenceFinished.connect(self.fish._on_persistence_finished)
        attempts = 0

        def write(_rows):
            nonlocal attempts
            attempts += 1
            if attempts == 1:
                raise RuntimeError(f"base verrouillée {kind}")

        worker._write_rows = write
        box = {
            "x1": 1.0, "y1": 2.0, "x2": 11.0, "y2": 12.0,
            "cx": 0.25, "cy": 0.25, "track_id": 7,
        }
        worker._process = lambda _frame, *, persist=False, generation=0: [dict(box)]
        self.fish._active_worker_generation = generation
        self.fish._active_persistence_generation = generation
        self.fish._set_busy(True)
        if kind == "range":
            self.fish._active_range_generation = generation
            operation = lambda: worker._track_range(
                1, 1, generation, threading.Event(),
            )
        else:
            self.fish._active_follow_generation = generation
            self.fish._following = True
            operation = lambda: worker._follow_track(
                box, 1, 1, generation=generation,
                cancel_token=threading.Event(),
            )

        try:
            operation()
        except RuntimeError as exc:
            self.fish._on_track_failed(exc.generation, str(exc))
        self.assertEqual(
            self.fish.statusText, f"Ecriture pistes : base verrouillée {kind}",
        )
        self.assertFalse(self.fish.busy)
        self.assertFalse(self.fish.following)
        self.assertEqual(self.fish._active_worker_generation, -1)
        self.assertEqual(self.fish._active_range_generation, -1)
        self.assertEqual(self.fish._active_follow_generation, -1)
        self.assertEqual(self.fish._active_persistence_generation, generation)
        self.assertEqual(self.fish._persistence_error_generation, generation)
        self.assertEqual(len(worker._pending_rows), 1)

        worker._flush_pending()
        self.assertEqual(attempts, 2)
        self.assertEqual(worker._pending_rows, [])
        self.assertEqual(self.fish.statusText, "Pistes enregistrées")
        self.assertEqual(self.fish._active_persistence_generation, -1)
        self.assertEqual(self.fish._persistence_error_generation, -1)
        self.assertFalse(self.fish.busy)
        self.assertFalse(self.fish.following)

    def test_range_final_flush_failure_closes_calculation_and_retries_batch(self):
        self._assert_final_flush_failure_is_retryable("range")

    def test_follow_final_flush_failure_closes_calculation_and_retries_batch(self):
        self._assert_final_flush_failure_is_retryable("follow")

    def test_play_invalidates_all_tracking_generations_and_ignores_late_persistence(self):
        self.fish._tracker = _TrackerStub()
        self.fish._tracking = True
        self.fish._following = True
        self.fish._graze_state = "tracking"
        self.fish._set_busy(True)
        for name in (
            "_active_worker_generation", "_active_range_generation",
            "_active_follow_generation", "_active_persistence_generation",
            "_persistence_error_generation",
        ):
            setattr(self.fish, name, 77)
        self.measure.playing = True

        self.fish._on_play_state_changed()
        status = self.fish.statusText
        grazing_state = self.fish.grazingWorkflowState
        self.fish._on_persistence_finished(77)
        self.fish._on_track_failed(77, "Ecriture pistes : retour Play tardif")

        self.assertEqual(status, self.fish.statusText)
        self.assertEqual(grazing_state, self.fish.grazingWorkflowState)
        self.assertFalse(self.fish.busy)
        self.assertFalse(self.fish.following)
        for name in (
            "_active_worker_generation", "_active_range_generation",
            "_active_follow_generation", "_active_persistence_generation",
            "_persistence_error_generation",
        ):
            self.assertEqual(getattr(self.fish, name), -1)

    def test_cancel_grazing_invalidates_generations_and_ignores_late_persistence(self):
        self.fish._tracker = _TrackerStub()
        self.fish._following = True
        self.fish._graze_state = "tracking"
        self.fish._set_busy(True)
        self.fish._set_status("Analyse broutage active")
        for name in (
            "_active_worker_generation", "_active_range_generation",
            "_active_follow_generation", "_active_persistence_generation",
            "_persistence_error_generation",
        ):
            setattr(self.fish, name, 88)

        self.fish.cancelGrazingAnalysis()
        status = self.fish.statusText
        grazing_state = self.fish.grazingWorkflowState
        self.fish._on_persistence_finished(88)
        self.fish._on_track_failed(88, "Ecriture pistes : retour broutage tardif")

        self.assertEqual(status, self.fish.statusText)
        self.assertEqual(grazing_state, self.fish.grazingWorkflowState)
        self.assertFalse(self.fish.busy)
        self.assertFalse(self.fish.following)
        for name in (
            "_active_worker_generation", "_active_range_generation",
            "_active_follow_generation", "_active_persistence_generation",
            "_persistence_error_generation",
        ):
            self.assertEqual(getattr(self.fish, name), -1)


if __name__ == "__main__":
    unittest.main()
