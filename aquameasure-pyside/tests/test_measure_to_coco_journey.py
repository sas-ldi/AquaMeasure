"""Parcours Mesure : bbox sélectionnée -> SQLite -> export COCO scellé."""

from __future__ import annotations

import json
import os
import sys
import tempfile
import threading
import time
import unittest
from pathlib import Path
from unittest.mock import patch

from PySide6.QtCore import QCoreApplication, QObject, Signal


APP_ROOT = Path(__file__).resolve().parents[1]
REPO_ROOT = APP_ROOT.parent
FV_ROOT = REPO_ROOT / "fish-vision"
for candidate in (APP_ROOT, REPO_ROOT, FV_ROOT):
    if str(candidate) not in sys.path:
        sys.path.insert(0, str(candidate))

# L'application assemble ces deux répertoires dans le namespace ``src`` dans
# main.py. Le test reproduit ce montage sans démarrer toute l'interface.
import src  # noqa: E402

for namespace_part in (APP_ROOT / "src", FV_ROOT / "src"):
    if str(namespace_part) not in src.__path__:
        src.__path__.append(str(namespace_part))

from src.controllers.data_controller import DataController  # noqa: E402
from src.controllers.fish_controller import FishController  # noqa: E402
from src.annodb.connection import init_db, session_scope  # noqa: E402
from src.annodb.export_coco import (  # noqa: E402
    UNIDENTIFIED_CATEGORY_NAME,
    export_coco,
)
from src.annodb.models import ExportRun, SpatialAnnotation  # noqa: E402


class _MeasureStub(QObject):
    frameIndexChanged = Signal()
    leftVideoChanged = Signal()
    rightVideoChanged = Signal()
    playingChanged = Signal()
    framesUpdated = Signal()
    distanceMmChanged = Signal()

    def __init__(self, media: Path):
        super().__init__()
        self.leftVideo = str(media)
        self.rightVideo = ""
        self.frameIndex = 1
        self.frameCount = 4
        self.frameWidth = 100
        self.frameHeight = 80
        self.playing = False
        self.distanceMm = 0.0
        self.clear_points_calls = 0

    def clearPoints(self):
        self.clear_points_calls += 1
        self.set_distance_mm(0.0)

    def leftAbsFrameAt(self, timeline_index: int) -> int:
        return 2 + int(timeline_index)

    @property
    def leftAbsFrame(self) -> int:
        return self.leftAbsFrameAt(self.frameIndex)

    def alignedIndexFromLeftAbs(self, absolute_index: int) -> int:
        return int(absolute_index) - 2

    def overlay_remap_active(self):
        return False

    def loadCalibration(self):
        return None

    def rectified_pair(self, _frame_index: int):
        return None, None, None, None

    def set_distance_mm(self, value: float):
        self.distanceMm = float(value)
        self.distanceMmChanged.emit()


class _FishStub:
    def __init__(self, boxes: list[dict]):
        self._boxes = boxes
        self.selectedFishIndex = -1
        self.confidence = 0.5

    @property
    def lastBoxCount(self) -> int:
        return len(self._boxes)

    def lastBoxAtIndex(self, index: int) -> dict:
        return dict(self._boxes[index]) if 0 <= index < len(self._boxes) else {}

    def isManualBoxIndex(self, index: int) -> bool:
        return 0 <= index < len(self._boxes)

    def setSelectedFishIndex(self, index: int):
        self.selectedFishIndex = int(index)

    def clearFocusBox(self):
        pass

    def tracking_flusher(self):
        return None


class _ImmediateThread:
    """Exécute le worker au démarrage pour rendre le trajet Qt déterministe."""

    def __init__(self, *, target, daemon: bool):
        self._target = target
        self.daemon = daemon

    def start(self):
        self._target()


class _TrackerStub:
    """Tracker réduit à ce que le suivi assisté lui demande vraiment.

    Le vrai tracker ouvre la vidéo dans un thread et écrit les pistes par lots :
    impossible à rejouer ici. Ce qui compte pour le rattachement, c'est l'UUID
    de piste qu'il finit par rendre.
    """

    def __init__(self, db_track_id: str):
        self._db_track_id = db_track_id
        self.follow_calls: list[tuple] = []

    def request_follow(self, box, start_abs, end_abs, logical_id):
        self.follow_calls.append((dict(box), int(start_abs), int(end_abs)))
        return 1

    def db_track_id(self, external_track_id: int) -> str:
        return self._db_track_id

    def cached_boxes(self, abs_frame: int) -> list:
        return []

    def trails(self, abs_frame: int) -> list:
        return []

    def cancel(self):
        return None


class _BlockedThread:
    instances: list["_BlockedThread"] = []

    def __init__(self, *, target, daemon: bool):
        self._target = target
        self.daemon = daemon
        self.instances.append(self)

    def start(self):
        pass

    def run(self):
        self._target()


class MeasureToCocoJourneyTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QCoreApplication.instance() or QCoreApplication([])

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory(prefix="measure_to_coco_")
        self.addCleanup(self._tmp.cleanup)
        self.tmp_path = Path(self._tmp.name)
        self.db_path = self.tmp_path / "annotations.db"
        self._set_env("FISH_VISION_SETTINGS", str(self.tmp_path / "settings.json"))
        self.storage_path = self.tmp_path / "storage.json"
        self._set_env("FISH_VISION_DB", str(self.db_path))
        self._set_env("AQUAMEASURE_STORAGE_CONFIG", str(self.storage_path))

        from src.annodb import storage_config

        storage_config.invalidate_cache()
        self.addCleanup(storage_config.invalidate_cache)
        init_db(self.db_path, seed=True)

        import cv2
        import numpy as np

        self.media = self.tmp_path / "sequence.avi"
        image = np.zeros((80, 100, 3), dtype=np.uint8)
        image[20:70, 10:60] = (220, 220, 220)
        writer = cv2.VideoWriter(
            str(self.media), cv2.VideoWriter_fourcc(*"MJPG"), 10.0, (100, 80),
        )
        self.assertTrue(writer.isOpened())
        for index in range(4):
            frame = image.copy()
            frame[:, :, 0] = index * 20
            writer.write(frame)
        writer.release()
        self.assertTrue(self.media.is_file())

        self.measure = _MeasureStub(self.media)
        self.box = {
            "x1": 10.0,
            "y1": 20.0,
            "x2": 60.0,
            "y2": 70.0,
            "trackId": -1,
            "clsName": "fish",
            "conf": 0.0,
        }

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

    def _wait_until(self, predicate, timeout: float = 3.0):
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            self.app.processEvents()
            if predicate():
                return
            time.sleep(0.01)
        self.fail("Condition Qt non satisfaite avant expiration")

    def test_empty_draft_stays_unreviewed_and_na_is_explicit(self):
        import fish_annotate as fa
        fa.create_annotator("Integration Test")
        data = DataController(self.measure, _FishStub([self.box]))
        data.selectBoxExplicit(0)
        with patch("src.controllers.data_controller.threading.Thread", _ImmediateThread):
            data.saveDraftObservation()
        rows = fa.list_observations(media_path=str(self.media))
        self.assertEqual(len(rows), 1)
        self.assertNotEqual(rows[0]["identification_status"], "identified")
        self.assertIn("identification à faire", data.statusText)
        with session_scope() as session:
            ann = session.get(SpatialAnnotation, rows[0]["ann_id"])
            self.assertIsNone(ann.reviewed_by)
        data.saveSelectedRow("NA", "NA", "NA")
        self.assertEqual(len(fa.list_observations(media_path=str(self.media))), 1)
        with session_scope() as session:
            ann = session.get(SpatialAnnotation, rows[0]["ann_id"])
            self.assertIsNotNone(ann.reviewed_by)

    def test_detach_keeps_track_samples_and_events(self):
        import fish_annotate as fa
        from sqlalchemy import select, func
        from src.annodb.models import Track, TrackSample, TemporalEvent
        fish, data, ann_id, track_id, _ = self._registry_fish_and_track()
        self.assertEqual(data.attachObservationToTrack(ann_id, track_id), fa.LINK_LINKED)
        self._seed_existing_interval(data, ann_id, track_id, 3, 5)
        kind = fa.create_behavior_type("Bouchee integration", "point", "●")
        fa.add_behavior_point(track_id, 4, event_type=kind["key"])
        data.refreshBehaviorPoints()
        points = [r for r in data.selectedObservationEvents if r["scope"] == "point"]
        self.assertEqual(points[0]["count"], 1)
        summary = fa.track_link_summary(ann_id)
        self.assertEqual(summary["point_count"], 1)
        self.assertEqual(summary["interval_count"], 1)
        self.assertEqual(data.detachSelectedObservationFromTrack(), fa.LINK_DETACHED)
        self.assertEqual(data.selectedTrackDbId, "")
        self.assertFalse(data.selectedObservationEvents)
        with session_scope() as session:
            self.assertIsNone(session.get(SpatialAnnotation, ann_id).track_id)
            self.assertIsNotNone(session.get(Track, track_id))
            self.assertEqual(session.scalar(select(func.count()).select_from(TrackSample).where(TrackSample.track_id == track_id)), 3)
            self.assertEqual(session.scalar(select(func.count()).select_from(TemporalEvent).where(TemporalEvent.track_id == track_id)), 2)

    def test_manual_box_classifies_displayed_crop_before_any_write(self):
        import fish_annotate as fa
        import numpy as np
        frame = np.zeros((80, 100, 3), dtype=np.uint8)
        fish = FishController(self.measure)
        fish._fishial_ready = True
        def classify(actual, boxes):
            self.assertIs(actual, frame)
            return [{**boxes[0], "species_name": "Acanthurus sp.", "species_conf": 0.8}]
        with patch.object(self.measure, "rectified_pair", return_value=(frame, None, None, None)), patch("fishial_classify.is_available", return_value=True), patch("fishial_classify.classify_boxes", side_effect=classify) as classified:
            fish.addManualBox(10, 20, 60, 70)
        classified.assert_called_once()
        self.assertEqual(fish.lastBoxAtIndex(0)["speciesName"], "Acanthurus sp.")
        self.assertEqual(fa.list_observations(media_path=str(self.media)), [])

    def test_validated_identity_survives_tracking_replay_correction_and_detach(self):
        import fish_annotate as fa
        fish, data, ann_id, track_id, _ = self._registry_fish_and_track()
        fa.create_annotator("Identification Test")
        data.saveSelectedRow("Acanthuridae", "Acanthurus", "Acanthurus sp.")
        auto_box = {**self.box, "track_id": 7, "species_name": "Cyprinus rubrofuscus", "species_conf": 0.91}
        fish._graze_seed_ann_id = ann_id
        fish._graze_state = "tracking"
        fish._assist_track_external_id = 7
        live = fish._boxes_for_qml([auto_box])[0]
        self.assertEqual(live["speciesName"], "Acanthurus sp.")
        self.assertEqual(live["identificationSource"], "validated")
        self.assertEqual(live["speciesConf"], 0)
        other = fish._boxes_for_qml([{**auto_box, "track_id": 99}])[0]
        self.assertEqual(other["speciesName"], "Cyprinus rubrofuscus")
        self.assertEqual(data.attachObservationToTrack(ann_id, track_id), fa.LINK_LINKED)
        fish._graze_seed_ann_id = ""
        # Un controleur neuf prouve que le nom vient de la base, pas du focus.
        replay = FishController(self.measure)
        replay.set_data_controller(data)
        self.assertEqual(replay._boxes_for_qml([auto_box])[0]["speciesName"], "Acanthurus sp.")
        cached = replay._annotation_overlays()
        persisted_box = cached["tracksByFrame"][3][0]
        self.assertEqual(replay._boxes_for_qml([persisted_box])[0]["speciesName"], "Acanthurus sp.")
        data.saveSelectedRow("Scaridae", "Scarus", "Scarus ghobban")
        self.assertEqual(replay._boxes_for_qml([auto_box])[0]["speciesName"], "Scarus ghobban")
        data.detachSelectedObservationFromTrack()
        self.assertEqual(replay._boxes_for_qml([auto_box])[0]["speciesName"], "Cyprinus rubrofuscus")

    def test_single_save_action_creates_then_corrects_the_same_fish(self):
        import fish_annotate as fa
        fa.create_annotator("Unified Save Test")
        data = DataController(self.measure, _FishStub([self.box]))
        data.selectBoxExplicit(0)
        self.measure.distanceMm = 154.2
        data.onStereoMeasureChanged()
        with patch("src.controllers.data_controller.threading.Thread", _ImmediateThread):
            data.saveFish("Acanthuridae", "Acanthurus", "Acanthurus sp.")
        original_id = data.selectedAnnId
        self.assertTrue(original_id)
        self.assertFalse(data.draftActive)
        self.assertEqual(self.measure.clear_points_calls, 1)
        self.assertEqual(self.measure.distanceMm, 0)
        self.assertAlmostEqual(data.editMeasurementMm, 154.2)
        data.setFamilyFilter("Scaridae")
        data.setGenusFilter("Scarus")
        data.setSpeciesFilter("Scarus ghobban")
        self.assertTrue(data.registryRowDirty)
        data.saveFish("Scaridae", "Scarus", "Scarus ghobban")
        data.saveFish("Scaridae", "Scarus", "Scarus ghobban")
        rows = fa.list_observations(media_path=str(self.media))
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["ann_id"], original_id)
        self.assertEqual(rows[0]["species"], "Scarus ghobban")
        self.assertAlmostEqual(rows[0]["measurement_mm"], 154.2)
        self.assertEqual(rows[0]["identification_status"], "identified")
        self.assertFalse(data.registryRowDirty)
        self.assertEqual(self.measure.clear_points_calls, 3)

    def test_save_without_identity_hides_handles_but_keeps_saved_length(self):
        import fish_annotate as fa
        data = DataController(self.measure, _FishStub([self.box]))
        data.selectBoxExplicit(0)
        self.measure.distanceMmChanged.connect(data.onStereoMeasureChanged)
        self.measure.set_distance_mm(123.4)
        with patch("src.controllers.data_controller.threading.Thread", _ImmediateThread):
            data.saveFish("", "", "")
        self.assertEqual(self.measure.clear_points_calls, 1)
        self.assertEqual(self.measure.distanceMm, 0)
        self.assertTrue(data.selectedAnnId)
        self.assertAlmostEqual(data.editMeasurementMm, 123.4)
        rows = fa.list_observations(media_path=str(self.media))
        self.assertEqual(len(rows), 1)
        self.assertAlmostEqual(rows[0]["measurement_mm"], 123.4)
        data.saveFish("", "", "")
        self.assertEqual(self.measure.clear_points_calls, 2)
        self.assertAlmostEqual(data.editMeasurementMm, 123.4)

    def test_failed_save_preserves_measurement_handles_for_retry(self):
        data = DataController(self.measure, _FishStub([self.box]))
        data.selectBoxExplicit(0)
        self.measure.distanceMmChanged.connect(data.onStereoMeasureChanged)
        self.measure.set_distance_mm(123.4)
        with patch("src.controllers.data_controller.threading.Thread", _ImmediateThread), \
                patch.object(data, "_db_add_observation", side_effect=RuntimeError("write failed")):
            data.saveFish("Scaridae", "Scarus", "Scarus ghobban")
        self.assertEqual(self.measure.clear_points_calls, 0)
        self.assertAlmostEqual(self.measure.distanceMm, 123.4)
        self.assertTrue(data.draftActive)
        self.assertAlmostEqual(data.editMeasurementMm, 123.4)

    def test_single_save_respects_busy_and_playback_guards(self):
        data = DataController(self.measure, _FishStub([self.box]))
        data._selected_ann_id = "already-exists"
        with patch.object(data, "_save_selected_row") as update:
            data._busy = True
            data.saveFish("Scaridae", "", "")
            self.assertIn("en cours", data.statusText)
            data._busy = False
            self.measure.playing = True
            data.saveFish("Scaridae", "", "")
            self.assertIn("pause", data.statusText)
            update.assert_not_called()

    def test_unique_box_is_selected_without_hidden_right_click(self):
        fish = _FishStub([self.box])
        data = DataController(self.measure, fish)
        selected: list[int] = []
        data._add_observation_at_index = selected.append

        data.addObservationFromSelectedBox()

        self.assertEqual(selected, [0])
        self.assertEqual(data.selectedBoxIndex, 0)
        self.assertEqual(fish.selectedFishIndex, 0)

    # ── Ordre du workflow : fiche d'abord, écriture ensuite ────────────────
    # Le client décrivait le parcours comme « à l'envers » : il fallait créer
    # la ligne pour voir la proposition du modèle, puis mesurer, puis valider.
    # Ces tests figent le nouvel ordre - clic sur le poisson, préremplissage,
    # mesure, un seul enregistrement - et prouvent qu'aucune ligne n'est
    # écrite avant ce dernier geste.

    def test_right_click_prefills_taxonomy_without_writing_a_row(self):
        import fish_annotate as fa

        fish = _FishStub([{**self.box, "speciesName": "Acanthurus sp."}])
        data = DataController(self.measure, fish)

        data.selectBoxExplicit(0)

        self.assertTrue(data.draftActive)
        self.assertEqual(data.editFamily, "Acanthuridae")
        self.assertEqual(data.editGenus, "Acanthurus")
        self.assertEqual(data.editSpecies, "Acanthurus sp.")
        self.assertEqual(data.selectedAnnId, "")
        self.assertFalse(data.registryRowDirty)
        self.assertEqual(fa.list_observations(media_path=str(self.media)), [])

    def test_detector_class_alone_also_feeds_the_prefill(self):
        """Sans espèce Fishial, la classe du détecteur propose au moins la famille."""
        import fish_annotate as fa

        fish = _FishStub([{**self.box, "clsName": "Scaridae"}])
        data = DataController(self.measure, fish)

        data.selectBoxExplicit(0)

        self.assertEqual(data.editFamily, "Scaridae")
        self.assertEqual(data.editGenus, "")
        self.assertEqual(data.editSpecies, "")
        self.assertIn("Scaridae", data.draftTaxonOrigin)
        self.assertEqual(fa.list_observations(media_path=str(self.media)), [])

    def test_measure_prefills_the_draft_length_before_any_write(self):
        import fish_annotate as fa

        data = DataController(self.measure, _FishStub([self.box]))
        data.selectBoxExplicit(0)

        self.measure.distanceMm = 143.6
        data.onStereoMeasureChanged()

        self.assertTrue(data.draftActive)
        self.assertAlmostEqual(data.editMeasurementMm, 143.6)
        self.assertAlmostEqual(data.pendingStereoMeasureMm, 143.6)
        self.assertIn("Enregistrer le poisson", data.statusText)
        self.assertEqual(fa.list_observations(media_path=str(self.media)), [])

    def test_one_gesture_writes_taxon_and_length_together(self):
        import fish_annotate as fa

        fa.create_annotator("Pierrick Test")
        fish = _FishStub([{**self.box, "speciesName": "Acanthurus sp."}])
        data = DataController(self.measure, fish)
        data.selectBoxExplicit(0)
        self.measure.distanceMm = 208.3
        data.onStereoMeasureChanged()
        # « On change si on n'est pas content avec ce qu'il y a » : la
        # correction saisie AVANT l'enregistrement doit être celle qui part.
        data.setGenusFilter("Ctenochaetus")
        data.setSpeciesFilter("Ctenochaetus sp.")

        with patch(
            "src.controllers.data_controller.threading.Thread", _ImmediateThread,
        ):
            data.saveDraftObservation()

        rows = fa.list_observations(media_path=str(self.media))
        self.assertEqual(len(rows), 1)
        row = rows[0]
        self.assertAlmostEqual(row["measurement_mm"], 208.3)
        self.assertEqual(row["family"], "Acanthuridae")
        self.assertEqual(row["genus"], "Ctenochaetus")
        self.assertEqual(row["species"], "Ctenochaetus sp.")
        self.assertEqual(row["identification_status"], "identified")
        self.assertEqual(data.selectedAnnId, row["ann_id"])
        self.assertFalse(data.draftActive)
        self.assertFalse(data.registryRowDirty)

    def test_removing_the_draft_length_keeps_the_fish_and_writes_none(self):
        import fish_annotate as fa

        data = DataController(self.measure, _FishStub([self.box]))
        data.selectBoxExplicit(0)
        self.measure.distanceMm = 174.0
        data.onStereoMeasureChanged()
        self.assertAlmostEqual(data.editMeasurementMm, 174.0)

        data.clearDraftMeasurement()

        self.assertTrue(data.draftActive)
        self.assertEqual(data.editMeasurementMm, 0.0)
        self.assertEqual(data.pendingStereoMeasureMm, 0.0)

        with patch(
            "src.controllers.data_controller.threading.Thread", _ImmediateThread,
        ):
            data.saveDraftObservation()

        row = fa.list_observations(media_path=str(self.media))[0]
        self.assertIsNone(row["measurement_mm"])

    def test_reopening_a_saved_fish_never_duplicates_it(self):
        """Le cadre reste sélectionné après l'écriture : pas de seconde ligne."""
        import fish_annotate as fa

        data = DataController(self.measure, _FishStub([self.box]))
        data.selectBoxExplicit(0)
        data.setFamilyFilter("Acanthuridae")
        with patch(
            "src.controllers.data_controller.threading.Thread", _ImmediateThread,
        ):
            data.saveDraftObservation()
        self.assertEqual(len(fa.list_observations(media_path=str(self.media))), 1)

        data.beginDraftFromSelectedBox()

        self.assertFalse(data.draftActive)
        self.assertIn("déjà enregistré", data.statusText)
        with patch(
            "src.controllers.data_controller.threading.Thread", _ImmediateThread,
        ):
            data.saveDraftObservation()
        self.assertEqual(len(fa.list_observations(media_path=str(self.media))), 1)

    def test_clearing_the_selection_closes_the_draft_without_writing(self):
        import fish_annotate as fa

        fish = _FishStub([{**self.box, "speciesName": "Acanthurus sp."}])
        data = DataController(self.measure, fish)
        data.selectBoxExplicit(0)
        self.measure.distanceMm = 99.0
        data.onStereoMeasureChanged()
        self.assertTrue(data.draftActive)

        data.clearSelection()

        self.assertFalse(data.draftActive)
        self.assertEqual(data.editFamily, "")
        self.assertEqual(data.editGenus, "")
        self.assertEqual(data.editSpecies, "")
        self.assertEqual(data.editMeasurementMm, 0.0)
        self.assertEqual(data.selectedBoxIndex, -1)
        self.assertEqual(fa.list_observations(media_path=str(self.media)), [])

    def test_resizing_the_box_drops_the_length_shown_by_the_sheet(self):
        """Le panneau ne doit pas montrer une longueur qui ne sera pas écrite."""
        fish = FishController(self.measure)
        data = DataController(self.measure, fish)
        fish.set_data_controller(data)
        fish._manual_by_frame.clear()
        fish.addManualBox(10.0, 20.0, 60.0, 70.0)
        data.selectBoxExplicit(0)
        self.measure.distanceMm = 132.0
        data.onStereoMeasureChanged()
        self.assertAlmostEqual(data.editMeasurementMm, 132.0)

        self.assertTrue(fish.updateManualBox(0, 12.0, 22.0, 66.0, 76.0))

        self.assertTrue(data.draftActive)
        self.assertEqual(data.editMeasurementMm, 0.0)
        self.assertEqual(data.pendingStereoMeasureMm, 0.0)

        # Remesurer la nouvelle géométrie réalimente la MÊME fiche.
        self.measure.distanceMm = 158.0
        data.onStereoMeasureChanged()
        self.assertAlmostEqual(data.editMeasurementMm, 158.0)

    def test_resized_auto_box_saves_same_species_and_corrected_geometry(self):
        import fish_annotate as fa

        fish = FishController(self.measure)
        data = DataController(self.measure, fish)
        fish.set_data_controller(data)
        fish._last_boxes = [{**self.box, "species_name": "Chromis viridis", "species_conf": 0.87}]
        fish._publish_overlay()
        data.selectBoxExplicit(0)
        self.assertEqual(data.editSpecies, "Chromis viridis")
        data.editSpecies = "Chromis chromis"
        self.measure.distanceMm = 132.
        data.onStereoMeasureChanged()
        with patch.object(fish, "_classify_manual_box") as classify:
            self.assertTrue(fish.updateBox(0, 12., 22., 66., 76.))
            data.selectBoxExplicit(0)
            self.assertEqual(data.editSpecies, "Chromis chromis")
            self.assertEqual(data.editMeasurementMm, 0.)
            self.measure.distanceMm = 158.
            data.onStereoMeasureChanged()
            with patch("src.controllers.data_controller.threading.Thread", _ImmediateThread):
                data.saveDraftObservation()
        classify.assert_not_called()
        rows = fa.list_observations(media_path=str(self.media))
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["species"], "Chromis chromis")
        self.assertAlmostEqual(rows[0]["measurement_mm"], 158.)
        self.assertEqual(data._bbox_pixels(rows[0]["geometry"]), (12., 22., 66., 76.))

    def test_existing_row_identification_still_edits_in_place(self):
        """Non-régression : corriger une ligne déjà écrite reste possible."""
        import fish_annotate as fa

        data = DataController(self.measure, _FishStub([self.box]))
        with patch(
            "src.controllers.data_controller.threading.Thread", _ImmediateThread,
        ):
            data.addObservationFromSelectedBox()
        ann_id = data.selectedAnnId
        self.assertTrue(ann_id)

        data.refreshRegistry()
        data.loadRegistryRow(data._registry_index_by_ann_id(ann_id))
        # Ouvrir une ligne du registre ferme toute fiche : les deux modes
        # partagent les mêmes champs et ne peuvent pas coexister.
        self.assertFalse(data.draftActive)

        data.setFamilyFilter("Acanthuridae")
        data.setGenusFilter("NA")
        data.setSpeciesFilter("NA")
        self.assertTrue(data.registryRowDirty)

        data.saveSelectedObservation()

        persisted = next(
            row for row in fa.list_observations(media_path=str(self.media))
            if row["ann_id"] == ann_id
        )
        self.assertEqual(persisted["family"], "Acanthuridae")
        self.assertEqual(persisted["identification_status"], "identified")
        self.assertFalse(data.registryRowDirty)

    def test_unsaved_identification_blocks_opening_another_fish(self):
        """Le verrou d'ajout survit : le préremplissage écrase les trois champs."""
        other = {**self.box, "x1": 65.0, "x2": 95.0}
        data = DataController(self.measure, _FishStub([self.box, other]))
        data.selectBoxIndex(0)
        with patch(
            "src.controllers.data_controller.threading.Thread", _ImmediateThread,
        ):
            data.addObservationFromSelectedBox()
        data.setFamilyFilter("Acanthuridae")
        self.assertTrue(data.registryRowDirty)
        saved_ann_id = data.selectedAnnId

        data.selectBoxExplicit(1)

        self.assertFalse(data.draftActive)
        self.assertEqual(data.selectedAnnId, saved_ann_id)
        self.assertEqual(data.editFamily, "Acanthuridae")
        self.assertIn("non enregistrée", data.statusText)

        # « Annuler » débloque, et le poisson suivant s'ouvre normalement.
        data.discardRowEdits()
        data.selectBoxExplicit(1)
        self.assertTrue(data.draftActive)
        self.assertEqual(data.selectedAnnId, "")

    def test_clearing_a_row_selection_releases_the_unsaved_lock(self):
        """Le verrou restait armé sans ligne à valider : plus aucun ajout."""
        data = DataController(self.measure, _FishStub([self.box]))
        with patch(
            "src.controllers.data_controller.threading.Thread", _ImmediateThread,
        ):
            data.addObservationFromSelectedBox()
        data.setFamilyFilter("Acanthuridae")
        self.assertTrue(data.registryRowDirty)

        data._clear_selected_row()

        self.assertEqual(data.selectedAnnId, "")
        self.assertFalse(data.registryRowDirty)

    def test_controller_toggles_instant_behavior_without_measure_or_track(self):
        import fish_annotate as fa

        created = fa.add_observation(
            str(self.media),
            3,
            {"x1": 10.0, "y1": 20.0, "x2": 60.0, "y2": 70.0},
            source="manual",
            measurement_mm=None,
            track_id=None,
            frame_ref="absolute",
        )
        data = DataController(self.measure, _FishStub([self.box]))
        self.assertTrue(data.addBehaviorType("Virage bref", "instant"))
        data.refreshRegistry()
        row_index = next(
            i for i in range(data.registry.rowCount())
            if data.registry.row_at(i).get("ann_id") == created["ann_id"]
        )
        data.loadRegistryRow(row_index)
        instant_index = next(
            i for i, row in enumerate(data._event_types)
            if row.get("key") == "virage_bref"
        )
        data.setEventTypeIndex(instant_index)

        self.assertFalse(data.currentBehaviorFlagged)
        self.assertTrue(data.toggleSelectedBehavior())
        self.assertTrue(data.currentBehaviorFlagged)
        stored = fa.list_observations(ann_id=created["ann_id"])[0]
        self.assertIsNone(stored["measurement_mm"])
        self.assertIsNone(stored["track_id"])
        self.assertEqual([row["key"] for row in stored["behaviors"]], ["virage_bref"])
        import fish_db_stats as dbs

        dumped = dbs.dump_session_json(stored["media_id"])
        dumped_row = next(
            row for row in dumped["annotations"]
            if row["ann_id"] == created["ann_id"]
        )
        self.assertEqual(
            [row["key"] for row in dumped_row["behaviors"]], ["virage_bref"]
        )

        self.assertFalse(data.toggleSelectedBehavior())
        self.assertFalse(data.currentBehaviorFlagged)
        stored = fa.list_observations(ann_id=created["ann_id"])[0]
        self.assertEqual(stored["behaviors"], [])

        self.assertTrue(data.toggleSelectedBehavior())
        self.assertTrue(data.currentBehaviorFlagged)
        reloaded = DataController(self.measure, _FishStub([self.box]))
        reloaded.loadEventTypes()
        reloaded.refreshRegistry()
        row_index = next(
            i for i in range(reloaded.registry.rowCount())
            if reloaded.registry.row_at(i).get("ann_id") == created["ann_id"]
        )
        reloaded.loadRegistryRow(row_index)
        instant_index = next(
            i for i, row in enumerate(reloaded._event_types)
            if row.get("key") == "virage_bref"
        )
        reloaded.setEventTypeIndex(instant_index)
        self.assertTrue(reloaded.currentBehaviorFlagged)

    def test_an_interval_type_can_be_removed_even_during_a_follow(self):
        """Le suivi conservé ne fige plus aucun comportement.

        Le garde-fou « ce comportement est figé par le suivi en cours »
        protégeait le cycle « Début / Fin et analyser », qui gelait un type
        d'événement entre son début et sa fin. Ce cycle est parti : le suivi
        ne produit qu'une piste, il n'a plus de type à geler.
        """
        fish, data, _ann_id, _produced, _other = self._registry_fish_and_track()
        self.assertTrue(data.addBehaviorType("Ponte suivie", "interval"))
        row = next(
            item for item in data.behaviorTypes if item.get("key") == "ponte_suivie"
        )

        fish.beginTrackFollow()
        self.assertTrue(fish.trackFollowActive)

        self.assertTrue(data.removeBehaviorType(row["id"]))
        self.assertNotIn(
            "ponte_suivie", [item.get("key") for item in data.behaviorTypes],
        )

    def _registry_fish_and_track(self, *, bound_to_other_track: bool = False):
        """Un poisson mesuré dans le registre, et la piste que le suivi rendra.

        Reproduit l'état réel au moment du geste : une observation choisie dans
        le registre (donc « focalisée » sur son image), et une piste ByteTrack
        déjà écrite en base par le worker de tracking.
        """
        import fish_annotate as fa
        from src.annodb.tracks import get_or_create_track

        created = fa.add_observation(
            str(self.media),
            3,
            {"x1": 10.0, "y1": 20.0, "x2": 60.0, "y2": 70.0},
            source="manual",
            measurement_mm=214.5,
            frame_ref="absolute",
        )
        media_id = fa.resolve_media_id(str(self.media))
        self.assertIsNotNone(media_id)
        from src.annodb.tracks import add_track_sample, refresh_track_bounds

        with session_scope() as session:
            produced = get_or_create_track(
                session, media_id=media_id, external_track_id=7,
            ).id
            # Une piste rendue par le tracker porte ses positions : sans elles
            # le bloc « Bouchées » ne la voit pas (il ignore les pistes vides),
            # et rien ne serait cliquable sur l'image.
            for frame in range(3, 6):
                add_track_sample(
                    session, track_id=produced, frame_index=frame,
                    cx=35.0, cy=45.0, bbox=(10.0, 20.0, 60.0, 70.0),
                )
            refresh_track_bounds(session, produced)
            other = get_or_create_track(
                session, media_id=media_id, external_track_id=99,
            ).id
            if bound_to_other_track:
                # Rattachement posé directement en base : c'est l'état de
                # départ du test, pas le chemin qu'il vérifie.
                session.get(SpatialAnnotation, created["ann_id"]).track_id = other
        fish = FishController(self.measure)
        data = DataController(self.measure, fish)
        fish.set_data_controller(data)
        data.loadEventTypes()
        graze_index = next(
            i for i, row in enumerate(data._event_types)
            if row.get("key") == "grazing"
        )
        data.setEventTypeIndex(graze_index)
        self.assertEqual(data.eventTypeScope, "interval")
        data.refreshRegistry()
        row_index = next(
            i for i in range(data.registry.rowCount())
            if data.registry.row_at(i).get("ann_id") == created["ann_id"]
        )
        data.focusRegistryRow(row_index)
        self.assertEqual(data.selectedAnnId, created["ann_id"])
        self.assertTrue(fish.focusBox["valid"], "la ligne du registre n'est pas focalisée")
        return fish, data, created["ann_id"], produced, other

    def _seed_existing_interval(self, data, ann_id, track_db_id, start, end):
        """Un intervalle « déjà en base », comme dans la base du client.

        L'application n'en écrit plus depuis le retrait du cycle « Début / Fin
        et analyser » : ceux que le client a déjà enregistrés doivent
        continuer d'être lus, affichés et exportés. On les pose donc par
        l'écriture conservée à dessein, `fish_annotate.add_grazing_interval`.
        """
        import fish_annotate as fa

        fa.add_grazing_interval(
            track_db_id, start, end, frame_ref="absolute", event_type="grazing",
        )
        data.refreshGrazing()
        data.refreshRegistry()
        row_index = next(
            i for i in range(data.registry.rowCount())
            if data.registry.row_at(i).get("ann_id") == ann_id
        )
        data.loadRegistryRow(row_index)

    def test_an_interval_already_in_base_is_still_shown_and_exported(self):
        """Ce que le client a déjà enregistré ne doit rien perdre.

        L'application n'écrit plus d'intervalle, mais sa base en contient : la
        pastille de la fiche, `selectedObservationEvents` et la ligne d'export
        doivent continuer de les porter, joints au poisson par sa piste.
        """
        import fish_annotate as fa

        fish, data, ann_id, produced, _other = self._registry_fish_and_track()

        # Aucune bbox IA sur l'image : la ligne du registre suffit à désigner
        # le poisson, ce qui était impossible avant (clic droit obligatoire).
        self.assertEqual(fish.lastBoxCount, 0)
        self.assertTrue(fish.behaviorSeedReady)

        self._run_track_follow(fish, produced)

        stored = fa.list_observations(ann_id=ann_id)[0]
        self.assertEqual(stored["track_id"], produced)
        self.assertEqual(stored["measurement_mm"], 214.5)
        # La piste amorce le suivi à la bbox enregistrée de l'observation.
        self.assertEqual(
            fish._tracker.follow_calls[0][0]["x1"], 10.0,
        )
        self.assertEqual(fish._tracker.follow_calls[0][1:], (3, 5))

        # Le suivi n'a rien écrit : la fiche est vide d'événements.
        self.assertEqual(data.selectedObservationEvents, [])

        self._seed_existing_interval(data, ann_id, produced, 3, 5)

        events = data.selectedObservationEvents
        self.assertEqual(
            [(item["label"], item["scope"], item["detail"]) for item in events],
            [("Broutage", "interval", "f3→f5")],
        )

        # L'export joint le poisson et sa broute par `track_id` : la ligne du
        # CSV de session porte la taille ET l'événement, ensemble.
        from src.annodb.models import MediaAsset
        from src.annodb.session_stats import iter_session_timeline_rows

        with session_scope() as session:
            media = session.get(MediaAsset, fa.resolve_media_id(str(self.media)))
            exported = next(
                row for row in iter_session_timeline_rows(session, media)
                if row["frame_index"] == 3
            )
        self.assertEqual(exported["track_id"], produced)
        self.assertEqual(exported["measurement_mm"], 214.5)
        self.assertEqual(exported["is_grazing"], 1)
        self.assertEqual(exported["event_type"], "grazing")

    def test_fish_already_bound_to_another_track_is_never_rewritten(self):
        """Rediriger une fiche en silence fausserait le comptage d'individus.

        La piste produite par le suivi existe bien - le travail n'est pas
        perdu - mais la fiche du poisson n'est pas touchée, et le message le
        dit au lieu de laisser le découvrir dans un export faux.
        """
        import fish_annotate as fa

        fish, data, ann_id, produced, other = self._registry_fish_and_track(
            bound_to_other_track=True,
        )

        self._run_track_follow(fish, produced)

        stored = fa.list_observations(ann_id=ann_id)[0]
        self.assertEqual(stored["track_id"], other)
        self.assertEqual(fish.grazingWorkflowState, "warning")
        self.assertIn("appartient déjà à une autre piste", fish.grazingWorkflowMessage)
        # Un intervalle posé sur la piste du suivi ne doit surtout pas
        # s'afficher sur la fiche d'un poisson rattaché à une AUTRE piste.
        self._seed_existing_interval(data, ann_id, produced, 3, 5)
        intervals = fa.list_grazing_intervals(str(self.media))
        self.assertEqual([row["track_db_id"] for row in intervals], [produced])
        self.assertEqual(data.selectedObservationEvents, [])

    def _run_track_follow(self, fish, produced_track_id: str, behavior_key=None, after_begin=None):
        """« In » sur le poisson sélectionné, navigation, puis « Out »."""
        if behavior_key:
            self.assertTrue(fish.beginBehaviorFollow(behavior_key))
        else:
            fish.beginTrackFollow()
        self.assertEqual(fish.grazingWorkflowState, "marked")
        if after_begin:
            after_begin()
        self.measure.frameIndex = 3
        fish._tracker = _TrackerStub(produced_track_id)
        fish.finishTrackFollow()
        self.assertEqual(fish.grazingWorkflowState, "tracking")
        fish._assist_track_external_id = 7
        fish._assist_on_finished()

    def _peck_controller(self, data, fish):
        """Contrôleur de bouchées branché sur la même vidéo et la même base.

        Dans l'application, le chargement d'une paire de vidéos résout le
        media ; le stub porte déjà son chemin sans jamais l'avoir émis, d'où
        l'appel explicite - `Pecks` ne trouve aucune piste sans lui.
        """
        from src.controllers.peck_controller import PeckController

        data.loadSessionMetadata()
        self.assertNotEqual(data.mediaId, "")
        pecks = PeckController(self.measure, data, fish)
        pecks.refresh()
        return pecks

    def test_behavior_duration_and_three_points_are_independent(self):
        import fish_annotate as fa
        fish, data, ann_id, produced, _other = self._registry_fish_and_track()
        data.loadEventTypes()
        self._run_track_follow(fish, produced, behavior_key="grazing")
        self.assertEqual(fish.grazingWorkflowState, "success")
        intervals = fa.list_grazing_intervals(str(self.media))
        self.assertEqual(len(intervals), 1)
        self.assertEqual(intervals[0]["event_type"], "grazing")
        self.assertEqual(intervals[0]["track_db_id"], produced)
        self.assertEqual((intervals[0]["frame_start_abs"], intervals[0]["frame_end_abs"]), (3, 5))
        pecks = self._peck_controller(data, fish)
        pecks.selectTrack(produced, False)
        pecks.setTypeKey("bite")
        for frame in (1, 2, 3):
            self.measure.frameIndex = frame
            self.assertTrue(pecks.markAtCurrentFrame(), pecks.statusText)
        points = fa.list_behavior_points(str(self.media))
        self.assertEqual(len(points), 3)
        self.assertEqual({row["event_type"] for row in points}, {"bite"})
        self.assertEqual({row["track_db_id"] for row in points}, {produced})
        self.assertEqual(len(fa.list_observations(ann_id=ann_id)), 1)
        self.assertEqual(len(fa.list_grazing_intervals(str(self.media))), 1)

    def test_duration_uses_type_captured_at_in(self):
        import fish_annotate as fa
        fish, data, _ann, produced, _other = self._registry_fish_and_track()
        data.loadEventTypes()
        self.assertTrue(fish.beginBehaviorFollow("grazing"))
        data.addBehaviorType("Passage suivi", "interval")
        self.assertNotEqual(data.eventTypeKey, "grazing")
        self.measure.frameIndex = 3
        fish._tracker = _TrackerStub(produced)
        fish.finishTrackFollow()
        fish._assist_track_external_id = 7
        fish._assist_on_finished()
        self.assertEqual([r["event_type"] for r in fa.list_grazing_intervals(str(self.media))], ["grazing"])

    def test_cancelled_duration_does_not_leak_into_next_trajectory(self):
        import fish_annotate as fa
        fish, data, _ann, produced, _other = self._registry_fish_and_track()
        data.loadEventTypes()
        self.assertTrue(fish.beginBehaviorFollow("grazing"))
        fish.cancelGrazingAnalysis()
        self.assertEqual(fish.trackFollowBehaviorKey, "")
        self._run_track_follow(fish, produced)
        self.assertEqual(fa.list_grazing_intervals(str(self.media)), [])

    def test_duration_does_not_annotate_a_conflicting_track(self):
        import fish_annotate as fa
        fish, data, ann_id, produced, other = self._registry_fish_and_track()
        data.loadEventTypes()
        self._run_track_follow(fish, produced, behavior_key="grazing",
                               after_begin=lambda: data.attachObservationToTrack(ann_id, other))
        self.assertEqual(fish.grazingWorkflowState, "warning")
        self.assertEqual(fa.list_observations(ann_id=ann_id)[0]["track_id"], other)
        self.assertEqual(fa.list_grazing_intervals(str(self.media)), [])

    def test_add_and_remove_duration_on_existing_track_preserves_points(self):
        import fish_annotate as fa
        fish, data, ann_id, produced, _other = self._registry_fish_and_track()
        self._run_track_follow(fish, produced)
        pecks = self._peck_controller(data, fish)
        pecks.selectTrack(produced, False)
        pecks.setTypeKey("bite")
        self.measure.frameIndex = 1
        self.assertTrue(pecks.markAtCurrentFrame())
        with patch.object(fish, "_init_tracker", side_effect=AssertionError("Ne pas recalculer la piste")), \
             patch.object(fish, "_launch_assist_segment", side_effect=AssertionError("Ne pas relancer le suivi")):
            self.assertTrue(fish.beginBehaviorFollow("grazing"))
            self.measure.frameIndex = 9
            fish.finishTrackFollow()
            self.assertTrue(fish.trackFollowStartMarked)
            self.assertEqual(fa.list_grazing_intervals(str(self.media)), [])
            self.measure.frameIndex = 3
            fish.finishTrackFollow()
        self.assertEqual(fish.grazingWorkflowState, "success")
        self.assertEqual(fa.list_observations(ann_id=ann_id)[0]["track_id"], produced)
        self.assertEqual(len(fa.list_grazing_intervals(str(self.media))), 1)
        interval = next(row for row in data.selectedObservationEvents if row["scope"] == "interval")
        self.assertTrue(data.deleteSelectedDurationEvent(interval["eventId"]))
        self.assertEqual(fa.list_grazing_intervals(str(self.media)), [])
        self.assertEqual(len(fa.list_behavior_points(str(self.media))), 1)
        self.assertEqual(fa.list_observations(ann_id=ann_id)[0]["track_id"], produced)

    def test_instant_type_cannot_be_stretched_over_a_duration(self):
        fish, data, _ann, _produced, _other = self._registry_fish_and_track()
        data.loadEventTypes()
        self.assertFalse(fish.beginBehaviorFollow("bite"))
        self.assertFalse(fish.trackFollowActive)

    def test_track_follow_links_the_track_without_writing_any_interval(self):
        """La chaîne complète du client, sans broute imposée.

        « On sélectionne le poisson et on dit que le tracking concerne ce
        poisson, on sélectionne In et Out, ça traque tout le long. Ensuite,
        quand on fait avec les flèches image par image, on sélectionne ce
        tracking et là on va positionner : il broute, il broute, il broute. »

        Le chemin retiré, « Début / Fin et analyser », écrivait toujours un
        intervalle : il fallait inventer une broute pour obtenir la piste sur
        laquelle poser les bouchées. C'est désormais le seul geste de suivi.
        """
        import fish_annotate as fa

        fish, data, ann_id, produced, _other = self._registry_fish_and_track()
        self.assertTrue(fish.behaviorSeedReady)

        self._run_track_follow(fish, produced)

        # 1. La piste est rattachée au poisson mesuré.
        stored = fa.list_observations(ann_id=ann_id)[0]
        self.assertEqual(stored["track_id"], produced)
        self.assertEqual(stored["external_track_id"], 7)
        self.assertEqual(stored["measurement_mm"], 214.5)
        self.assertEqual(fish.grazingWorkflowState, "success")
        self.assertIn("Trajectoire", fish.grazingWorkflowMessage)

        # 2. AUCUN événement d'intervalle : c'est toute la différence.
        self.assertEqual(fa.list_grazing_intervals(str(self.media)), [])
        self.assertEqual(data.selectedObservationEvents, [])
        with session_scope() as session:
            from src.annodb.models import TemporalEvent

            self.assertEqual(session.query(TemporalEvent).count(), 0)

        # 3. Le suivi ne laisse rien derrière lui dans la machine à états, et
        #    il n'existe plus de second cycle susceptible de s'allumer.
        self.assertFalse(fish.trackFollowActive)
        self.assertFalse(fish.trackFollowStartMarked)
        for retire in (
            "beginGrazingAnalysis",
            "finishGrazingAndAnalyze",
            "setTrackInAtCurrent",
            "setTrackOutAtCurrent",
            "analyzeTrackingSegment",
            "trackInFrame",
            "trackOutFrame",
            "trackFollowMode",
            "grazingStartMarked",
        ):
            self.assertFalse(
                hasattr(fish, retire),
                f"« {retire} » appartient au chemin de suivi retiré",
            )

        # 4. Les bouchées se posent sur CETTE piste, sans rien choisir.
        pecks = self._peck_controller(data, fish)
        point_type = next(row for row in fa.list_point_event_types()
                          if row["key"] == "bite")
        pecks.loadPointTypes()
        pecks.selectTrack(produced, False)
        self.assertEqual(pecks.selectedTrackId, produced)
        for frame_index in (1, 2, 3):
            self.measure.frameIndex = frame_index
            self.assertTrue(pecks.markAtCurrentFrame(), pecks.statusText)
        self.assertEqual(pecks.markerCount, 3)
        points = fa.list_behavior_points(str(self.media))
        self.assertEqual([row["track_db_id"] for row in points], [produced] * 3)
        self.assertEqual([row["event_type"] for row in points],
                         [point_type["key"]] * 3)
        # Les bouchées sont des événements ponctuels : elles ne doivent pas
        # ressusciter un intervalle sur la fiche du poisson.
        self.assertEqual(fa.list_grazing_intervals(str(self.media)), [])

        # 5. Les quatre jalons du bandeau, à la source.
        data.refreshRegistry()
        row_index = next(
            i for i in range(data.registry.rowCount())
            if data.registry.row_at(i).get("ann_id") == ann_id
        )
        data.loadRegistryRow(row_index)
        self.assertEqual(data.selectedTrackDbId, produced)
        self.assertEqual(data.selectedTrackNumber, 7)
        self.assertEqual(data.editMeasurementMm, 214.5)
        peck_row = next(
            row for row in pecks.tracks if row["trackId"] == produced
        )
        self.assertEqual(peck_row["markerCount"], 3)

    def test_two_successive_follows_never_write_an_interval(self):
        """Le geste conservé reste muet, même répété.

        Le cycle « Comportement » écrivait un intervalle à chaque fin de
        suivi. Il a été retiré : suivre deux fois le même poisson ne doit
        produire que des pistes, jamais un TemporalEvent.
        """
        import fish_annotate as fa

        fish, data, ann_id, produced, _other = self._registry_fish_and_track()
        self._run_track_follow(fish, produced)
        self.assertEqual(fa.list_grazing_intervals(str(self.media)), [])

        # Même poisson, même piste, un second aller-retour In/Out.
        self.measure.frameIndex = 1
        data.focusSelectedObservation()
        self._run_track_follow(fish, produced)

        self.assertEqual(fa.list_grazing_intervals(str(self.media)), [])
        self.assertEqual(data.selectedObservationEvents, [])
        with session_scope() as session:
            from src.annodb.models import TemporalEvent

            self.assertEqual(session.query(TemporalEvent).count(), 0)
        self.assertEqual(fa.list_observations(ann_id=ann_id)[0]["track_id"], produced)
        self.assertEqual(fish.grazingWorkflowState, "success")

    def test_track_follow_refuses_a_fish_that_has_no_registry_line(self):
        """Une piste rattachée à rien reproduirait la coupure à réparer."""
        import fish_annotate as fa

        fish = FishController(self.measure)
        data = DataController(self.measure, fish)
        fish.set_data_controller(data)
        fish._last_boxes = [dict(self.box)]
        fish._publish_overlay()
        fish.setSelectedFishIndex(0)
        self.assertTrue(fish.behaviorSeedReady)

        fish.beginTrackFollow()
        self.assertEqual(fish.grazingWorkflowState, "error")
        self.assertIn("Enregistrez d'abord ce poisson", fish.grazingWorkflowMessage)
        self.assertFalse(fish.trackFollowStartMarked)
        self.assertFalse(fish.trackFollowActive)

        # Rien n'a été écrit : ni piste, ni événement, ni observation.
        self.assertEqual(fa.list_observations(media_path=str(self.media)), [])
        with session_scope() as session:
            from src.annodb.models import TemporalEvent, Track

            self.assertEqual(session.query(Track).count(), 0)
            self.assertEqual(session.query(TemporalEvent).count(), 0)

    def test_a_second_in_never_destroys_the_one_already_posted(self):
        """Un seul cycle : un « In » de plus ne doit pas effacer le premier.

        Deux cycles se partageaient la machine à états et pouvaient s'écraser
        l'un l'autre ; il n'en reste qu'un, mais le garde-fou vaut toujours
        pour un double clic ou un « In » posé depuis l'autre volet.
        """
        fish, _data, _ann_id, produced, _other = self._registry_fish_and_track()

        fish.beginTrackFollow()
        self.assertTrue(fish.trackFollowStartMarked)
        self.assertTrue(fish.trackFollowActive)
        start = fish.grazingStartFrame

        self.measure.frameIndex = 4
        fish.beginTrackFollow()
        self.assertTrue(fish.trackFollowStartMarked, "le In a été perdu")
        self.assertEqual(fish.grazingStartFrame, start, "le In a été déplacé")
        self.assertIn("suivi est déjà engagé", fish.statusText)

        # Rien n'est parti au tracker tant que « Out » n'a pas été posé.
        fish.cancelGrazingAnalysis()
        self.assertFalse(fish.trackFollowActive)
        self.assertEqual(len(_TrackerStub(produced).follow_calls), 0)

    def test_cancelling_a_track_follow_writes_nothing_at_all(self):
        import fish_annotate as fa

        fish, _data, ann_id, produced, _other = self._registry_fish_and_track()

        fish.beginTrackFollow()
        self.measure.frameIndex = 3
        fish._tracker = _TrackerStub(produced)
        fish.finishTrackFollow()
        fish.cancelGrazingAnalysis()

        self.assertFalse(fish.trackFollowActive)
        self.assertEqual(fish.grazingWorkflowState, "idle")
        self.assertIn("aucune piste enregistrée", fish.grazingWorkflowMessage)
        self.assertIsNone(fa.list_observations(ann_id=ann_id)[0]["track_id"])
        self.assertEqual(fa.list_grazing_intervals(str(self.media)), [])

    def test_short_path_measure_only_leaves_track_and_events_empty(self):
        """« Si on veut juste mesurer le poisson on peut aussi. »"""
        import fish_annotate as fa

        fish = _FishStub([self.box])
        data = DataController(self.measure, fish)
        data.selectBoxExplicit(0)
        self.assertTrue(data.draftActive)
        self.measure.distanceMm = 154.2
        data.onStereoMeasureChanged()
        with patch(
            "src.controllers.data_controller.threading.Thread", _ImmediateThread,
        ):
            data.saveDraftObservation()

        stored = fa.list_observations(ann_id=data.selectedAnnId)[0]
        self.assertAlmostEqual(stored["measurement_mm"], 154.2)
        self.assertIsNone(stored["track_id"])
        self.assertEqual(stored["behaviors"], [])
        # Les deux jalons de droite restent éteints, sans que rien ne manque.
        self.assertEqual(data.selectedTrackDbId, "")
        self.assertEqual(data.selectedTrackNumber, -1)
        self.assertEqual(data.selectedObservationEvents, [])

    def test_short_path_measure_plus_instant_flag_needs_no_track(self):
        """« Ou juste mesurer et mettre un événement, c'est bon aussi. »"""
        import fish_annotate as fa

        fish = _FishStub([self.box])
        data = DataController(self.measure, fish)
        self.assertTrue(data.addBehaviorType("Fuite", "instant"))
        data.selectBoxExplicit(0)
        self.measure.distanceMm = 154.2
        data.onStereoMeasureChanged()
        with patch(
            "src.controllers.data_controller.threading.Thread", _ImmediateThread,
        ):
            data.saveDraftObservation()
        ann_id = data.selectedAnnId
        self.assertNotEqual(ann_id, "")

        flag_index = next(
            i for i, row in enumerate(data._event_types)
            if row.get("key") == "fuite"
        )
        data.setEventTypeIndex(flag_index)
        self.assertTrue(data.toggleSelectedBehavior())

        stored = fa.list_observations(ann_id=ann_id)[0]
        self.assertAlmostEqual(stored["measurement_mm"], 154.2)
        self.assertIsNone(stored["track_id"])
        self.assertEqual([row["key"] for row in stored["behaviors"]], ["fuite"])
        self.assertEqual(data.selectedTrackDbId, "")
        self.assertEqual(data.selectedTrackNumber, -1)
        self.assertEqual(
            [(item["label"], item["scope"])
             for item in data.selectedObservationEvents],
            [("Fuite", "instant")],
        )
        # Aucune piste n'a été créée pour autant.
        with session_scope() as session:
            from src.annodb.models import Track

            self.assertEqual(session.query(Track).count(), 0)

    def test_attach_refuses_unknown_observation_without_touching_the_base(self):
        import fish_annotate as fa

        fish, data, ann_id, produced, _other = self._registry_fish_and_track()

        self.assertEqual(data.attachObservationToTrack("", produced), "missing")
        self.assertEqual(data.attachObservationToTrack(ann_id, ""), "missing")
        self.assertEqual(
            data.attachObservationToTrack(ann_id, "piste-inconnue"), "error",
        )
        self.assertIsNone(fa.list_observations(ann_id=ann_id)[0]["track_id"])

        self.assertEqual(data.attachObservationToTrack(ann_id, produced), "linked")
        self.assertEqual(data.attachObservationToTrack(ann_id, produced), "already")

    def test_data_controller_has_no_direct_interval_persistence_path(self):
        import fish_annotate as fa

        data = DataController(self.measure, _FishStub([self.box]))
        with patch.object(fa, "add_grazing_interval") as add_interval:
            for legacy_name in (
                "grazeStart",
                "grazeEnd",
                "grazeCancel",
                "_graze_mark",
                "pendingGrazeStart",
            ):
                with self.assertRaises(AttributeError):
                    getattr(data, legacy_name)
            add_interval.assert_not_called()

    def test_multiple_boxes_require_an_explicit_choice(self):
        fish = _FishStub([self.box, {**self.box, "x1": 65.0, "x2": 90.0}])
        data = DataController(self.measure, fish)
        selected: list[int] = []
        data._add_observation_at_index = selected.append

        data.addObservationFromSelectedBox()

        self.assertEqual(selected, [])
        self.assertIn("Plusieurs cadres", data.statusText)

    def test_focused_row_replaces_old_selection_even_without_iou(self):
        fish = FishController(self.measure)
        data = DataController(self.measure, fish)
        fish.set_data_controller(data)
        other_box = {**self.box, "x1": 65.0, "x2": 90.0}
        fish._replace_last_boxes([self.box, other_box])
        fish._publish_overlay()
        rows = [
            {
                "ann_id": "ann-a", "frame_index": 3, "frame_ref": "absolute",
                "geometry": {"x_min": 10.0, "y_min": 20.0, "x_max": 60.0, "y_max": 70.0},
            },
            {
                "ann_id": "ann-b", "frame_index": 3, "frame_ref": "absolute",
                "geometry": {"x_min": 1.0, "y_min": 1.0, "x_max": 8.0, "y_max": 8.0},
            },
        ]
        data.registry.set_rows(rows)
        data.loadRegistryRow(0)
        fish.setSelectedFishIndex(0)
        data.selectBoxIndex(0)

        data.focusRegistryRow(1)

        self.assertEqual(data.selectedAnnId, "ann-b")
        self.assertEqual(data.selectedBoxIndex, -1)
        self.assertEqual(fish.selectedFishIndex, -1)
        target, index = fish._measure_target_box()
        self.assertEqual(index, -1)
        self.assertEqual(
            (target["x1"], target["y1"], target["x2"], target["y2"]),
            (1.0, 1.0, 8.0, 8.0),
        )

    def test_multiple_boxes_without_selection_or_focus_block_measurement(self):
        fish = FishController(self.measure)
        data = DataController(self.measure, fish)
        fish.set_data_controller(data)
        fish._replace_last_boxes([self.box, {**self.box, "x1": 65.0, "x2": 90.0}])
        fish._publish_overlay()

        fish.measureSelectedBoxLength()

        self.assertIn("Selectionnez une ligne", fish.statusText)

    def test_busy_guard_is_shared_by_button_right_click_and_python(self):
        fish = _FishStub([self.box])
        data = DataController(self.measure, fish)
        _BlockedThread.instances.clear()
        result = {
            "out": {"ann_id": "ann-1", "measurement_mm": None},
            "manual": True,
            "species": "?",
            "measurement_target": None,
        }

        with (
            patch("src.controllers.data_controller.threading.Thread", _BlockedThread),
            patch.object(data, "_db_add_observation", return_value=result) as write,
        ):
            data.addObservationFromSelectedBox()
            self.assertTrue(data.busy)
            data.addObservationAtBoxIndex(0)
            data._add_observation_at_index(0)
            self.assertEqual(len(_BlockedThread.instances), 1)
            self.assertEqual(write.call_count, 0)
            _BlockedThread.instances[0].run()

        self.assertEqual(write.call_count, 1)
        self.assertFalse(data.busy)

    def test_stereo_measure_is_bound_to_bbox_and_absolute_frame(self):
        import fish_annotate as fa

        other_box = {**self.box, "x1": 65.0, "x2": 90.0}
        fish = _FishStub([self.box, other_box])
        data = DataController(self.measure, fish)

        data.selectBoxIndex(0)
        self.measure.distanceMm = 111.0
        data.onStereoMeasureChanged()
        self.assertAlmostEqual(data.pendingStereoMeasureMm, 111.0)

        # Changer de poisson invalide la mesure A : B est enregistré sans elle.
        data.selectBoxIndex(1)
        with patch(
            "src.controllers.data_controller.threading.Thread", _ImmediateThread,
        ):
            data.addObservationFromSelectedBox()

        self.measure.frameIndex = 2
        self.measure.frameIndexChanged.emit()
        data.selectBoxIndex(0)
        self.measure.distanceMm = 222.0
        data.onStereoMeasureChanged()
        with patch(
            "src.controllers.data_controller.threading.Thread", _ImmediateThread,
        ):
            data.addObservationFromSelectedBox()

        rows = fa.list_observations(media_path=str(self.media))
        self.assertEqual(len(rows), 2)
        by_frame = {row["frame_index"]: row for row in rows}
        self.assertIsNone(by_frame[3]["measurement_mm"])
        self.assertAlmostEqual(by_frame[4]["measurement_mm"], 222.0)
        self.assertEqual(by_frame[3]["geometry"]["x_min"], 65.0)
        self.assertEqual(by_frame[4]["geometry"]["x_min"], 10.0)
        self.assertEqual(data.pendingStereoMeasureMm, 0.0)

    def test_stereo_signal_persists_selected_observation_and_cleans_row(self):
        import fish_annotate as fa

        fish = _FishStub([self.box])
        data = DataController(self.measure, fish)
        with patch(
            "src.controllers.data_controller.threading.Thread", _ImmediateThread,
        ):
            data.addObservationFromSelectedBox()

        self.measure.distanceMm = 164.5
        with patch(
            "src.controllers.data_controller.threading.Thread", _ImmediateThread,
        ):
            data.onStereoMeasureChanged()

        row = fa.list_observations(media_path=str(self.media))[0]
        self.assertAlmostEqual(row["measurement_mm"], 164.5)
        self.assertAlmostEqual(data.editMeasurementMm, 164.5)
        self.assertFalse(data.registryRowDirty)
        self.assertEqual(data.pendingStereoMeasureMm, 0.0)

    def test_failed_stereo_persistence_leaves_row_dirty_and_not_ready(self):
        import fish_annotate as fa

        fish = _FishStub([self.box])
        data = DataController(self.measure, fish)
        with patch(
            "src.controllers.data_controller.threading.Thread", _ImmediateThread,
        ):
            data.addObservationFromSelectedBox()

        self.measure.distanceMm = 173.0
        with (
            patch("fish_annotate.update_observation", side_effect=OSError("DB occupée")),
            patch("src.controllers.data_controller.threading.Thread", _ImmediateThread),
        ):
            data.onStereoMeasureChanged()

        row = fa.list_observations(media_path=str(self.media))[0]
        self.assertIsNone(row["measurement_mm"])
        self.assertAlmostEqual(data.editMeasurementMm, 173.0)
        self.assertTrue(data.registryRowDirty)
        self.assertAlmostEqual(data.pendingStereoMeasureMm, 173.0)

    def test_sparse_measure_keeps_the_draft_and_its_corrected_species(self):
        import fish_annotate as fa
        import numpy as np

        fa.create_annotator("Mesure test")
        fish = FishController(self.measure)
        data = DataController(self.measure, fish)
        fish.set_data_controller(data)
        fish._replace_last_boxes([self.box])
        fish._publish_overlay()
        data.selectBoxExplicit(0)
        data.setSpeciesFilter("Scardinius erythrophthalmus")
        self.measure.distanceMmChanged.connect(data.onStereoMeasureChanged)
        image = np.zeros((80, 100, 3), dtype=np.uint8)
        projection = np.eye(3, 4, dtype=float)
        self.measure.rectified_pair = lambda _index: (image, image, projection, projection)
        # Un rechargement de calibration déclenche de nouvelles détections,
        # qui remplacent les cadres sur lesquels le brouillon était fondé.
        self.measure.loadCalibration = lambda: fish._replace_last_boxes([dict(self.box)])
        result = {"pixel_a": (12.0, 25.0), "pixel_b": (58.0, 65.0), "length_mm": 91.2}
        with (
            patch("fish_sparse_measure.measure_bbox_length_mm", return_value=result),
            patch("stereo_utils.stereo_match_disparity_rect", return_value=None),
            patch("src.controllers.data_controller.threading.Thread", _ImmediateThread),
        ):
            fish.measureSelectedBoxLength()
            self.assertTrue(data.draftActive)
            self.assertEqual(data.editSpecies, "Scardinius erythrophthalmus")
            self.assertAlmostEqual(data.pendingStereoMeasureMm, 91.2, msg=(fish.statusText, data.statusText, self.measure.distanceMm, data.selectedBoxIndex))
            data.saveFish(data.editFamily, data.editGenus, data.editSpecies)
        rows = fa.list_observations(media_path=str(self.media))
        self.assertEqual(len(rows), 1)
        self.assertAlmostEqual(rows[0]["measurement_mm"], 91.2)

    def test_sparse_measure_right_point_fallback_is_persisted(self):
        import fish_annotate as fa
        import numpy as np

        data = DataController(self.measure, _FishStub([self.box]))
        with patch(
            "src.controllers.data_controller.threading.Thread", _ImmediateThread,
        ):
            data.addObservationFromSelectedBox()

        fish = FishController(self.measure)
        fish.set_data_controller(data)
        data._fish = fish
        fish._replace_last_boxes([self.box])
        fish.setSelectedFishIndex(0)
        data.selectBoxIndex(0)
        self.measure.distanceMmChanged.connect(data.onStereoMeasureChanged)
        image = np.zeros((80, 100, 3), dtype=np.uint8)
        projection = np.eye(3, 4, dtype=float)
        self.measure.rectified_pair = lambda _index: (
            image, image, projection, projection,
        )

        result = {
            "pixel_a": (12.0, 25.0),
            "pixel_b": (58.0, 65.0),
            "length_mm": 91.2,
        }
        with (
            patch("fish_sparse_measure.measure_bbox_length_mm", return_value=result),
            patch("stereo_utils.stereo_match_disparity_rect", return_value=None),
            patch("src.controllers.data_controller.threading.Thread", _ImmediateThread),
        ):
            fish.measureSelectedBoxLength()

        row = fa.list_observations(media_path=str(self.media))[0]
        self.assertAlmostEqual(row["measurement_mm"], 91.2)
        self.assertFalse(data.registryRowDirty)

    def test_measurement_persistence_returns_immediately_while_sqlite_is_blocked(self):
        import fish_annotate as fa

        data = DataController(self.measure, _FishStub([self.box]))
        with patch("src.controllers.data_controller.threading.Thread", _ImmediateThread):
            data.addObservationFromSelectedBox()
        real_update = fa.update_observation
        entered = threading.Event()
        release = threading.Event()

        def blocked_update(*args, **kwargs):
            entered.set()
            release.wait(3.0)
            return real_update(*args, **kwargs)

        self.addCleanup(release.set)
        with patch("fish_annotate.update_observation", side_effect=blocked_update):
            self.measure.distanceMm = 143.0
            started = time.monotonic()
            data.onStereoMeasureChanged()
            elapsed = time.monotonic() - started
            self.assertTrue(entered.wait(1.0))
            self.assertLess(elapsed, 0.2)
            self.assertTrue(data.registryRowDirty)
            release.set()
            self._wait_until(lambda: not data.registryRowDirty)

        self.assertAlmostEqual(
            fa.list_observations(media_path=str(self.media))[0]["measurement_mm"],
            143.0,
        )

    def test_stale_measurement_result_does_not_replace_new_target(self):
        import fish_annotate as fa

        fish = _FishStub([self.box, {**self.box, "x1": 65.0, "x2": 90.0}])
        data = DataController(self.measure, fish)
        with patch("src.controllers.data_controller.threading.Thread", _ImmediateThread):
            data.selectBoxIndex(0)
            data.addObservationFromSelectedBox()
            data.selectBoxIndex(1)
            data.addObservationFromSelectedBox()
        rows = fa.list_observations(media_path=str(self.media))
        first_id = next(row["ann_id"] for row in rows if row["geometry"]["x_min"] == 10.0)
        second_id = next(row["ann_id"] for row in rows if row["geometry"]["x_min"] == 65.0)
        data.loadRegistryRow(data._registry_index_by_ann_id(first_id))
        data.selectBoxIndex(0)
        real_update = fa.update_observation
        entered = threading.Event()
        release = threading.Event()
        completed = threading.Event()

        def blocked_update(*args, **kwargs):
            entered.set()
            release.wait(3.0)
            result = real_update(*args, **kwargs)
            completed.set()
            return result

        self.addCleanup(release.set)
        with patch("fish_annotate.update_observation", side_effect=blocked_update):
            self.measure.distanceMm = 155.0
            data.onStereoMeasureChanged()
            self.assertTrue(entered.wait(1.0))
            data.loadRegistryRow(data._registry_index_by_ann_id(second_id))
            data.selectBoxIndex(1)
            selected_before = data.selectedAnnId
            status_before = data.statusText
            release.set()
            self.assertTrue(completed.wait(2.0))
            self._wait_until(lambda: data.selectedAnnId == second_id)
            self.app.processEvents()

        self.assertEqual(selected_before, second_id)
        self.assertEqual(data.selectedAnnId, second_id)
        self.assertEqual(data.statusText, status_before)
        self.assertEqual(data.editMeasurementMm, 0.0)

    def test_measurement_queue_commits_latest_request_last(self):
        import fish_annotate as fa

        data = DataController(self.measure, _FishStub([self.box]))
        with patch("src.controllers.data_controller.threading.Thread", _ImmediateThread):
            data.addObservationFromSelectedBox()
        ann_id = data.selectedAnnId
        real_update = fa.update_observation
        first_entered = threading.Event()
        release_first = threading.Event()
        calls: list[float] = []

        def ordered_update(target_id, **kwargs):
            value = float(kwargs["measurement_mm"])
            calls.append(value)
            if value == 100.0:
                first_entered.set()
                release_first.wait(3.0)
            return real_update(target_id, **kwargs)

        self.addCleanup(release_first.set)
        with patch("fish_annotate.update_observation", side_effect=ordered_update):
            data.applyMeasurementToSelectedRow(100.0)
            self.assertTrue(first_entered.wait(1.0))
            data.applyMeasurementToSelectedRow(200.0)
            release_first.set()
            self._wait_until(
                lambda: ann_id not in data._measurement_pending_ann_ids,
            )

        self.assertEqual(calls, [100.0, 200.0])
        row = next(r for r in fa.list_observations(media_path=str(self.media))
                   if r["ann_id"] == ann_id)
        self.assertAlmostEqual(row["measurement_mm"], 200.0)
        registry_row = data.registry.row_at(data._registry_index_by_ann_id(ann_id))
        self.assertAlmostEqual(registry_row["measurement_mm"], 200.0)
        with session_scope(self.db_path) as session:
            run = export_coco(
                session,
                self.tmp_path / "queue_export",
                split_by="media",
                taxonomy_rank="fish",
                rectify_images=False,
                min_instances=1,
                min_media=1,
            )
        coco = json.loads(
            (Path(run.output_path) / "instances_fish.json").read_text(encoding="utf-8")
        )
        exported = next(
            row for row in coco["annotations"]
            if row["attributes"]["spatial_annotation_id"] == ann_id
        )
        self.assertAlmostEqual(exported["attributes"]["measurement_mm"], 200.0)

    def test_focused_saved_bbox_beats_close_iou_detection_for_measurement(self):
        import fish_annotate as fa

        fish = FishController(self.measure)
        data = DataController(self.measure, fish)
        fish.set_data_controller(data)
        data._fish = fish
        fish._replace_last_boxes([self.box])
        fish._publish_overlay()
        with patch("src.controllers.data_controller.threading.Thread", _ImmediateThread):
            data.selectBoxIndex(0)
            data.addObservationFromSelectedBox()
        ann_id = data.selectedAnnId

        close_detection = {**self.box, "x1": 12.0, "x2": 62.0}
        fish._replace_last_boxes([close_detection])
        fish._publish_overlay()
        data.focusRegistryRow(data._registry_index_by_ann_id(ann_id))
        self.assertEqual(data.selectedBoxIndex, 0)  # IoU proche sélectionné visuellement.

        self.measure.distanceMm = 219.0
        with patch("src.controllers.data_controller.threading.Thread", _ImmediateThread):
            data.onStereoMeasureChanged()

        row = next(r for r in fa.list_observations(media_path=str(self.media))
                   if r["ann_id"] == ann_id)
        self.assertAlmostEqual(row["measurement_mm"], 219.0)
        self.assertEqual(row["geometry"]["x_min"], 10.0)

    def test_last_explicit_click_b_replaces_focus_a_and_inserts_b(self):
        import fish_annotate as fa

        box_a = dict(self.box)
        box_b = {**self.box, "x1": 65.0, "x2": 95.0}
        fish = FishController(self.measure)
        data = DataController(self.measure, fish)
        fish.set_data_controller(data)
        data._fish = fish
        fish._replace_last_boxes([box_a, box_b])
        fish._publish_overlay()

        with patch("src.controllers.data_controller.threading.Thread", _ImmediateThread):
            data.selectBoxExplicit(0)
            self.measure.distanceMm = 111.0
            data.onStereoMeasureChanged()
            data.addObservationFromSelectedBox()
        ann_a = data.selectedAnnId
        row_a_index = data._registry_index_by_ann_id(ann_a)
        data.focusRegistryRow(row_a_index)
        self.assertTrue(fish.focusBox.get("valid"))

        # C'est le geste utilisateur sur B, pas l'ancien focus registre, qui
        # verrouille désormais les quatre points et la future insertion.
        data.selectBoxExplicit(1)
        self.assertEqual(data.selectedAnnId, "")
        self.assertFalse(fish.focusBox.get("valid"))
        self.measure.distanceMm = 222.0
        data.onStereoMeasureChanged()
        self.assertEqual(data._pending_stereo_target["bbox"], data._bbox_signature(box_b))
        self.assertNotIn("ann_id", data._pending_stereo_target)
        with patch("src.controllers.data_controller.threading.Thread", _ImmediateThread):
            data.addObservationFromSelectedBox()

        rows = fa.list_observations(media_path=str(self.media))
        self.assertEqual(len(rows), 2)
        persisted_a = next(row for row in rows if row["ann_id"] == ann_a)
        persisted_b = next(row for row in rows if row["ann_id"] != ann_a)
        self.assertAlmostEqual(persisted_a["measurement_mm"], 111.0)
        self.assertAlmostEqual(persisted_b["measurement_mm"], 222.0)
        self.assertEqual(persisted_b["geometry"]["x_min"], 65.0)

    def test_controller_conflicting_taxa_keep_track_ambiguous(self):
        import fish_annotate as fa

        from src.annodb.export_core import build_class_plan_for_tracks
        from src.annodb.models import MediaAsset, Track, TrackSample
        from src.annodb.session_stats import (
            compute_session_stats,
            iter_session_timeline_rows,
        )
        from src.annodb.tracks import get_or_create_track

        media_id = fa.resolve_media_id(str(self.media), create=True)
        with session_scope(self.db_path) as db:
            track_id = get_or_create_track(
                db, media_id=media_id, external_track_id=44, source="manual",
            ).id
        ann_a = fa.add_observation(
            str(self.media), 3, dict(self.box), source="manual",
            track_id=track_id, frame_ref="absolute",
        )["ann_id"]
        ann_b = fa.add_observation(
            str(self.media), 4, {**self.box, "x1": 20.0, "x2": 70.0},
            source="manual", track_id=track_id, frame_ref="absolute",
        )["ann_id"]
        fa.update_observation(
            ann_a, family_text="Scaridae", genus_text="Scarus",
            species_text="Scarus ghobban", reviewed_by="Test",
        )

        data = DataController(self.measure, _FishStub([self.box]))
        data.refreshRegistry()
        _BlockedThread.instances.clear()
        with patch("src.controllers.data_controller.threading.Thread", _BlockedThread):
            data.loadRegistryRow(data._registry_index_by_ann_id(ann_b))
            data.saveSelectedRow("Scaridae", "Scarus", "Scarus niger")

        with session_scope(self.db_path) as db:
            track = db.get(Track, track_id)
            self.assertIsNone(track.taxon_node_id)
            self.assertEqual(track.identification_status, "ambiguous")
            db.add(TrackSample(
                track_id=track_id,
                frame_index=3,
                cx=0.35,
                cy=0.45,
                bbox_json=json.dumps({
                    "x1": 10.0, "y1": 20.0, "x2": 60.0, "y2": 70.0,
                }),
            ))
            db.flush()
            media = db.get(MediaAsset, media_id)
            stats = compute_session_stats(db, media)
            timeline = list(iter_session_timeline_rows(db, media))
            plan = build_class_plan_for_tracks(
                db, [track], "species", min_instances=1, min_media=1,
            )
        self.assertEqual(stats["species_count"], 0)
        self.assertEqual(stats["max_per_species"], [])
        self.assertEqual(len(timeline), 1)
        self.assertEqual(
            [timeline[0][rank] for rank in ("family", "genus", "species")],
            ["NA", "NA", "NA"],
        )
        self.assertIsNone(plan.class_of_annotation.get(track_id))

    def test_fishial_session_promotion_exposes_running_success_and_error(self):
        fish = FishController(self.measure)
        data = DataController(self.measure, fish)
        fish.set_data_controller(data)
        data._fish = fish
        data._selected_ann_id = "ann-session"
        data._fishial_session_result = {"eligible": True}
        states = []
        data.fishialSessionChanged.connect(
            lambda: states.append(data.fishialSessionState)
        )

        success = {
            "ok": True, "eligible": True, "taxon_name": "Aqua testensis",
            "scope_label": "Session test",
            "candidates": 14, "exploitable": 14, "added": 14,
            "existing": 0, "rejected": 0,
        }
        with (
            patch("fishial_gallery.promote_session_references", return_value=success),
            patch("src.controllers.data_controller.threading.Thread", _ImmediateThread),
        ):
            data.promoteSelectedSessionToGallery()
        self.assertIn("running", states)
        self.assertEqual(data.fishialSessionState, "success")
        self.assertEqual(data.fishialSessionAdded, 14)
        self.assertIn("14 ajoutée", data.statusText)

        states.clear()
        unavailable = {
            "ok": False,
            "error": "Classificateur Fishial indisponible dans cet environnement",
        }
        with (
            patch("fishial_gallery.promote_session_references", return_value=unavailable),
            patch("src.controllers.data_controller.threading.Thread", _ImmediateThread),
        ):
            data.promoteSelectedSessionToGallery()
        self.assertIn("running", states)
        self.assertEqual(data.fishialSessionState, "error")
        self.assertIn("indisponible", data.fishialSessionMessage)
        self.assertEqual(data.fishialSessionSpecies, "Aqua testensis")
        self.assertEqual(data.fishialSessionScope, "Session test")
        self.assertEqual(data.fishialSessionCandidates, 14)
        self.assertEqual(data.fishialSessionExploitable, 14)

    def test_fishial_selection_resets_success_and_error_before_preview(self):
        fish = FishController(self.measure)
        data = DataController(self.measure, fish)
        fish.set_data_controller(data)
        for previous_state in ("success", "error"):
            data._fishial_session_state = previous_state
            data._fishial_session_result = {
                "selected_annotation_id": "ann-a",
                "eligible": True,
                "added": 14,
                "existing": 3,
                "error": "ancienne erreur" if previous_state == "error" else "",
            }
            data._selected_ann_id = "ann-b"
            data._selected_row_data = {
                "identification_status": "identified",
                "species_id": "species-b",
            }
            _BlockedThread.instances.clear()
            with patch(
                "src.controllers.data_controller.threading.Thread", _BlockedThread,
            ):
                data.refreshFishialSessionPreview()
            self.assertEqual(data.fishialSessionState, "idle")
            self.assertEqual(data.fishialSessionPreviewState, "running")
            self.assertEqual(data.fishialSessionAdded, 0)
            self.assertEqual(data.fishialSessionExisting, 0)
            self.assertEqual(data.fishialSessionMessage, "Enrichissement few-shot local : aucun réentraînement du modèle Fishial.")

            generation = data._fishial_generation
            data._finish_fishial_preview({
                "generation": generation,
                "selected_annotation_id": "ann-b",
                "ok": True,
                "eligible": True,
                "taxon_name": "Aqua beta",
                "candidates": 2,
                "exploitable": 2,
                "existing": 0,
                "rejected": 0,
            })
            self.assertEqual(data.fishialSessionPreviewState, "idle")
            self.assertEqual(data.fishialSessionState, "idle")
            self.assertEqual(data.fishialSessionSpecies, "Aqua beta")

    def test_explorer_fallback_selection_rejects_stale_success_and_error(self):
        data = DataController(self.measure, _FishStub([self.box]))
        row_b = {
            "ann_id": "ann-b", "identification_status": "identified",
            "species_id": "species-b", "species": "Aqua beta",
            "frame_index": 3,
        }
        for stale_ok in (True, False):
            data._selected_ann_id = "ann-a"
            data._fishial_session_state = "success" if stale_ok else "error"
            data._fishial_session_result = {
                "selected_annotation_id": "ann-a", "added": 14,
                "error": "ancienne erreur" if not stale_ok else "",
            }
            old_generation = data._fishial_generation
            _BlockedThread.instances.clear()
            with patch(
                "src.controllers.data_controller.threading.Thread", _BlockedThread,
            ):
                # Chemin de repli réellement appelé par DbExplorer quand B
                # n'appartient pas au registre limité au média gauche.
                data.focus_observation_row(row_b)
            self.assertEqual(data.selectedAnnId, "ann-b")
            self.assertEqual(data.fishialSessionState, "idle")
            self.assertEqual(data.fishialSessionPreviewState, "running")
            self.assertEqual((data.fishialSessionAdded, data.fishialSessionExisting), (0, 0))
            generation_b = data._fishial_generation
            self.assertGreater(generation_b, old_generation)

            data._finish_fishial_preview({
                "generation": old_generation,
                "selected_annotation_id": "ann-a",
                "ok": stale_ok,
                "eligible": stale_ok,
                "taxon_name": "Aqua alpha",
                "added": 99,
                "error": "retour A" if not stale_ok else "",
            })
            self.assertEqual(data.fishialSessionPreviewState, "running")
            self.assertEqual(data.fishialSessionSpecies, "")

            data._finish_fishial_preview({
                "generation": generation_b,
                "selected_annotation_id": "ann-b",
                "ok": False,
                "eligible": False,
                "error": "aperçu B impossible",
            })
            self.assertEqual(data.fishialSessionPreviewState, "error")
            self.assertEqual(data.fishialSessionMessage, "aperçu B impossible")

    def test_new_observation_selection_resets_and_refreshes_fishial_preview(self):
        data = DataController(self.measure, _FishStub([self.box]))
        data._fishial_session_state = "success"
        data._fishial_session_result = {"added": 14, "existing": 3}
        generation_a = data._fishial_generation
        with patch("src.controllers.data_controller.threading.Thread", _ImmediateThread):
            data.addObservationFromSelectedBox()
        self.assertTrue(data.selectedAnnId)
        self.assertGreater(data._fishial_generation, generation_a)
        self.assertEqual(data.fishialSessionState, "idle")
        self.assertEqual(data.fishialSessionPreviewState, "error")
        self.assertEqual((data.fishialSessionAdded, data.fishialSessionExisting), (0, 0))
        self.assertIn("non relue", data.fishialSessionMessage)

    def test_fishial_rejection_reasons_are_all_actionable_french(self):
        data = DataController(self.measure, _FishStub([self.box]))
        reasons = {
            "photo_espace_rectifie_incompatible": "photo déclarée rectifiée",
            "media_droit_non_pris_en_charge": "média droit non pris en charge",
            "espace_geometrie_incompatible": "espace de géométrie incompatible",
            "media_gauche_session_requise": "média gauche de la session requis",
            "calibration_annotation_absente": "calibration de l'annotation absente",
            "calibration_indisponible": "calibration indisponible",
            "calibration_incompatible": "calibration incompatible",
        }
        data._fishial_session_result = {
            "rejection_reasons": {key: 1 for key in reasons},
        }
        message = data.fishialSessionMessage
        for technical, french in reasons.items():
            self.assertNotIn(technical, message)
            self.assertIn(french, message)

    def test_zero_active_promotion_is_controller_error_even_if_backend_says_ok(self):
        data = DataController(self.measure, _FishStub([self.box]))
        data._selected_ann_id = "ann-session"
        data._fishial_generation = 4
        data._finish_fishial_promotion({
            "generation": 4, "selected_annotation_id": "ann-session",
            "ok": True, "added": 0, "existing": 0, "rejected": 14,
        })
        self.assertEqual(data.fishialSessionState, "error")
        self.assertIn("Aucune référence Fishial active", data.statusText)

    def test_project_fishial_promotion_is_async_and_reports_counters(self):
        data = DataController(self.measure, _FishStub([self.box]))
        data._gallery.set_rows([{
            "taxon_node_id": "species-project",
            "scientific_name": "Aqua projecta",
            "crop_count": 14,
        }])
        _BlockedThread.instances.clear()
        with patch("src.controllers.data_controller.threading.Thread", _BlockedThread):
            started = time.monotonic()
            data.promoteGalleryTaxonAt(0)
            elapsed = time.monotonic() - started
        self.assertLess(elapsed, 0.1)
        self.assertEqual(data.fishialProjectState, "running")
        self.assertEqual(len(_BlockedThread.instances), 1)

        success = {
            "ok": True, "added": 8, "existing": 5, "rejected": 1,
        }
        with patch("fishial_gallery.promote_taxon_to_gallery", return_value=success):
            _BlockedThread.instances[0].run()
        self.assertEqual(data.fishialProjectState, "success")
        self.assertEqual(
            (data.fishialProjectAdded, data.fishialProjectExisting,
             data.fishialProjectRejected),
            (8, 5, 1),
        )

        _BlockedThread.instances.clear()
        data._gallery.set_rows([{
            "taxon_node_id": "species-project",
            "scientific_name": "Aqua projecta",
            "crop_count": 14,
        }])
        with patch("src.controllers.data_controller.threading.Thread", _BlockedThread):
            data.promoteGalleryTaxonAt(0)
        with patch(
            "fishial_gallery.promote_taxon_to_gallery",
            return_value={"ok": False, "error": "embedder indisponible"},
        ):
            _BlockedThread.instances[0].run()
        self.assertEqual(data.fishialProjectState, "error")
        self.assertIn("indisponible", data.fishialProjectMessage)

    def test_bulk_fishial_is_async_prevents_overlap_and_reports_partial_success(self):
        data = DataController(self.measure, _FishStub([self.box]))
        data._gallery.set_rows([{"taxon_node_id": "species-project", "rank": "species"}])
        _BlockedThread.instances.clear()
        with patch("src.controllers.data_controller.threading.Thread", _BlockedThread):
            data.promoteAllNewFishialImages()
            data.promoteAllNewFishialImages()
            data.promoteGalleryTaxonAt(0)
        self.assertEqual(len(_BlockedThread.instances), 1)
        self.assertEqual(data.fishialProjectState, "running")

        def partial_result(*, progress):
            progress(1, 2, "Aqua projecta")
            self.assertIn("1/2", data.fishialProjectMessage)
            return {"ok": False, "added": 5, "existing": 20, "rejected": 1,
                    "species_processed": 2, "species_below_threshold": 3,
                    "error": "Aqua altera : image inaccessible"}

        with patch("fishial_gallery.promote_all_new_references", side_effect=partial_result), patch.object(
            data, "refreshGallery",
        ) as refresh:
            _BlockedThread.instances[0].run()
            refresh.assert_called_once_with()
        self.assertEqual(data.fishialProjectState, "error")
        self.assertEqual(data.fishialProjectAdded, 5)
        self.assertIn("5 ajoutée(s)", data.fishialProjectMessage)
        self.assertIn("3 espèce(s) sous le seuil", data.fishialProjectMessage)
        self.assertIn("image inaccessible", data.fishialProjectMessage)

    def test_frame_change_clears_ai_count_and_blocks_stale_validation(self):
        import fish_annotate as fa
        import fish_db_stats as fdb

        fish = FishController(self.measure)
        data = DataController(self.measure, fish)
        fish.set_data_controller(data)
        data._fish = fish
        data._media_id = fa.resolve_media_id(str(self.media), create=True)
        fish._fish_ia = True
        fish._replace_last_boxes([
            {**self.box, "x1": float(i * 10), "x2": float(i * 10 + 8)}
            for i in range(5)
        ])
        fish._set_frame_count_ready(True)
        fish._publish_overlay()
        data.refreshFrameAbundance()
        self.assertEqual(data.frameAiCount, 5)

        self.measure.frameIndex = 2
        self.measure.frameIndexChanged.emit()
        self.assertEqual(fish.lastBoxCount, 0)
        self.assertEqual(fish.fishCount, 0)
        self.assertEqual(data.frameAiCount, 0)
        self.assertFalse(data.frameCountCurrent)

        data.validateFrameCount(5)
        self.assertFalse(
            fdb.get_frame_abundance(
                data.mediaId, self.measure.leftAbsFrame
            )["exists"]
        )
        self.assertIn("Attendez", data.statusText)

        data.frameManualCount = 2
        self.assertTrue(data.frameCountCurrent)
        data.validateFrameCount(2)
        row = fdb.get_frame_abundance(data.mediaId, self.measure.leftAbsFrame)
        self.assertTrue(row["validated"])
        self.assertEqual(row["effective_count"], 2)

    def test_partial_na_survives_controller_restart(self):
        import fish_annotate as fa

        data = DataController(self.measure, _FishStub([self.box]))
        with patch("src.controllers.data_controller.threading.Thread", _ImmediateThread):
            data.addObservationFromSelectedBox()
        ann_id = data.selectedAnnId
        data.saveSelectedRow("Acanthuridae", "NA", "NA")

        persisted = next(r for r in fa.list_observations(media_path=str(self.media))
                         if r["ann_id"] == ann_id)
        self.assertEqual(persisted["identification_status"], "identified")
        self.assertFalse(persisted["family_is_na"])
        self.assertTrue(persisted["genus_is_na"])
        self.assertTrue(persisted["species_is_na"])

        restarted = DataController(self.measure, _FishStub([self.box]))
        restarted.refreshRegistry()
        restarted.loadRegistryRow(restarted._registry_index_by_ann_id(ann_id))
        self.assertNotEqual(restarted.editFamily, "NA")
        self.assertEqual(restarted.editGenus, "NA")
        self.assertEqual(restarted.editSpecies, "NA")

    def test_session_summary_is_maxn_not_a_total_of_individuals(self):
        data = DataController(self.measure, _FishStub([self.box]))
        data._media_id = "media-1"
        with (
            patch("fish_db_stats.session_max_visible_fish", return_value=4),
            patch("fish_db_stats.count_validated_frames", return_value=3),
            patch(
                "fish_db_stats.get_session_detail",
                return_value={"grazing_count": 2, "species_count": 5},
            ),
        ):
            data._refresh_session_stats()

        self.assertEqual(
            data.sessionStatsSummary,
            "MaxN 4 · 3 frame(s) validée(s) · 2 événement(s) · 5 espèce(s)",
        )
        self.assertNotIn("piste", data.sessionStatsSummary.lower())
        self.assertNotIn("enregistr", data.sessionStatsSummary.lower())

    def test_maxn_uses_only_validated_absolute_frames(self):
        import fish_annotate as fa
        import fish_db_stats as fdb

        media_id = fa.resolve_media_id(str(self.media), create=True)
        fdb.upsert_frame_abundance_ai(media_id, 10, 9, frame_ref="absolute")
        self.assertIsNone(fdb.session_max_visible_fish(media_id))
        fdb.validate_frame_abundance(
            media_id, 11, 3, ai_count=4, frame_ref="absolute"
        )
        self.assertEqual(fdb.session_max_visible_fish(media_id), 3)
        self.assertEqual(fdb.count_validated_frames(media_id), 1)
        fdb.validate_frame_abundance(
            media_id, 10, 7, ai_count=9, frame_ref="absolute"
        )
        self.assertEqual(fdb.session_max_visible_fish(media_id), 7)

        refreshed = fdb.upsert_frame_abundance_ai(
            media_id, 10, 2, frame_ref="absolute",
        )
        self.assertEqual(refreshed["ai_count"], 2)
        self.assertEqual(refreshed["manual_count"], 7)
        self.assertTrue(refreshed["validated"])
        self.assertEqual(fdb.session_max_visible_fish(media_id), 7)

    def test_tracked_observation_waits_for_real_track_before_insert(self):
        import fish_annotate as fa

        data = DataController(self.measure, _FishStub([self.box]))
        flush_results = iter((False, True))
        payload = {
            "path": str(self.media),
            "frame_index": 3,
            "manual": True,
            "raw": {
                "x1": 10.0, "y1": 20.0, "x2": 60.0, "y2": 70.0,
                "track_id": 7, "db_track_id": "", "conf": 0.0,
            },
            "external_track_id": 7,
            "flush_tracks": lambda: next(flush_results),
            "model_conf_threshold": None,
            "measurement_mm": None,
            "measurement_target": None,
        }
        with (
            patch.object(fa, "resolve_track_db_id", return_value="track-db-7"),
            patch.object(
                fa, "add_observation",
                return_value={"ann_id": "ann-linked", "measurement_mm": None},
            ) as insert,
        ):
            with self.assertRaisesRegex(RuntimeError, "réessayez"):
                data._db_add_observation(payload)
            insert.assert_not_called()

            result = data._db_add_observation(payload)
            self.assertEqual(result["out"]["ann_id"], "ann-linked")
            insert.assert_called_once()
            self.assertEqual(insert.call_args.kwargs["track_id"], "track-db-7")

    def test_bbox_edit_invalidates_measurement_before_add(self):
        import fish_annotate as fa

        fish = FishController(self.measure)
        data = DataController(self.measure, fish)
        fish.set_data_controller(data)
        fish.addManualBox(10.0, 20.0, 60.0, 70.0)
        data.selectBoxIndex(0)
        self.measure.distanceMm = 133.0
        data.onStereoMeasureChanged()
        self.assertAlmostEqual(data.pendingStereoMeasureMm, 133.0)

        self.assertTrue(fish.updateManualBox(0, 12.0, 20.0, 62.0, 70.0))

        self.assertEqual(data.pendingStereoMeasureMm, 0.0)
        self.assertEqual(fish.statusText, "Bbox modifiée - mesurez à nouveau")
        self.assertEqual(data.statusText, "Bbox modifiée - mesurez à nouveau")
        with patch("src.controllers.data_controller.threading.Thread", _ImmediateThread):
            data.addObservationFromSelectedBox()
        row = fa.list_observations(media_path=str(self.media))[0]
        self.assertIsNone(row["measurement_mm"])

    def test_redetection_same_cardinality_invalidates_fish_and_data_selection(self):
        other_box = {**self.box, "x1": 65.0, "x2": 90.0}
        fish = FishController(self.measure)
        data = DataController(self.measure, fish)
        fish.set_data_controller(data)
        fish._replace_last_boxes([self.box, other_box])
        fish.setSelectedFishIndex(0)
        data.selectBoxIndex(0)

        fish._detect_seq = 7
        fish._on_detect_ok(
            [other_box, self.box], 7,
            (self.measure.frameIndex, self.measure.leftAbsFrame),
        )

        self.assertEqual(fish.selectedFishIndex, -1)
        self.assertEqual(data.selectedBoxIndex, -1)
        selected: list[int] = []
        data._add_observation_at_index = selected.append
        data.addObservationFromSelectedBox()
        self.assertEqual(selected, [])
        self.assertIn("Plusieurs cadres", data.statusText)

    def test_bbox_sqlite_measure_na_then_real_coco_export(self):
        import fish_annotate as fa

        annotator = fa.create_annotator("Pierrick Test")
        self.assertTrue(annotator["annotator_id"])
        fish = _FishStub([self.box])
        data = DataController(self.measure, fish)
        data.selectBoxIndex(0)
        self.measure.distanceMm = 187.4
        data.onStereoMeasureChanged()

        with patch(
            "src.controllers.data_controller.threading.Thread", _ImmediateThread,
        ):
            data.addObservationFromSelectedBox()

        ann_id = data.selectedAnnId
        self.assertTrue(ann_id)
        self.assertEqual(data.registryCount, 1)
        rows = fa.list_observations(media_path=str(self.media))
        self.assertEqual(len(rows), 1)
        created = rows[0]
        self.assertEqual(created["ann_id"], ann_id)
        self.assertEqual(created["frame_index"], 3)
        self.assertEqual(created["frame_ref"], "absolute")
        self.assertEqual(created["source"], "manual")
        self.assertEqual(created["author"], "Pierrick Test")
        self.assertAlmostEqual(created["measurement_mm"], 187.4)
        self.assertEqual(created["geometry_space"], "raw")
        self.assertEqual(created["geometry"]["ref_width"], 100)
        self.assertEqual(created["geometry"]["ref_height"], 80)
        self.assertEqual(
            [created["geometry"][key] for key in ("x_min", "y_min", "x_max", "y_max")],
            [10.0, 20.0, 60.0, 70.0],
        )

        data.saveSelectedRow("NA", "NA", "NA")
        persisted = fa.list_observations(media_path=str(self.media))[0]
        self.assertAlmostEqual(persisted["measurement_mm"], 187.4)
        self.assertEqual(persisted["identification_status"], "unidentifiable")
        self.assertEqual(persisted["source"], "manual")

        with session_scope(self.db_path) as session:
            run = export_coco(
                session,
                self.tmp_path / "exports",
                split_by="media",
                taxonomy_rank="family",
                rectify_images=False,
                min_instances=1,
                min_media=1,
            )
            output = Path(run.output_path)
            run_id = run.id

        coco = json.loads(
            (output / "instances_family.json").read_text(encoding="utf-8")
        )
        manifest = json.loads((output / "manifest.json").read_text(encoding="utf-8"))
        self.assertEqual(len(coco["images"]), 1)
        self.assertEqual(coco["images"][0]["frame_index"], 3)
        self.assertEqual(coco["images"][0]["frame_ref"], "absolute")
        self.assertEqual(coco["images"][0]["image_space"], "raw")
        self.assertEqual(len(coco["annotations"]), 1)
        exported = coco["annotations"][0]
        self.assertEqual(exported["bbox"], [10.0, 20.0, 50.0, 50.0])
        self.assertEqual(exported["ignore"], 1)
        self.assertEqual(exported["iscrowd"], 1)
        categories = {row["id"]: row["name"] for row in coco["categories"]}
        self.assertEqual(
            categories[exported["category_id"]], UNIDENTIFIED_CATEGORY_NAME,
        )
        attributes = exported["attributes"]
        self.assertEqual(attributes["spatial_annotation_id"], ann_id)
        self.assertEqual(attributes["geometry_space"], "raw")
        self.assertEqual(attributes["source"], "manual")
        self.assertEqual(attributes["identification_status"], "unidentifiable")
        self.assertEqual(attributes["author"], "Pierrick Test")
        self.assertAlmostEqual(attributes["measurement_mm"], 187.4)
        self.assertEqual(manifest["format"], "coco")
        self.assertEqual(manifest["source"]["annotation_count"], 1)
        self.assertTrue(manifest["source"]["db_snapshot_sha256"])

        with session_scope(self.db_path) as session:
            stored_run = session.get(ExportRun, run_id)
            self.assertIsNotNone(stored_run)
            self.assertEqual(stored_run.status, "completed")
            self.assertEqual(stored_run.annotation_count, 1)
            self.assertTrue(stored_run.manifest_sha256)
            stored_ann = session.get(SpatialAnnotation, ann_id)
            self.assertAlmostEqual(stored_ann.measurement_mm, 187.4)


if __name__ == "__main__":
    unittest.main()
