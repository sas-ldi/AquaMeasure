"""Interactions Qt réelles du workflow Mesure (clavier, souris, placement)."""

from __future__ import annotations

import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import Q_ARG, QMetaObject, QObject, QPoint, QPointF, QUrl, Qt
from PySide6.QtGui import QWheelEvent, QFont, QFontDatabase
from PySide6.QtQml import QQmlApplicationEngine, QQmlComponent
from PySide6.QtQuick import QQuickItem
from PySide6.QtTest import QSignalSpy, QTest
from PySide6.QtWidgets import QApplication


APP_ROOT = Path(__file__).resolve().parents[2] / "src" / "interface"
if str(APP_ROOT) not in sys.path:
    sys.path.insert(0, str(APP_ROOT))

import main as app_main  # noqa: E402
from src.controllers.app_controller import AppController  # noqa: E402
from src.imaging.frame_image_provider import FrameImageProvider  # noqa: E402


class MeasureQmlRuntimeTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])
        for filename in ("segoeui.ttf", "segoeuib.ttf", "consola.ttf"):
            font_path = Path(os.environ.get("WINDIR", "C:/Windows")) / "Fonts" / filename
            if font_path.exists():
                QFontDatabase.addApplicationFont(str(font_path))
        cls.app.setFont(QFont("Segoe UI"))
        cls._tmp = tempfile.TemporaryDirectory(prefix="measure_qml_runtime_")
        cls._old_db = os.environ.get("FISH_VISION_DB")
        cls._old_storage = os.environ.get("AQUAMEASURE_STORAGE_CONFIG")
        tmp = Path(cls._tmp.name)
        os.environ["FISH_VISION_DB"] = str(tmp / "annotations.db")
        os.environ["AQUAMEASURE_STORAGE_CONFIG"] = str(tmp / "storage.json")

        cls.qml_root = app_main._prepare_qml_module(APP_ROOT)
        cls._qml_objects = []
        cls.controller = AppController()
        cls.images = FrameImageProvider()
        cls.controller.sync().set_image_provider(cls.images)
        cls.controller.calibration().set_image_provider(cls.images)
        cls.controller.measure().set_image_provider(cls.images)
        cls.engine = QQmlApplicationEngine()
        cls.engine.addImportPath(str(cls.qml_root))
        cls.engine.addImageProvider("frames", cls.images)
        ctx = cls.engine.rootContext()
        for name, value in (
            ("App", cls.controller),
            ("Device", cls.controller.device()),
            ("Sync", cls.controller.sync()),
            ("Calib", cls.controller.calibration()),
            ("Measure", cls.controller.measure()),
            ("Settings", cls.controller.settings()),
            ("Fish", cls.controller.fish()),
            ("Detectors", cls.controller.detectors()),
            ("Data", cls.controller.data()),
            ("DbExplorer", cls.controller.dbExplorer()),
            ("Sessions", cls.controller.sessions()),
            ("Annotator", cls.controller.annotator()),
            ("ProTools", cls.controller.proTools()),
            ("Tracks", cls.controller.tracks()),
            ("Pecks", cls.controller.pecks()),
            ("Storage", cls.controller.storage()),
            ("AppLogoUrl", QUrl()),
            ("AppSansFont", "Segoe UI"),
            ("AppMonoFont", "Consolas"),
        ):
            ctx.setContextProperty(name, value)

    @classmethod
    def tearDownClass(cls):
        for obj in reversed(cls._qml_objects):
            if hasattr(obj, "close"):
                obj.close()
            obj.deleteLater()
        cls._qml_objects.clear()
        cls.app.processEvents()
        del cls.engine
        del cls.controller
        del cls.images
        from src.annodb import connection

        if connection._engine is not None:
            connection._engine.dispose()
            connection._engine = None
            connection._SessionLocal = None
        connection._migrated_paths.clear()
        if cls._old_db is None:
            os.environ.pop("FISH_VISION_DB", None)
        else:
            os.environ["FISH_VISION_DB"] = cls._old_db
        if cls._old_storage is None:
            os.environ.pop("AQUAMEASURE_STORAGE_CONFIG", None)
        else:
            os.environ["AQUAMEASURE_STORAGE_CONFIG"] = cls._old_storage
        cls._tmp.cleanup()

    def _create_window(self, source: str):
        component = QQmlComponent(self.engine)
        component.setData(source.encode("utf-8"), QUrl("inline:test.qml"))
        deadline = 100
        while component.isLoading() and deadline > 0:
            QTest.qWait(20)
            deadline -= 1
        window = component.create()
        self.assertIsNotNone(
            window,
            {
                "status": str(component.status()),
                "errors": [str(error) for error in component.errors()],
            },
        )
        self._qml_objects.extend((component, window))
        window.show()
        QTest.qWait(80)
        return window

    def test_lost_follow_click_reuses_box_without_selection_point_or_marker(self):
        fish = self.controller.fish()
        data = self.controller.data()
        measure = self.controller.measure()
        measure._playing = False
        fish._last_boxes = [{
            "x1": 100.0, "y1": 100.0, "x2": 200.0, "y2": 200.0,
            "track_id": 99, "cls_name": "fish", "conf": 0.9,
            "species_name": "Chromis viridis",
        }]
        fish._manual_boxes = []
        fish._publish_overlay()
        window = self._create_window("""
            import QtQuick
            import QtQuick.Window
            import AquaMeasure
            Window {
                width: 400; height: 300
                MeasureStereoView {
                    objectName: "resumeView"
                    anchors.fill: parent
                    sideLabel: "G"; isLeft: true; videoPath: ""
                    videoFrameCount: 10; frameWidth: 400; frameHeight: 300
                    fps: 25; absFrame: 5; placementEnabled: true
                    interactionEnabled: true; playing: false
                }
            }
        """)
        view = window.findChild(QObject, "resumeView")
        points = QSignalSpy(view.pointPlaced)
        try:
            with (
                patch.object(fish, "_tracker"),
                patch.object(fish, "_launch_assist_segment") as launch,
                patch.object(fish, "selectTrackFromBox") as select_track,
                patch.object(data, "selectBoxExplicit") as select_box,
                patch.object(self.controller.pecks(), "markFromOverlay") as mark,
            ):
                fish._assist_start = 0
                fish._assist_end = 100
                fish._set_busy(False)
                fish._set_assist("waiting")
                for button in (Qt.LeftButton, Qt.RightButton):
                    QTest.mouseClick(window, button, Qt.NoModifier, QPoint(150, 150))
                self.assertEqual(launch.call_count, 2)
                self.assertEqual(launch.call_args.args[0], fish._last_boxes[0])
                select_track.assert_not_called()
                select_box.assert_not_called()
                mark.assert_not_called()
                self.assertEqual(points.count(), 0)
                self.assertEqual(fish._manual_boxes, [])
                # Hors cadre, un clic ne relance pas le suivi.
                QTest.mouseClick(window, Qt.LeftButton, Qt.NoModifier, QPoint(300, 250))
                self.assertEqual(launch.call_count, 2)
                self.assertEqual(points.count(), 1)
                # Glisser depuis une boîte doit toujours permettre de réencadrer.
                with patch.object(fish, "addManualBox") as draw:
                    QTest.mousePress(window, Qt.LeftButton, Qt.NoModifier, QPoint(150, 150))
                    QTest.mouseMove(window, QPoint(250, 240), 20)
                    QTest.mouseRelease(window, Qt.LeftButton, Qt.NoModifier, QPoint(250, 240))
                    draw.assert_called_once()
                self.assertEqual(launch.call_count, 2)
                self.assertEqual(points.count(), 1)
        finally:
            fish._set_assist("idle")
            window.close()

    def test_space_arrows_and_text_input_focus(self):
        measure = self.controller.measure()
        measure._frame_count = 10
        measure._frame_index = 5
        measure._playing = False
        measure.frameCountChanged.emit()
        measure.frameIndexChanged.emit()
        measure.playingChanged.emit()
        self.controller.currentPage = 4

        window = self._create_window("""
            import QtQuick
            import QtQuick.Window
            import AquaMeasure
            Window {
                width: 1000; height: 700
                MeasurePage { anchors.fill: parent }
                TextInput {
                    objectName: "editor"
                    x: 20; y: 20; width: 200; height: 40; z: 100
                }
            }
        """)

        QTest.keyClick(window, Qt.Key_Space)
        self.assertTrue(measure.playing)
        QTest.keyClick(window, Qt.Key_Right)
        self.assertFalse(measure.playing)
        # Les lecteurs sans source du harnais se recalent à 0 à l'arrêt ;
        # on fixe ensuite une frame stable pour prouver les pas exacts.
        measure._frame_index = 5
        measure.frameIndexChanged.emit()
        QTest.keyClick(window, Qt.Key_Right)
        self.assertEqual(measure.frameIndex, 6)
        before_left = measure.frameIndex
        QTest.keyClick(window, Qt.Key_Left)
        self.assertEqual(measure.frameIndex, before_left - 1)

        editor = window.findChild(QObject, "editor")
        self.assertIsNotNone(editor)
        editor.forceActiveFocus()
        QTest.keyClick(window, Qt.Key_Space)
        self.assertFalse(measure.playing)
        self.assertEqual(editor.property("text"), " ")

    def test_leaving_sidebar_spinbox_restores_measure_page_keys(self):
        measure = self.controller.measure()
        measure._frame_count = 10
        measure._frame_index = 5
        measure._playing = False
        measure.frameCountChanged.emit()
        self.controller.currentPage = 4
        window = self._create_window("""
            import QtQuick
            import QtQuick.Controls
            import AquaMeasure
            ApplicationWindow {
                width: 1300; height: 700
                MeasurePage { x: 300; width: 1000; height: 700 }
                ScrollView {
                    width: 280; height: 700
                    contentWidth: 260; contentHeight: 700
                    AppSpinBox {
                        objectName: "confidenceEditor"
                        x: 20; y: 20; width: 240
                        from: 5; to: 95; value: 30
                    }
                }
            }
        """)
        try:
            spin = window.findChild(QObject, "confidenceEditor")
            editor = spin.property("contentItem")
            QTest.mouseClick(window, Qt.LeftButton, Qt.NoModifier, QPoint(140, 40))
            self.assertTrue(editor.property("activeFocus"))
            QTest.keyClick(window, Qt.Key_A, Qt.ControlModifier)
            QTest.keyClick(window, Qt.Key_6)
            QTest.keyClick(window, Qt.Key_5)
            # Cliquer dans la page quitte aussi le FocusScope du ScrollView.
            QTest.mouseClick(window, Qt.LeftButton, Qt.NoModifier, QPoint(305, 5))
            self.assertFalse(editor.property("activeFocus"))
            self.assertEqual(spin.property("value"), 65)
            measure._frame_index = 5
            measure.frameIndexChanged.emit()
            QTest.keyClick(window, Qt.Key_Right)
            self.assertEqual(measure.frameIndex, 6)
        finally:
            window.close()

    def test_real_mouse_selection_hover_and_four_point_placement(self):
        fish = self.controller.fish()
        data = self.controller.data()
        measure = self.controller.measure()
        measure._frame_count = 10
        measure._frame_width = 400
        measure._frame_height = 300
        measure._playing = False
        fish._last_boxes = [{
            "x1": 100.0, "y1": 100.0, "x2": 200.0, "y2": 200.0,
            "track_id": -1, "cls_name": "fish", "conf": 0.9,
        }]
        fish._manual_boxes = []
        fish._manual_by_frame.clear()
        fish._publish_overlay()
        data.selectBoxIndex(-1)
        data.setSelectedTrackFromId(-1)

        window = self._create_window("""
            import QtQuick
            import QtQuick.Window
            import AquaMeasure
            Window {
                width: 800; height: 300
                MeasureStereoView {
                    objectName: "leftView"
                    x: 0; y: 0; width: 400; height: 300
                    sideLabel: "G"; isLeft: true; videoPath: ""
                    videoFrameCount: 10; frameWidth: 400; frameHeight: 300
                    fps: 25; absFrame: 5; placementEnabled: true
                    interactionEnabled: true; playing: false
                }
                MeasureStereoView {
                    objectName: "rightView"
                    x: 400; y: 0; width: 400; height: 300
                    sideLabel: "D"; isLeft: false; videoPath: ""
                    videoFrameCount: 10; frameWidth: 400; frameHeight: 300
                    fps: 25; absFrame: 5; placementEnabled: true
                    interactionEnabled: true; playing: false
                }
            }
        """)
        left = window.findChild(QObject, "leftView")
        right = window.findChild(QObject, "rightView")
        left_points = QSignalSpy(left.pointPlaced)
        right_points = QSignalSpy(right.pointPlaced)

        QTest.mouseMove(window, QPoint(150, 150))
        self.assertEqual(left.property("_hoveredManualIndex"), 0)
        QTest.mouseClick(window, Qt.LeftButton, Qt.NoModifier, QPoint(150, 150))
        QTest.mouseClick(window, Qt.LeftButton, Qt.NoModifier, QPoint(180, 180))
        QTest.mouseClick(window, Qt.LeftButton, Qt.NoModifier, QPoint(550, 150))
        QTest.mouseClick(window, Qt.LeftButton, Qt.NoModifier, QPoint(580, 180))
        self.assertEqual(left_points.count(), 2)
        self.assertEqual(right_points.count(), 2)
        self.assertEqual(fish.selectedFishIndex, -1)
        self.assertEqual(data.selectedBoxIndex, -1)
        self.assertEqual(data.selectedTrackLabel, "")

        # Le clic droit sélectionne sans poser de point ni créer d'observation.
        registry_count = data._registry.rowCount()
        QTest.mouseClick(window, Qt.RightButton, Qt.NoModifier, QPoint(150, 150))
        self.assertEqual(fish.selectedFishIndex, 0)
        self.assertEqual(data.selectedBoxIndex, 0)
        self.assertEqual(left_points.count(), 2)
        self.assertEqual(data._registry.rowCount(), registry_count)
        QTest.mouseClick(window, Qt.RightButton, Qt.NoModifier, QPoint(300, 250))
        self.assertEqual(fish.selectedFishIndex, -1)
        self.assertEqual(data.selectedBoxIndex, -1)

        # Sans sélection préalable au clic droit, une bbox manuelle ne vole
        # jamais les clics de mesure, même à l'intérieur.
        fish._last_boxes = []
        fish._manual_boxes = []
        fish._manual_by_frame.clear()
        fish.addManualBox(100.0, 100.0, 200.0, 200.0)
        manual_count = fish.lastBoxCount
        for point in (
            QPoint(150, 150), QPoint(175, 165),
            QPoint(550, 150), QPoint(580, 180),
        ):
            QTest.mouseClick(window, Qt.LeftButton, Qt.NoModifier, point)
        self.assertEqual(left_points.count(), 4)
        self.assertEqual(right_points.count(), 4)
        self.assertEqual(fish.lastBoxCount, manual_count)

        # Hors placement, le clic droit sélectionne et une vraie piste seule
        # active les actions d'événement.
        left.setProperty("placementEnabled", False)
        fish._manual_boxes = []
        fish._last_boxes = [{
            "x1": 100.0, "y1": 100.0, "x2": 200.0, "y2": 200.0,
            "track_id": 7, "cls_name": "fish", "conf": 0.9,
        }]
        fish._publish_overlay()
        QTest.mouseClick(window, Qt.RightButton, Qt.NoModifier, QPoint(150, 150))
        self.assertEqual(data.selectedTrackLabel, "7")
        self.assertEqual(left_points.count(), 4)

    def test_focused_registry_row_drives_next_action_before_box_count(self):
        data = self.controller.data()
        fish = self.controller.fish()
        measure = self.controller.measure()
        measure._frame_count = 10
        measure._playing = False
        fish._last_boxes = []
        fish._manual_boxes = []
        fish._publish_overlay()
        data._selected_ann_id = "ann-focus"
        data._selected_row_data = {"identification_status": "unreviewed"}
        data._edit_measurement_mm = 0.0
        data.selectedAnnIdChanged.emit()
        data.selectedObservationChanged.emit()

        window = self._create_window("""
            import QtQuick
            import QtQuick.Window
            import AquaMeasure
            Window {
                width: 500; height: 700
                MeasureRegistryPanel { anchors.fill: parent }
            }
        """)
        panel = window.findChild(QObject, "measureRegistryPanel")
        self.assertIsNotNone(panel)
        next_action = panel.property("nextAction")
        self.assertIn("Identifiez le poisson", next_action)
        self.assertNotIn("Tracez un cadre", next_action)
        data._edit_measurement_mm = 120.0
        data.editMeasurementMmChanged.emit()
        self.app.processEvents()
        self.assertIn("Identifiez le poisson", panel.property("nextAction"))

    def test_refresh_registry_button_does_not_collapse_panel(self):
        window = self._create_window("""
            import QtQuick
            import QtQuick.Window
            import AquaMeasure
            Window {
                width: 600; height: 700
                MeasureRegistryPanel { anchors.fill: parent }
            }
        """)
        panel = window.findChild(QObject, "measureRegistryPanel")
        button = window.findChild(QObject, "refreshRegistryButton")
        self.assertIsNotNone(panel)
        self.assertIsNotNone(button)
        self.assertTrue(panel.property("expanded"))

        clicked = QSignalSpy(button.clicked)
        # Le bord gauche était recouvert par la zone invisible de repli.
        scene_pos = button.mapToScene(
            QPointF(2.0, float(button.property("height")) / 2.0)
        )
        QTest.mouseClick(
            window,
            Qt.LeftButton,
            Qt.NoModifier,
            QPoint(round(scene_pos.x()), round(scene_pos.y())),
        )

        self.assertEqual(clicked.count(), 1)
        self.assertTrue(panel.property("expanded"))

    def test_saving_fish_removes_visible_measurement_handles_only_on_success(self):
        data = self.controller.data()
        measure = self.controller.measure()
        data.clearSelection()
        data._db_ok = True
        data._busy = False
        data._selected_ann_id = "saved-fish-handles-test"
        data._edit_measurement_mm = 154.2
        measure._playing = False
        measure._left_a = measure._right_a = QPointF(60, 70)
        measure._left_b = measure._right_b = QPointF(160, 170)
        measure._distance_mm = 154.2
        measure.pointsChanged.emit()
        window = self._create_window("""
            import QtQuick
            import QtQuick.Window
            import AquaMeasure
            Window { width: 400; height: 300
                MeasureStereoView {
                    objectName: "savedFishView"
                    anchors.fill: parent; sideLabel: "G"; isLeft: true
                    videoPath: ""; videoFrameCount: 10
                    frameWidth: 400; frameHeight: 300; fps: 25; absFrame: 0
                    pointA: Measure.leftPointA; pointB: Measure.leftPointB
                    placementEnabled: true; interactionEnabled: true; playing: false
                }
            }
        """)
        view = window.findChild(QObject, "savedFishView")
        try:
            self.assertTrue(view.property("_hasA"))
            self.assertTrue(view.property("_hasB"))
            with patch("fish_annotate.update_observation", side_effect=RuntimeError("write failed")), \
                    patch.object(data, "ensureTaxonomy"):
                data.saveFish("Scaridae", "Scarus", "Scarus ghobban")
            self.app.processEvents()
            self.assertTrue(view.property("_hasA"))
            self.assertTrue(view.property("_hasB"))
            with patch("fish_annotate.update_observation") as update, \
                    patch.object(data, "refreshRegistry"), patch.object(data, "ensureTaxonomy"):
                data.saveFish("Scaridae", "Scarus", "Scarus ghobban")
            self.app.processEvents()
            self.assertAlmostEqual(update.call_args.kwargs["measurement_mm"], 154.2)
            self.assertFalse(view.property("_hasA"))
            self.assertFalse(view.property("_hasB"))
            self.assertLess(measure.rightPointA.x(), 0)
            self.assertLess(measure.rightPointB.x(), 0)
            self.assertEqual(data.selectedAnnId, "saved-fish-handles-test")
            self.assertAlmostEqual(data.editMeasurementMm, 154.2)
        finally:
            data.clearSelection()

    def test_save_button_accepts_bbox_without_stereo_measurement(self):
        data = self.controller.data()
        fish = self.controller.fish()
        measure = self.controller.measure()
        measure._frame_count = 10
        measure._playing = False
        fish._last_boxes = [{
            "x1": 10.0, "y1": 20.0, "x2": 80.0, "y2": 100.0,
            "track_id": -1, "cls_name": "fish", "conf": 0.9,
        }]
        fish._manual_boxes = []
        fish._publish_overlay()
        data.selectBoxIndex(0)
        data._pending_stereo_mm = 0.0
        data.pendingStereoMeasureMmChanged.emit()

        window = self._create_window("""
            import QtQuick
            import QtQuick.Window
            import AquaMeasure
            Window { width: 700; height: 700
                MeasureRegistryPanel { anchors.fill: parent }
            }
        """)
        button = window.findChild(QObject, "saveFishButton")
        self.assertIsNotNone(button)
        self.assertTrue(button.property("enabled"))

    def test_panel_shows_the_prefilled_draft_before_any_write(self):
        """Le panneau doit décrire l'ordre réel : fiche d'abord, écriture après.

        Auparavant il fallait créer la ligne pour voir la proposition du
        modèle, et la longueur ne s'affichait que dans la colonne « Taille »
        d'une observation déjà écrite : impossible de vérifier avant d'écrire.
        """
        data = self.controller.data()
        fish = self.controller.fish()
        measure = self.controller.measure()
        measure._frame_count = 10
        measure._playing = False
        fish._last_boxes = [{
            "x1": 10.0, "y1": 20.0, "x2": 80.0, "y2": 100.0,
            "track_id": -1, "cls_name": "fish", "conf": 0.9,
        }]
        fish._manual_boxes = []
        fish._publish_overlay()
        data._db_ok = True
        data.dbAvailableChanged.emit()
        # Le contrôleur est partagé par toute la classe : repartir d'une page
        # blanche, sinon un verrou laissé par un autre test refuserait la fiche.
        data.clearSelection()

        window = self._create_window("""
            import QtQuick
            import QtQuick.Window
            import AquaMeasure
            Window { width: 700; height: 900
                MeasureRegistryPanel { anchors.fill: parent }
            }
        """)
        panel = window.findChild(QObject, "measureRegistryPanel")
        save_button = window.findChild(QObject, "saveFishButton")
        measure_row = window.findChild(QObject, "draftMeasurementRow")
        measure_value = window.findChild(QObject, "draftMeasurementValue")
        clear_measure = window.findChild(QObject, "clearMeasurementButton")
        clear_selection = window.findChild(QObject, "clearSelectionButton")
        for widget in (panel, save_button, measure_row, measure_value,
                       clear_measure, clear_selection):
            self.assertIsNotNone(widget)

        try:
            # Aucun poisson choisi : le panneau invite au clic droit, pas à
            # « ajouter une ligne ».
            self.assertFalse(data.property("draftActive"))
            self.assertIn("Clic droit", panel.property("nextAction"))
            self.assertEqual(
                save_button.property("text"), "Enregistrer le poisson",
            )
            self.assertFalse(measure_row.property("visible"))

            # Le geste du clic droit : la fiche s'ouvre, rien n'est écrit.
            data.selectBoxExplicit(0)
            self.app.processEvents()
            self.assertTrue(data.property("draftActive"))
            self.assertEqual(data.selectedAnnId, "")
            self.assertEqual(
                save_button.property("text"), "Enregistrer le poisson",
            )
            self.assertTrue(save_button.property("actionable"))
            self.assertIn("Enregistrer le poisson", panel.property("nextAction"))
            self.assertTrue(measure_row.property("visible"))
            self.assertIn("non mesurée", measure_value.property("text"))
            self.assertFalse(clear_measure.property("visible"))
            self.assertTrue(clear_selection.property("actionable"))

            # La mesure alimente la fiche, et se retire.
            data.setEditMeasurementMm(154.0)
            self.app.processEvents()
            self.assertIn("154.0 mm", measure_value.property("text"))
            self.assertTrue(clear_measure.property("visible"))
            self.assertIn("la longueur", panel.property("nextAction"))
        finally:
            data.clearSelection()
            self.app.processEvents()

    def test_single_save_button_accepts_edits_to_an_existing_fish(self):
        """Le meme bouton enregistre une correction, meme sans cadre detecte."""
        data = self.controller.data()
        fish = self.controller.fish()
        measure = self.controller.measure()
        measure._frame_count = 10
        measure._playing = False
        fish._last_boxes = [{
            "x1": 10.0, "y1": 20.0, "x2": 80.0, "y2": 100.0,
            "track_id": -1, "cls_name": "fish", "conf": 0.9,
        }]
        fish._manual_boxes = []
        fish._publish_overlay()
        data.clearSelection()
        data._db_ok = True
        data.dbAvailableChanged.emit()
        data._busy = False
        data.selectBoxIndex(0)
        data._selected_ann_id = ""
        data._edit_family = ""
        data._edit_genus = ""
        data._edit_species = ""
        data._snapshot_row_state()

        window = self._create_window("""
            import QtQuick
            import QtQuick.Window
            import AquaMeasure
            Window { width: 700; height: 800
                FishWorkflowPanel { anchors.fill: parent }
            }
        """)
        add_button = window.findChild(QObject, "saveFishButton")
        save_taxon = window.findChild(QObject, "saveTaxonButton")
        discard = window.findChild(QObject, "discardTaxonButton")
        self.assertIsNotNone(add_button)
        # Une seule action enregistre la fiche et ses corrections.
        self.assertIsNone(save_taxon)
        self.assertIsNotNone(discard)
        self.assertFalse(add_button.property("actionable"))
        self.assertFalse(discard.property("visible"))

        try:
            data._selected_ann_id = "ann-dirty"
            data.selectedAnnIdChanged.emit()
            data.setGenusFilter("NA")
            self.app.processEvents()

            self.assertTrue(data.property("registryRowDirty"))
            self.assertTrue(add_button.property("actionable"))
            self.assertEqual(add_button.property("text"), "Enregistrer le poisson")
            fish._last_boxes = []
            fish.detectionChanged.emit()
            self.app.processEvents()
            self.assertTrue(add_button.property("actionable"))
            with patch.object(data, "_save_selected_row", return_value=True) as update:
                self.assertTrue(QMetaObject.invokeMethod(add_button, "clicked"))
                update.assert_called_once_with("", "NA", "")
            # La sortie de secours n'apparaît que quand elle a un sens.
            self.assertTrue(discard.property("visible"))
        finally:
            data.discardRowEdits()
            data._selected_ann_id = ""
            data.selectedAnnIdChanged.emit()
            data._snapshot_row_state()
            self.app.processEvents()

    def test_cancelling_refreshes_the_taxon_field_under_the_cursor(self):
        """Un champ focalisé refuse de se resynchroniser : les boutons l'y forcent."""
        data = self.controller.data()
        data._selected_ann_id = "ann-focus"
        data.selectedAnnIdChanged.emit()
        data._edit_family = ""
        data._edit_genus = "Trachinotus"
        data._edit_species = ""
        data.editGenusChanged.emit()
        data._snapshot_row_state()

        window = self._create_window("""
            import QtQuick
            import QtQuick.Window
            import AquaMeasure
            Window { width: 700; height: 800
                FishWorkflowPanel { anchors.fill: parent }
            }
        """)
        genus = window.findChild(QObject, "genusTaxonField")
        discard = window.findChild(QObject, "discardTaxonButton")
        self.assertIsNotNone(genus)
        self.assertEqual(genus.property("inputText"), "Trachinotus")

        field = genus.findChild(QObject, "taxonSearchInput")
        self.assertIsNotNone(field, "champ de saisie interne introuvable")

        try:
            QMetaObject.invokeMethod(field, "forceActiveFocus")
            self.app.processEvents()
            self.assertTrue(field.property("activeFocus"))

            field.setProperty("text", "NA")
            self.app.processEvents()
            self.assertEqual(data.editGenus, "NA")
            self.assertTrue(data.property("registryRowDirty"))

            discard.clicked.emit()
            self.app.processEvents()

            self.assertEqual(data.editGenus, "Trachinotus")
            self.assertEqual(genus.property("inputText"), "Trachinotus")
        finally:
            data.discardRowEdits()
            data._selected_ann_id = ""
            data.selectedAnnIdChanged.emit()
            data._snapshot_row_state()
            self.app.processEvents()

    def test_duration_controls_lock_type_then_remove_only_the_interval(self):
        pecks, measure, data, ann_id, track_id, _point = self._prepare_fish_chain(88)
        fish = self.controller.fish()
        fish.cancelGrazingAnalysis()
        self._select_registry_row(data, ann_id)
        data.loadEventTypes()
        self.controller.currentPage = 4
        window = self._create_window("""
            import QtQuick
            import QtQuick.Window
            import AquaMeasure
            Window { width: 320; height: 1000
                FishWorkflowPanel { anchors.fill: parent }
            }
        """)
        block = window.findChild(QObject, "trackFollowBlock")
        block.setProperty("followMode", 0)
        block.setProperty("behaviorSpan", 1)
        block.setProperty("durationKey", "grazing")
        measure._frame_index = 11
        measure.frameIndexChanged.emit()
        self.app.processEvents()
        QMetaObject.invokeMethod(window.findChild(QObject, "trackFollowInButton"), "clicked")
        self.app.processEvents()
        self.assertTrue(fish.trackFollowStartMarked, fish.statusText)
        for name in ("followModeTabs", "behaviorSpanTabs", "durationBehaviorSelector"):
            self.assertFalse(window.findChild(QObject, name).property("enabled"), name)
        measure._frame_index = 14
        measure.frameIndexChanged.emit()
        QMetaObject.invokeMethod(window.findChild(QObject, "trackFollowOutButton"), "clicked")
        self.app.processEvents()
        self.assertEqual(fish.grazingWorkflowState, "success", fish.statusText)
        self.assertEqual(data.selectedTrackDbId, track_id)
        self.assertEqual(len([row for row in data.selectedObservationEvents if row["scope"] == "interval"]), 1)
        self.assertTrue(window.findChild(QObject, "followModeTabs").property("enabled"))
        remove = self._visual_child(window.contentItem(), "observationEventRemoveButton")
        self.assertIsNotNone(remove)
        QMetaObject.invokeMethod(remove, "clicked")
        self.app.processEvents()
        self.assertFalse(any(row["scope"] == "interval" for row in data.selectedObservationEvents))
        self.assertEqual(data.selectedTrackDbId, track_id)

    def test_measurement_workspace_keeps_views_and_unique_editor_at_two_sizes(self):
        _pecks, _measure, _data, _ann, _track, _point = self._prepare_fish_chain(87)
        self.controller.currentPage = 4
        window = self._create_window("""
            import QtQuick
            import QtQuick.Window
            import AquaMeasure
            Window { width: 1280; height: 800
                SidePanelSplitShell {
                    anchors.fill: parent
                    collapsibleSettings: true
                    MeasurePage { anchors.fill: parent; anchors.margins: 8 }
                }
            }
        """)
        for width, height in ((1280, 800), (1600, 900)):
            window.setWidth(width)
            window.setHeight(height)
            QTest.qWait(100)
            work = window.findChild(QQuickItem, "measurementWorkflowPane")
            video = window.findChild(QQuickItem, "measurementVideoPane")
            registry = window.findChild(QQuickItem, "measurementRegistryPane")
            self.assertGreaterEqual(work.width(), 280)
            self.assertGreaterEqual(video.width(), 600)
            self.assertGreaterEqual(registry.width(), 220)
            self.assertEqual(len(window.findChildren(QObject, "saveFishButton")), 1)
            for name in ("familyTaxonField", "measureSelectedBoxButton", "trackFollowInButton", "peckMarkButton"):
                self.assertEqual(len(window.findChildren(QObject, name)), 1, name)
                self.assertIsNone(registry.findChild(QObject, name), name)
            for name in ("measurementCountSpin", "validateFrameCountButton"):
                control = window.findChild(QQuickItem, name)
                left = control.mapToItem(video, QPointF(0, 0)).x()
                self.assertGreaterEqual(left, 0, name)
                self.assertLessEqual(left + control.width(), video.width() + 1, name)
            before = video.width()
            drawer = window.findChild(QObject, "measurementSettingsDrawer")
            QMetaObject.invokeMethod(drawer, "open")
            QTest.qWait(160)
            self.assertTrue(drawer.property("opened"))
            self.assertGreater(drawer.property("height"), height * .8)
            self.assertEqual(video.width(), before)
            body = window.findChild(QQuickItem, "measurementSettingsBody")
            self.assertIsNotNone(body)
            self.assertGreater(body.height(), height * .7)
            window.grabWindow().save(str(APP_ROOT.parent / "build" / f"workspace-{width}.png"))
            QMetaObject.invokeMethod(drawer, "close")

        QMetaObject.invokeMethod(drawer, "open")
        QTest.qWait(160)
        self.controller.currentPage = 5
        QTest.qWait(160)
        self.assertFalse(drawer.property("opened"), "Les réglages de Mesure masquent la page Données")
        self.controller.currentPage = 4

    def test_only_measure_shell_exposes_a_settings_drawer(self):
        self.controller.currentPage = 4
        window = self._create_window("""
            import QtQuick
            import QtQuick.Window
            import QtQuick.Layouts
            import AquaMeasure
            Window { width: 1280; height: 800
                StackLayout { anchors.fill: parent; currentIndex: App.currentPage === 4 ? 1 : 0
                    SidePanelSplitShell { }
                    SidePanelSplitShell { collapsibleSettings: true }
                }
            }
        """)
        drawers = window.findChildren(QObject, "measurementSettingsDrawer")
        self.assertEqual(len(drawers), 1)
        QMetaObject.invokeMethod(drawers[0], "open")
        QTest.qWait(160)
        self.assertTrue(drawers[0].property("opened"))
        self.controller.currentPage = 5
        QTest.qWait(160)
        self.assertFalse(drawers[0].property("opened"))
        self.controller.currentPage = 4

    def test_sidebar_has_no_duplicate_follow_or_behavior_controls(self):
        window = self._create_window("""
            import QtQuick
            import QtQuick.Window
            import AquaMeasure
            Window { width: 420; height: 900
                ContextSidePanel { anchors.fill: parent }
            }
        """)
        for name in ("behaviorTypeSelector", "instantBehaviorButton",
                     "intervalBehaviorNotice", "contextTrackFollowBlock",
                     "contextTrackFollowInButton", "contextTrackFollowOutButton"):
            self.assertIsNone(window.findChild(QObject, name), name)

    def test_isolated_point_refuses_another_frame_or_interval_type(self):
        pecks, measure, data, ann_id, _track, point = self._prepare_fish_chain(71, with_track=False)
        self._select_registry_row(data, ann_id)
        data.loadEventTypes()
        measure._frame_index = data.selectedFrameIndex + 2
        measure.frameIndexChanged.emit()
        self.assertFalse(data.toggleSelectedPointEvent(point["key"]))
        self.assertIn("Revenez", data.statusText)
        measure._frame_index = data.selectedFrameIndex
        measure.frameIndexChanged.emit()
        self.assertFalse(data.toggleSelectedPointEvent("grazing"))
        self.assertNotIn("grazing", data.selectedPointEventKeys)

    def test_settings_exposes_minimal_behavior_form(self):
        data = self.controller.data()
        fish = self.controller.fish()
        old_db_ok = data._db_ok
        data._db_ok = True
        self.assertTrue(data.addBehaviorType("Ponte suivie réglages", "interval"))
        behavior = next(
            row for row in data.behaviorTypes
            if row.get("label") == "Ponte suivie réglages"
        )
        # Un suivi engagé ne fige plus aucun comportement : le cycle qui
        # gelait un type d'événement entre son début et sa fin a été retiré,
        # celui qui reste ne produit qu'une piste. Le bouton « Retirer » doit
        # donc rester actif pendant un suivi.
        fish._graze_state = "marked"
        fish.grazingWorkflowChanged.emit()
        window = self._create_window("""
            import QtQuick
            import QtQuick.Window
            import AquaMeasure
            Window { width: 900; height: 800
                SettingsPage { anchors.fill: parent }
            }
        """)
        name = window.findChild(QObject, "behaviorNameField")
        self.assertIsNotNone(name)
        self.assertIsNotNone(window.findChild(QObject, "behaviorScopeSelector"))
        logo = window.findChild(QObject, "behaviorLogoSelector")
        self.assertIsNotNone(logo)
        self.assertEqual(logo.property("count"), 10)
        add = window.findChild(QObject, "addBehaviorButton")
        remove_buttons = []

        def collect_remove_buttons(item):
            for child in item.childItems():
                if child.objectName() == "behaviorRemoveButton":
                    remove_buttons.append(child)
                collect_remove_buttons(child)

        collect_remove_buttons(window.contentItem())
        remove = next(
            (button for button in remove_buttons if button.property("visible")),
            None,
        )
        self.assertIsNotNone(add)
        self.assertIsNotNone(remove)
        self.assertEqual(add.property("disabledReason"), "Saisissez un nom de comportement.")
        name.setProperty("text", "Test")
        data._db_ok = False
        data.dbAvailableChanged.emit()
        self.app.processEvents()
        self.assertEqual(add.property("disabledReason"), "Base d'annotations indisponible.")
        self.assertTrue(
            remove.property("actionable"),
            "le garde-fou du cycle retiré bloque encore le bouton « Retirer »",
        )
        data._db_ok = old_db_ok
        fish._graze_state = "idle"
        data.dbAvailableChanged.emit()
        fish.grazingWorkflowChanged.emit()
        self.app.processEvents()
        self.assertTrue(data.removeBehaviorType(behavior["id"]))

    def test_video_overlay_stays_visible_during_playback(self):
        window = self._create_window("""
            import QtQuick
            import QtQuick.Window
            import AquaMeasure
            Window { width: 640; height: 360
                MeasureStereoView {
                    anchors.fill: parent
                    sideLabel: "Gauche"; isLeft: true; videoPath: ""
                    videoFrameCount: 10; frameWidth: 640; frameHeight: 360
                    fps: 25; absFrame: 0; placementEnabled: false
                    interactionEnabled: false; playing: true
                }
            }
        """)
        overlay = window.findChild(QObject, "videoOverlayCanvas")
        self.assertIsNotNone(overlay)
        self.assertTrue(overlay.property("visible"))

    def test_app_combo_explicitly_replaces_focused_a_with_b(self):
        data = self.controller.data()
        fish = self.controller.fish()
        measure = self.controller.measure()
        measure._frame_count = 10
        measure._frame_index = 0
        measure._playing = False
        box_a = {"x1": 10.0, "y1": 20.0, "x2": 80.0, "y2": 100.0,
                 "track_id": -1, "cls_name": "fish", "conf": 0.9}
        box_b = {"x1": 180.0, "y1": 20.0, "x2": 280.0, "y2": 100.0,
                 "track_id": -1, "cls_name": "fish", "conf": 0.9}
        fish._last_boxes = [box_a, box_b]
        fish._manual_boxes = []
        data._selected_ann_id = "ann-a"
        data._selected_row_data = {
            "ann_id": "ann-a", "frame_index": 0, "frame_index_abs": 0,
            "geometry": {"x_min": 10.0, "y_min": 20.0,
                         "x_max": 80.0, "y_max": 100.0},
        }
        data.selectedAnnIdChanged.emit()
        data.selectedObservationChanged.emit()
        fish.focusAnnotationBox("ann-a", 0, 10, 20, 80, 100, "A")
        fish._publish_overlay()

        window = self._create_window("""
            import QtQuick
            import QtQuick.Window
            import AquaMeasure
            Window { width: 600; height: 700
                MeasureRegistryPanel { anchors.fill: parent }
            }
        """)
        selector = window.findChild(QObject, "boxToAddSelector")
        self.assertIsNotNone(selector)
        invoked = QMetaObject.invokeMethod(
            selector, "activated", Qt.DirectConnection, Q_ARG(int, 1),
        )
        self.assertTrue(invoked)
        self.assertEqual(data.selectedAnnId, "")
        self.assertFalse(fish.focusBox.get("valid"))
        self.assertEqual(data.selectedBoxIndex, 1)

    def test_right_click_selects_without_arming_edition(self):
        """Le clic droit selectionne, comme sur n'importe quelle bbox.

        Il armait de fait l'edition : le premier glisser gauche modifiait la
        bbox, ce qui vide la selection cote Fish/Data - et « Mesurer » n'avait
        alors plus de cible.
        """
        fish = self.controller.fish()
        data = self.controller.data()
        measure = self.controller.measure()
        measure._frame_count = 10
        measure._frame_width = 400
        measure._frame_height = 300
        measure._playing = False
        fish._last_boxes = []
        fish._manual_boxes = []
        fish._manual_by_frame.clear()
        fish.addManualBox(100.0, 100.0, 200.0, 200.0)
        data.selectBoxIndex(0)
        window = self._create_window("""
            import QtQuick
            import QtQuick.Window
            import AquaMeasure
            Window { width: 400; height: 300
                MeasureStereoView {
                    objectName: "rightClickView"
                    anchors.fill: parent; sideLabel: "G"; isLeft: true
                    videoPath: ""; videoFrameCount: 10
                    frameWidth: 400; frameHeight: 300; fps: 25; absFrame: 0
                    placementEnabled: true; interactionEnabled: true; playing: false
                }
            }
        """)
        view = window.findChild(QObject, "rightClickView")
        before = fish.lastBoxAtIndex(0)
        QTest.mouseClick(window, Qt.RightButton, Qt.NoModifier, QPoint(150, 150))
        self.assertEqual(view.property("_rightSelectedBoxIndex"), 0)
        self.assertFalse(
            view.property("_manualEditActive"),
            "le clic droit ne doit pas armer l'edition",
        )

        # Glisser sur la bbox selectionnee : ni deplacement, ni nouvelle bbox.
        count_before = fish.lastBoxCount
        QTest.mousePress(window, Qt.LeftButton, Qt.NoModifier, QPoint(150, 150))
        QTest.mouseMove(window, QPoint(175, 165), delay=30)
        QTest.mouseRelease(window, Qt.LeftButton, Qt.NoModifier, QPoint(175, 165))
        self.app.processEvents()
        self.assertEqual(
            fish.lastBoxCount, count_before,
            "un glisser sur la bbox selectionnee ne doit pas en creer une autre",
        )
        after = fish.lastBoxAtIndex(0)
        self.assertAlmostEqual(after["x1"], before["x1"], places=3)
        self.assertAlmostEqual(after["y1"], before["y1"], places=3)

    def test_real_right_click_opens_the_fish_sheet_without_writing(self):
        """Un vrai clic droit ouvre la fiche : c'est le geste que décrit le client.

        Il ne crée AUCUNE ligne - l'ordre inverse (créer puis identifier)
        était justement le reproche.
        """
        fish = self.controller.fish()
        data = self.controller.data()
        measure = self.controller.measure()
        measure._frame_count = 10
        measure._frame_width = 400
        measure._frame_height = 300
        measure._playing = False
        data.clearSelection()
        fish._last_boxes = []
        fish._manual_boxes = []
        fish._manual_by_frame.clear()
        fish.addManualBox(100.0, 100.0, 200.0, 200.0)
        rows_before = data.registryCount

        window = self._create_window("""
            import QtQuick
            import QtQuick.Window
            import AquaMeasure
            Window { width: 400; height: 300
                MeasureStereoView {
                    objectName: "sheetView"
                    anchors.fill: parent; sideLabel: "G"; isLeft: true
                    videoPath: ""; videoFrameCount: 10
                    frameWidth: 400; frameHeight: 300; fps: 25; absFrame: 0
                    placementEnabled: true; interactionEnabled: true; playing: false
                }
            }
        """)
        try:
            self.assertFalse(data.property("draftActive"))
            QTest.mouseClick(
                window, Qt.RightButton, Qt.NoModifier, QPoint(150, 150),
            )
            self.app.processEvents()

            self.assertTrue(data.property("draftActive"))
            self.assertEqual(data.selectedBoxIndex, 0)
            self.assertEqual(data.selectedAnnId, "")
            self.assertEqual(data.registryCount, rows_before)
        finally:
            data.clearSelection()
            self.app.processEvents()

    def test_arming_edition_restores_move_and_resize(self):
        """Une fois l'edition armee (crayon), on retrouve deplacer/redimensionner."""
        fish = self.controller.fish()
        data = self.controller.data()
        measure = self.controller.measure()
        measure._frame_count = 10
        measure._frame_width = 400
        measure._frame_height = 300
        measure._playing = False
        fish._last_boxes = []
        fish._manual_boxes = []
        fish._manual_by_frame.clear()
        fish.addManualBox(100.0, 100.0, 200.0, 200.0)
        data.selectBoxIndex(0)
        window = self._create_window("""
            import QtQuick
            import QtQuick.Window
            import AquaMeasure
            Window { width: 400; height: 300
                MeasureStereoView {
                    objectName: "manualPlacementView"
                    anchors.fill: parent; sideLabel: "G"; isLeft: true
                    videoPath: ""; videoFrameCount: 10
                    frameWidth: 400; frameHeight: 300; fps: 25; absFrame: 0
                    placementEnabled: true; interactionEnabled: true; playing: false
                }
            }
        """)
        before_count = fish.lastBoxCount
        view = window.findChild(QObject, "manualPlacementView")
        placed = QSignalSpy(view.pointPlaced)
        QTest.mouseClick(window, Qt.RightButton, Qt.NoModifier, QPoint(150, 150))
        self.assertEqual(view.property("_rightSelectedBoxIndex"), 0)
        # Le crayon arme l'edition - sans lui, le glisser serait ignore.
        view.setProperty("_manualEditArmed", 0)
        self.assertTrue(view.property("_manualEditActive"))
        QTest.mousePress(window, Qt.LeftButton, Qt.NoModifier, QPoint(150, 150))
        QTest.mouseMove(window, QPoint(175, 165), delay=30)
        QTest.mouseRelease(window, Qt.LeftButton, Qt.NoModifier, QPoint(175, 165))
        self.app.processEvents()
        self.assertEqual(fish.lastBoxCount, before_count)
        self.assertEqual(placed.count(), 0)
        moved = fish.lastBoxAtIndex(0)
        self.assertGreater(moved["x1"], 100.0)
        self.assertGreater(moved["y1"], 100.0)

        # Une poignée de la même bbox, toujours sélectionnée, redimensionne.
        old_x1 = moved["x1"]
        old_x2 = moved["x2"]
        corner = QPoint(round(moved["x1"]), round(moved["y1"]))
        QTest.mousePress(window, Qt.LeftButton, Qt.NoModifier, corner)
        QTest.mouseMove(window, corner + QPoint(-12, -8), delay=30)
        QTest.mouseRelease(
            window, Qt.LeftButton, Qt.NoModifier, corner + QPoint(-12, -8),
        )
        resized = fish.lastBoxAtIndex(0)
        self.assertLess(resized["x1"], old_x1)
        self.assertAlmostEqual(resized["x2"], old_x2, places=3)

    def test_auto_box_right_click_resize_and_delete_keep_identification(self):
        fish, data, measure = self.controller.fish(), self.controller.data(), self.controller.measure()
        data.clearSelection()
        measure._frame_count = 10
        measure._frame_width, measure._frame_height = 400, 300
        measure._playing = False
        measure.clearPoints()
        fish._manual_boxes = []
        fish._manual_by_frame.clear()
        fish._auto_box_edits.clear()
        original = {"x1": 100., "y1": 100., "x2": 200., "y2": 200.,
                    "track_id": -1, "conf": 0.8, "cls_name": "fish",
                    "species_name": "Chromis viridis", "species_conf": 0.87}
        fish._last_boxes = [original]
        fish._publish_overlay()
        window = self._create_window("""
            import QtQuick
            import QtQuick.Window
            import AquaMeasure
            Window { width: 400; height: 300
                MeasureStereoView {
                    objectName: "autoEditView"
                    anchors.fill: parent; sideLabel: "G"; isLeft: true
                    videoPath: ""; videoFrameCount: 10
                    frameWidth: 400; frameHeight: 300; fps: 25; absFrame: 0
                    placementEnabled: true; interactionEnabled: true; playing: false
                }
            }
        """)
        view = window.findChild(QObject, "autoEditView")
        placed = QSignalSpy(view.pointPlaced)
        with patch.object(fish, "_classify_manual_box") as classify:
            QTest.mouseClick(window, Qt.RightButton, Qt.NoModifier, QPoint(150, 150))
            self.assertEqual(view.property("_rightSelectedBoxIndex"), 0)
            self.assertTrue(data.draftActive)
            self.assertEqual(data.editSpecies, "Chromis viridis")
            # Une correction saisie dans la fiche reste elle aussi intacte.
            data.editSpecies = "Chromis chromis"
            edit = window.findChild(QObject, "editSelectedBoxButton")
            self.assertTrue(edit.isVisible())
            pos = edit.mapToScene(QPointF(edit.width()/2, edit.height()/2)).toPoint()
            QTest.mouseClick(window, Qt.LeftButton, Qt.NoModifier, pos)
            self.assertTrue(view.property("_manualEditActive"))
            QTest.mousePress(window, Qt.LeftButton, Qt.NoModifier, QPoint(200, 200))
            QTest.mouseMove(window, QPoint(228, 218), delay=30)
            QTest.mouseRelease(window, Qt.LeftButton, Qt.NoModifier, QPoint(228, 218))
            self.assertAlmostEqual(fish.lastBoxAtIndex(0)["x2"], 228., places=1)
            self.assertAlmostEqual(fish.lastBoxAtIndex(0)["y2"], 218., places=1)
            self.assertEqual(fish.lastBoxAtIndex(0)["speciesName"], "Chromis viridis")
            self.assertEqual(data.editSpecies, "Chromis chromis")
            self.assertTrue(data.draftActive)
            self.assertEqual(placed.count(), 0)
            QTest.mouseClick(window, Qt.RightButton, Qt.NoModifier, QPoint(150, 150))
            self.assertEqual(data.editSpecies, "Chromis chromis")
            delete = window.findChild(QObject, "deleteSelectedBoxButton")
            self.assertTrue(delete.isVisible())
            pos = delete.mapToScene(QPointF(delete.width()/2, delete.height()/2)).toPoint()
            QTest.mouseClick(window, Qt.LeftButton, Qt.NoModifier, pos)
            self.assertEqual(fish.lastBoxCount, 0)
            self.assertFalse(data.draftActive)
            self.assertEqual(data.selectedBoxIndex, -1)
        classify.assert_not_called()
        fish._replace_last_boxes([original])
        self.assertEqual(fish.lastBoxCount, 0)
        fish._auto_box_edits.clear()

    def test_real_drag_draws_bbox_without_point_or_implicit_selection(self):
        fish = self.controller.fish()
        measure = self.controller.measure()
        measure._frame_count = 10
        measure._frame_width = 400
        measure._frame_height = 300
        measure._playing = False
        fish._last_boxes = []
        fish._manual_boxes = []
        fish._manual_by_frame.clear()
        fish._publish_overlay()

        window = self._create_window("""
            import QtQuick
            import QtQuick.Window
            import AquaMeasure
            Window { width: 400; height: 300
                MeasureStereoView {
                    objectName: "drawManualView"
                    anchors.fill: parent; sideLabel: "G"; isLeft: true
                    videoPath: ""; videoFrameCount: 10
                    frameWidth: 400; frameHeight: 300; fps: 25; absFrame: 0
                    placementEnabled: true; interactionEnabled: true; playing: false
                }
            }
        """)
        view = window.findChild(QObject, "drawManualView")
        placed = QSignalSpy(view.pointPlaced)
        QTest.mousePress(window, Qt.LeftButton, Qt.NoModifier, QPoint(60, 70))
        QTest.mouseMove(window, QPoint(150, 170), delay=30)
        QTest.mouseRelease(window, Qt.LeftButton, Qt.NoModifier, QPoint(150, 170))
        self.app.processEvents()
        self.assertEqual(placed.count(), 0)
        self.assertEqual(fish.lastBoxCount, 1)
        self.assertEqual(view.property("_rightSelectedBoxIndex"), -1)
        self.assertEqual(fish.selectedFishIndex, -1)

        QTest.mouseClick(window, Qt.LeftButton, Qt.NoModifier, QPoint(100, 110))
        self.assertEqual(placed.count(), 1)
        self.assertEqual(view.property("_rightSelectedBoxIndex"), -1)
        QTest.mouseClick(window, Qt.RightButton, Qt.NoModifier, QPoint(100, 110))
        self.assertEqual(view.property("_rightSelectedBoxIndex"), 0)
        QTest.keyClick(window, Qt.Key_Delete)
        self.assertEqual(fish.lastBoxCount, 1)

    def test_real_point_selection_drag_and_keyboard_delete_precede_bbox_edit(self):
        fish = self.controller.fish()
        measure = self.controller.measure()
        measure.clearPoints()
        measure._frame_count = 10
        measure._frame_width = 400
        measure._frame_height = 300
        measure._playing = False
        fish._last_boxes = []
        fish._manual_boxes = []
        fish._manual_by_frame.clear()
        fish.addManualBox(90.0, 90.0, 230.0, 210.0)

        window = self._create_window("""
            import QtQuick
            import QtQuick.Window
            import AquaMeasure
            Window { width: 400; height: 300
                MeasureStereoView {
                    objectName: "pointPriorityView"
                    anchors.fill: parent; sideLabel: "G"; isLeft: true
                    videoPath: ""; videoFrameCount: 10
                    frameWidth: 400; frameHeight: 300; fps: 25; absFrame: 0
                    pointA: Measure.leftPointA; pointB: Measure.leftPointB
                    placementEnabled: Measure.measureStep === 0 || Measure.measureStep === 1
                    interactionEnabled: true; playing: false
                    onPointPlaced: function(x, y) { Measure.placePoint(true, x, y) }
                    onPointMoved: function(index, x, y) {
                        Measure.movePoint(true, index, x, y)
                    }
                    onPointDeleteRequested: function(index) {
                        Measure.removePoint(true, index)
                    }
                    onPointDragFinished: Measure.finishPointDrag()
                }
            }
        """)
        view = window.findChild(QObject, "pointPriorityView")
        pointer = window.findChild(QObject, "measurePointer")
        hint = window.findChild(QObject, "pointLoupeHint")
        self.assertIsNotNone(view)
        self.assertIsNotNone(pointer)
        self.assertIsNotNone(hint)

        QTest.mouseClick(window, Qt.LeftButton, Qt.NoModifier, QPoint(120, 140))
        QTest.mouseClick(window, Qt.LeftButton, Qt.NoModifier, QPoint(180, 160))
        self.assertEqual(measure.measureStep, 2)

        # La bbox est sélectionnée, mais le point B garde la priorité au drag.
        QTest.mouseClick(window, Qt.RightButton, Qt.NoModifier, QPoint(110, 110))
        before_box = dict(fish.lastBoxAtIndex(0))
        QTest.mouseMove(window, QPoint(180, 160))
        self.app.processEvents()
        self.assertEqual(view.property("_hoverPointIndex"), 1)
        self.assertEqual(pointer.property("cursorShape"), Qt.OpenHandCursor)
        self.assertIn("sélectionner B", hint.property("text"))
        QTest.mousePress(window, Qt.LeftButton, Qt.NoModifier, QPoint(180, 160))
        QTest.mouseMove(window, QPoint(195, 175), delay=30)
        QTest.mouseRelease(window, Qt.LeftButton, Qt.NoModifier, QPoint(195, 175))
        self.app.processEvents()
        self.assertEqual(view.property("_selectedPointIndex"), 1)
        self.assertGreater(measure.leftPointB.x(), 180.0)
        self.assertEqual(fish.lastBoxAtIndex(0), before_box)

        # Suppr vise B, jamais la bbox pourtant encore sélectionnée.
        QTest.keyClick(window, Qt.Key_Delete)
        self.app.processEvents()
        self.assertLess(measure.leftPointB.x(), 0.0)
        self.assertGreaterEqual(measure.leftPointA.x(), 0.0)
        self.assertEqual(fish.lastBoxCount, 1)
        self.assertEqual(fish.lastBoxAtIndex(0), before_box)

        # Backspace suit exactement la même règle pour A.
        QTest.mouseClick(window, Qt.LeftButton, Qt.NoModifier, QPoint(120, 140))
        self.assertEqual(view.property("_selectedPointIndex"), 0)
        QTest.keyClick(window, Qt.Key_Backspace)
        self.app.processEvents()
        self.assertLess(measure.leftPointA.x(), 0.0)
        self.assertEqual(measure.measureStep, 0)
        self.assertEqual(fish.lastBoxCount, 1)

    def test_playback_zoom_pan_and_peck_badge_share_image_coordinates(self):
        window = self._create_window("""
            import QtQuick
            import QtQuick.Window
            import AquaMeasure
            Window { width: 400; height: 300
                MeasureStereoView {
                    objectName: "zoomPlaybackView"
                    anchors.fill: parent; sideLabel: "G"; isLeft: true
                    videoPath: ""; videoFrameCount: 10
                    frameWidth: 400; frameHeight: 300; fps: 25; absFrame: 0
                    placementEnabled: false; interactionEnabled: true
                    rectifiedReady: true; playing: false
                }
            }
        """)
        view = window.findChild(QObject, "zoomPlaybackView")
        player = view.findChild(QObject, "stereoVideoPlayer")
        badge = view.findChild(QObject, "peckPopBadge")
        view.setProperty("viewZoom", 4.0)
        view.setProperty("panX", 120.0)
        view.setProperty("panY", -80.0)
        self.app.processEvents()
        for playing in (False, True, False):
            view.setProperty("playing", playing)
            self.app.processEvents()
            self.assertAlmostEqual(player.property("width"), 1600.0)
            self.assertAlmostEqual(player.property("height"), 1200.0)
            self.assertAlmostEqual(player.property("x"), view.property("_contentOx"))
            self.assertAlmostEqual(player.property("y"), view.property("_contentOy"))
        # Le signal backend anime le badge dans le meme repere pixel.
        self.controller.pecks().peckPopped.emit(160, 120, "B", "#f59e0b", "Bouchee")
        self.app.processEvents()
        self.assertTrue(badge.property("visible"))
        self.assertAlmostEqual(badge.property("x") + 22, view.property("_contentOx") + 160 * 4)
        self.assertAlmostEqual(badge.property("y") + 22, view.property("_contentOy") + 120 * 4)

    def test_arrows_nudge_the_selected_point_pixel_by_pixel(self):
        measure = self.controller.measure()
        measure.clearPoints()
        measure._frame_count = 10
        measure._frame_width = 400
        measure._frame_height = 300
        measure._playing = False

        window = self._create_window("""
            import QtQuick
            import QtQuick.Window
            import AquaMeasure
            Window { width: 400; height: 300
                Item {
                    id: transportProbe
                    objectName: "transportProbe"
                    anchors.fill: parent
                    property int arrows: 0
                    Keys.onPressed: function(event) {
                        if (event.key === Qt.Key_Left || event.key === Qt.Key_Right
                                || event.key === Qt.Key_Up || event.key === Qt.Key_Down) {
                            transportProbe.arrows += 1
                            event.accepted = true
                        }
                    }
                    MeasureStereoView {
                        objectName: "nudgeView"
                        anchors.fill: parent; sideLabel: "G"; isLeft: true
                        videoPath: ""; videoFrameCount: 10
                        frameWidth: 400; frameHeight: 300; fps: 25; absFrame: 0
                        pointA: Measure.leftPointA; pointB: Measure.leftPointB
                        placementEnabled: Measure.measureStep === 0 || Measure.measureStep === 1
                        interactionEnabled: true; playing: false
                        onPointPlaced: function(x, y) { Measure.placePoint(true, x, y) }
                        onPointMoved: function(index, x, y) {
                            Measure.movePoint(true, index, x, y)
                        }
                        onPointDeleteRequested: function(index) {
                            Measure.removePoint(true, index)
                        }
                        onPointDragFinished: Measure.finishPointDrag()
                    }
                }
            }
        """)
        view = window.findChild(QObject, "nudgeView")
        probe = window.findChild(QObject, "transportProbe")
        self.assertIsNotNone(view)
        self.assertIsNotNone(probe)

        QTest.mouseClick(window, Qt.LeftButton, Qt.NoModifier, QPoint(120, 140))
        QTest.mouseClick(window, Qt.LeftButton, Qt.NoModifier, QPoint(180, 160))
        QTest.mouseClick(window, Qt.LeftButton, Qt.NoModifier, QPoint(180, 160))
        self.app.processEvents()
        self.assertEqual(view.property("_selectedPointIndex"), 1)

        start = QPointF(measure.leftPointB)
        QTest.keyClick(window, Qt.Key_Right)
        self.app.processEvents()
        self.assertAlmostEqual(measure.leftPointB.x(), start.x() + 1.0, places=3)
        self.assertAlmostEqual(measure.leftPointB.y(), start.y(), places=3)

        QTest.keyClick(window, Qt.Key_Up)
        self.app.processEvents()
        self.assertAlmostEqual(measure.leftPointB.y(), start.y() - 1.0, places=3)

        QTest.keyClick(window, Qt.Key_Left, Qt.ShiftModifier)
        self.app.processEvents()
        self.assertAlmostEqual(measure.leftPointB.x(), start.x() - 9.0, places=3)

        # Tant qu'un point est tenu, les fleches ne remontent pas au transport.
        self.assertEqual(probe.property("arrows"), 0)

        # Echap rend la main : le defilement des images redevient possible.
        QTest.keyClick(window, Qt.Key_Escape)
        self.app.processEvents()
        self.assertEqual(view.property("_selectedPointIndex"), -1)
        QTest.keyClick(window, Qt.Key_Right)
        self.app.processEvents()
        self.assertEqual(probe.property("arrows"), 1)

    def test_nearest_overlapping_point_is_selected_and_b_is_deleted(self):
        fish = self.controller.fish()
        measure = self.controller.measure()
        measure.clearPoints()
        measure._frame_count = 10
        measure._frame_width = 400
        measure._frame_height = 300
        measure._playing = False
        fish._last_boxes = []
        fish._manual_boxes = []

        window = self._create_window("""
            import QtQuick
            import QtQuick.Window
            import AquaMeasure
            Window { width: 400; height: 300
                MeasureStereoView {
                    objectName: "overlappingPointsView"
                    anchors.fill: parent; sideLabel: "G"; isLeft: true
                    videoPath: ""; videoFrameCount: 10
                    frameWidth: 400; frameHeight: 300; fps: 25; absFrame: 0
                    pointA: Measure.leftPointA; pointB: Measure.leftPointB
                    placementEnabled: Measure.measureStep === 0 || Measure.measureStep === 1
                    interactionEnabled: true; playing: false
                    onPointPlaced: function(x, y) { Measure.placePoint(true, x, y) }
                    onPointDeleteRequested: function(index) { Measure.removePoint(true, index) }
                    onPointMoved: function(index, x, y) {
                        Measure.movePoint(true, index, x, y)
                    }
                    onPointDragFinished: Measure.finishPointDrag()
                }
            }
        """)
        view = window.findChild(QObject, "overlappingPointsView")
        QTest.mouseClick(window, Qt.LeftButton, Qt.NoModifier, QPoint(140, 140))
        QTest.mouseClick(window, Qt.LeftButton, Qt.NoModifier, QPoint(159, 140))

        QTest.mouseMove(window, QPoint(150, 140))
        self.app.processEvents()
        self.assertEqual(view.property("_hoverPointIndex"), 1)
        QTest.mouseClick(window, Qt.LeftButton, Qt.NoModifier, QPoint(150, 140))
        self.assertEqual(view.property("_selectedPointIndex"), 1)
        QTest.keyClick(window, Qt.Key_Delete)
        self.assertGreaterEqual(measure.leftPointA.x(), 0.0)
        self.assertLess(measure.leftPointB.x(), 0.0)

    def test_replacing_left_a_and_b_resynchronizes_right_before_compute(self):
        fish = self.controller.fish()
        measure = self.controller.measure()
        measure.clearPoints()
        measure._frame_count = 10
        measure._frame_width = 400
        measure._frame_height = 300
        measure._playing = False
        measure._epipolar = True
        fish._last_boxes = []
        fish._manual_boxes = []

        window = self._create_window("""
            import QtQuick
            import QtQuick.Window
            import AquaMeasure
            Window { width: 800; height: 300
                MeasureStereoView {
                    x: 0; width: 400; height: 300
                    sideLabel: "G"; isLeft: true; videoPath: ""
                    videoFrameCount: 10; frameWidth: 400; frameHeight: 300
                    fps: 25; absFrame: 0
                    pointA: Measure.leftPointA; pointB: Measure.leftPointB
                    placementEnabled: Measure.measureStep === 0 || Measure.measureStep === 1
                    interactionEnabled: true; playing: false
                    onPointPlaced: function(x, y) { Measure.placePoint(true, x, y) }
                    onPointDeleteRequested: function(index) { Measure.removePoint(true, index) }
                    onPointDragFinished: Measure.finishPointDrag()
                }
                MeasureStereoView {
                    x: 400; width: 400; height: 300
                    sideLabel: "D"; isLeft: false; videoPath: ""
                    videoFrameCount: 10; frameWidth: 400; frameHeight: 300
                    fps: 25; absFrame: 0
                    pointA: Measure.rightPointA; pointB: Measure.rightPointB
                    placementEnabled: Measure.measureStep === 2 || Measure.measureStep === 3
                    interactionEnabled: true; playing: false
                    onPointPlaced: function(x, y) { Measure.placePoint(false, x, y) }
                }
            }
        """)
        with patch.object(measure._service, "measure_segment") as compute:
            for point in (
                QPoint(100, 100), QPoint(220, 140),
                QPoint(500, 90), QPoint(620, 130),
            ):
                QTest.mouseClick(window, Qt.LeftButton, Qt.NoModifier, point)
            self.assertEqual(measure.measureStep, 4)

            QTest.mouseClick(window, Qt.LeftButton, Qt.NoModifier, QPoint(100, 100))
            QTest.keyClick(window, Qt.Key_Delete)
            compute.reset_mock()
            QTest.mouseClick(window, Qt.LeftButton, Qt.NoModifier, QPoint(120, 160))
            self.assertEqual(measure.rightPointA.y(), 160.0)
            compute.assert_called_once()

            QTest.mouseClick(window, Qt.LeftButton, Qt.NoModifier, QPoint(220, 140))
            QTest.keyClick(window, Qt.Key_Backspace)
            compute.reset_mock()
            QTest.mouseClick(window, Qt.LeftButton, Qt.NoModifier, QPoint(240, 180))
            self.assertEqual(measure.rightPointB.y(), 180.0)
            compute.assert_called_once()

    def test_four_points_on_b_replace_focused_a_as_measurement_target(self):
        data = self.controller.data()
        fish = self.controller.fish()
        measure = self.controller.measure()
        measure._frame_count = 10
        measure._frame_index = 0
        measure._frame_width = 400
        measure._frame_height = 300
        measure._left = "clip.mp4"
        measure._playing = False
        box_a = {
            "x1": 20.0, "y1": 80.0, "x2": 100.0, "y2": 180.0,
            "track_id": -1, "cls_name": "fish", "conf": 0.9,
        }
        box_b = {
            "x1": 220.0, "y1": 80.0, "x2": 320.0, "y2": 180.0,
            "track_id": -1, "cls_name": "fish", "conf": 0.9,
        }
        fish._last_boxes = [box_a, box_b]
        fish._manual_boxes = []
        fish._manual_by_frame.clear()
        data._selected_ann_id = "ann-a"
        data._selected_row_data = {
            "ann_id": "ann-a",
            "frame_index": 0,
            "frame_index_abs": measure.leftAbsFrame,
            "frame_ref": "absolute",
            "geometry": {
                "x_min": 20.0, "y_min": 80.0,
                "x_max": 100.0, "y_max": 180.0,
            },
        }
        data.selectedAnnIdChanged.emit()
        data.selectedObservationChanged.emit()
        fish.focusAnnotationBox("ann-a", 0, 20, 80, 100, 180, "A")
        fish._publish_overlay()
        self.assertEqual(data.selectedAnnId, "ann-a")

        window = self._create_window("""
            import QtQuick
            import QtQuick.Window
            import AquaMeasure
            Window {
                width: 800; height: 300
                MeasureStereoView {
                    objectName: "leftB"
                    x: 0; y: 0; width: 400; height: 300
                    sideLabel: "G"; isLeft: true; videoPath: ""
                    videoFrameCount: 10; frameWidth: 400; frameHeight: 300
                    fps: 25; absFrame: 0; placementEnabled: true
                    interactionEnabled: true; playing: false
                }
                MeasureStereoView {
                    objectName: "rightB"
                    x: 400; y: 0; width: 400; height: 300
                    sideLabel: "D"; isLeft: false; videoPath: ""
                    videoFrameCount: 10; frameWidth: 400; frameHeight: 300
                    fps: 25; absFrame: 0; placementEnabled: true
                    interactionEnabled: true; playing: false
                }
            }
        """)
        left = window.findChild(QObject, "leftB")
        right = window.findChild(QObject, "rightB")
        left_points = QSignalSpy(left.pointPlaced)
        right_points = QSignalSpy(right.pointPlaced)
        QTest.mouseClick(window, Qt.RightButton, Qt.NoModifier, QPoint(250, 110))
        for point in (
            QPoint(250, 110), QPoint(290, 150),
            QPoint(650, 110), QPoint(690, 150),
        ):
            QTest.mouseClick(window, Qt.LeftButton, Qt.NoModifier, point)

        self.assertEqual(left_points.count(), 2)
        self.assertEqual(right_points.count(), 2)
        self.assertEqual(data.selectedAnnId, "")
        self.assertFalse(fish.focusBox.get("valid"))
        self.assertEqual(data.selectedBoxIndex, 1)
        measure.set_distance_mm(222.0)
        self.assertEqual(
            data._pending_stereo_target["bbox"], data._bbox_signature(box_b),
        )
        self.assertNotIn("ann_id", data._pending_stereo_target)

    def test_count_validation_waits_for_current_frame_or_explicit_entry(self):
        data = self.controller.data()
        data._db_ok = True
        data._media_id = "media-current"
        data._set_frame_count_current(False)
        data.mediaIdChanged.emit()

        window = self._create_window("""
            import QtQuick
            import QtQuick.Window
            import AquaMeasure
            Window {
                width: 800; height: 100
                FrameAbundanceBar { anchors.fill: parent }
            }
        """)
        button = window.findChild(QObject, "validateFrameCountButton")
        self.assertIsNotNone(button)
        self.assertFalse(button.property("actionable"))
        data.frameManualCount = 3
        self.app.processEvents()
        self.assertTrue(button.property("actionable"))

    def test_measure_menu_and_register_label_match_the_guided_workflow(self):
        main_qml = (APP_ROOT / "qml" / "Main.qml").read_text(encoding="utf-8")
        all_qml = "\n".join(
            path.read_text(encoding="utf-8")
            for path in (APP_ROOT / "qml").rglob("*.qml")
        )
        self.assertNotIn("Data.grazeStart", all_qml)
        self.assertNotIn("Data.grazeEnd", all_qml)
        # Les commandes de suivi sont à gauche ; le registre garde les résultats.
        context_qml = (
            APP_ROOT / "qml" / "components" / "ContextSidePanel.qml"
        ).read_text(encoding="utf-8")
        panel_qml = (
            APP_ROOT / "qml" / "components" / "MeasureRegistryPanel.qml"
        ).read_text(encoding="utf-8")
        follow_qml = (APP_ROOT / "qml/components/FishFollowPanel.qml").read_text(encoding="utf-8")
        self.assertNotIn("Fish.beginTrackFollow()", panel_qml)
        self.assertNotIn("FishFollowPanel", context_qml)
        self.assertIn("FishFollowPanel", (APP_ROOT / "qml/components/FishWorkflowPanel.qml").read_text(encoding="utf-8"))
        for source in (follow_qml,):
            self.assertIn("Fish.beginTrackFollow()", source)
            self.assertIn("Fish.finishTrackFollow()", source)
            self.assertIn("Fish.cancelGrazingAnalysis()", source)
        for retire in (
            "Fish.beginGrazingAnalysis()",
            "Fish.finishGrazingAndAnalyze()",
            "Fish.setTrackInAtCurrent()",
            "Fish.setTrackOutAtCurrent()",
            "Fish.analyzeTrackingSegment()",
            "Fish.trackInFrame",
            "Fish.trackOutFrame",
            "Fish.trackFollowMode",
            "Fish.grazingStartMarked",
            "Fish.startAssistedTracking()",
            "Fish.followSelectedFish()",
        ):
            self.assertNotIn(retire, all_qml, f"« {retire} » survit dans le QML")
        # Le menu ne lance plus de suivi : il ne sert qu'au rattrapage d'un
        # decrochage. Un suivi demarre depuis la fiche du poisson, sinon la
        # piste produite ne rejoint aucune observation.
        self.assertNotIn("Suivre le poisson encadré", main_qml)
        for old_action in (
            "Reprendre le suivi du poisson encadré",
            "Numéroter les poissons (suivi automatique)",
        ):
            position = main_qml.index(old_action)
            declaration = main_qml[max(0, position - 220):position]
            self.assertIn("visible: App.currentPage !== 4", declaration)
        # Le panneau doit décrire l'ordre RÉEL : on ouvre la fiche du poisson
        # (rien n'est écrit), on vérifie la taxonomie proposée, on mesure, puis
        # un seul bouton enregistre. « Ajouter ce cadre au registre » nommait
        # l'ancien ordre inverse - créer la ligne d'abord, l'identifier après.
        self.assertIn('qsTr("Enregistrer le poisson")', panel_qml)
        self.assertIn('qsTr("Enregistrer le poisson")', panel_qml)
        self.assertNotIn('qsTr("Ajouter ce cadre au registre")', panel_qml)
        self.assertIn("Data.saveFish(", panel_qml)
        self.assertNotIn("saveTaxonButton", panel_qml)
        # La longueur se voit AVANT l'écriture, et se retire.
        self.assertIn("Data.clearDraftMeasurement()", panel_qml)
        self.assertIn("Data.clearSelection()", panel_qml)
        # La meme action sert aussi aux corrections.
        self.assertNotIn("Valider l'identification", panel_qml)
        self.assertNotIn("validez avec ✓", panel_qml)
        export_qml = (
            APP_ROOT / "qml" / "pages" / "data" / "DataExportTab.qml"
        ).read_text(encoding="utf-8")
        fishial_qml = (
            APP_ROOT / "qml" / "pages" / "data" / "FishialLibraryTab.qml"
        ).read_text(encoding="utf-8")
        self.assertIn("Exporter en COCO", export_qml)
        self.assertIn("Exporter en COCO-VID", export_qml)
        self.assertIn("Exporter en CSV", export_qml)
        self.assertNotIn('id: "fishial"', export_qml)
        self.assertIn("Bibliothèque Fishial locale", fishial_qml)
        self.assertIn("Ajouter à Fishial", fishial_qml)
        self.assertNotIn("exportDbCoco", export_qml)

    def test_project_gallery_uses_backend_eligibility_reason_for_every_row(self):
        data = self.controller.data()
        data._fishial_project_state = "idle"
        data.fishialProjectChanged.emit()
        gallery_rows = [
            {"taxon_node_id": "zero", "scientific_name": "Species zero",
             "rank": "species", "crop_count": 0,
             "promotion_eligible": False,
             "promotion_reason": "Aucune image disponible pour cette espèce."},
            {"taxon_node_id": "threshold", "scientific_name": "Species threshold",
             "rank": "species", "crop_count": 3,
             "pending_reference_count": 3,
             "promotion_eligible": False,
             "promotion_reason": "Seuil insuffisant : 3/5 image(s) validée(s)."},
            {"taxon_node_id": "catalog", "scientific_name": "Species catalog",
             "rank": "species", "crop_count": 8,
             "in_fishial_catalog": True,
             "pending_reference_count": 8,
             "promotion_eligible": True,
             "promotion_reason": "Références locales supplémentaires."},
            {"taxon_node_id": "provisional", "scientific_name": "Species provisoria",
             "rank": "species", "crop_count": 8,
             "pending_reference_count": 8,
             "promotion_eligible": True,
             "promotion_reason": "Espèce éligible à l'enrichissement Fishial local."},
            {"taxon_node_id": "active", "scientific_name": "Species activa",
             "rank": "species", "crop_count": 60, "gallery_ref_count": 20,
             "pending_reference_count": 40, "promotion_min_refs": 5,
             "in_fishial_catalog": True, "promotion_eligible": True},
            {"taxon_node_id": "current", "scientific_name": "Species currenta",
             "rank": "species", "crop_count": 20, "gallery_ref_count": 20,
             "pending_reference_count": 0, "promotion_min_refs": 5,
             "promotion_eligible": True},
        ]
        window = self._create_window("""
            import QtQuick
            import QtQuick.Window
            import AquaMeasure
            Window {
                width: 1200; height: 1800
                FishialLibraryTab { anchors.fill: parent }
            }
        """)
        data._gallery.set_rows(gallery_rows)
        data.galleryChanged.emit()
        self.app.processEvents()
        QTest.qWait(20)
        species_list = window.findChild(QObject, "fishialLibraryList")
        self.assertEqual(data.galleryCount, 6)
        self.assertEqual(species_list.property("count"), 6)
        self.assertEqual((data.fishialPendingImages, data.fishialPendingSpecies), (56, 3))
        expected = (
            (False, "Aucune image disponible"),
            (False, "Seuil insuffisant"),
            (True, ""),
            (True, ""),
            (True, ""),
            (False, "déjà leur référence locale"),
        )
        for index, (actionable, reason) in enumerate(expected):
            species_list.setProperty("currentIndex", index)
            self.app.processEvents()
            QTest.qWait(20)
            current_item = species_list.property("currentItem")
            button = (
                current_item.findChild(QObject, "fishialProjectPromoteButton")
                if current_item is not None else None
            )
            self.assertIsNotNone(button)
            self.assertEqual(button.property("actionable"), actionable)
            if reason:
                self.assertIn(reason, button.property("disabledReason"))
            if index == 2:
                self.assertEqual(button.property("text"), "Ajouter à Fishial")
            if index == 4:
                self.assertEqual(button.property("text"), "Ajouter les nouvelles images")
                with patch.object(data, "_start_fishial_project_promotion") as start:
                    center = button.mapToScene(QPointF(button.width() / 2, button.height() / 2))
                    QTest.mouseClick(window, Qt.LeftButton, Qt.NoModifier, center.toPoint())
                    start.assert_called_once_with("active")
        self.assertEqual(button.property("text"), "À jour")
        bulk = window.findChild(QQuickItem, "fishialPromoteAllButton")
        self.assertTrue(bulk.property("actionable"))
        with patch.object(data, "_start_fishial_project_promotion") as start:
            center = bulk.mapToScene(QPointF(bulk.width() / 2, bulk.height() / 2))
            QTest.mouseClick(window, Qt.LeftButton, Qt.NoModifier, center.toPoint())
            start.assert_called_once_with()
        data._fishial_project_state = "running"
        data.fishialProjectChanged.emit()
        self.app.processEvents()
        self.assertFalse(bulk.property("actionable"))
        data._fishial_project_state = "idle"
        data.fishialProjectChanged.emit()

    def test_behavior_shortcut_enter_and_outside_click_save_and_leave_field(self):
        import fish_annotate as fa

        data = self.controller.data()
        data.loadEventTypes()
        point_type = fa.list_point_event_types()[0]
        previous = point_type.get("shortcut") or ""

        def restore():
            fa.set_behavior_shortcut(point_type["id"], previous)
            data.loadEventTypes()
            self.controller.pecks().loadPointTypes()
        self.addCleanup(restore)

        window = self._create_window("""
            import QtQuick
            import QtQuick.Window
            import AquaMeasure
            Window {
                id: testWindow
                width: 1000; height: 900
                property int outsideClicks: 0
                SettingsPage { anchors.fill: parent; anchors.margins: 20 }
                Rectangle {
                    width: 16; height: 16; color: "blue"
                    MouseArea { anchors.fill: parent; onClicked: testWindow.outsideClicks++ }
                }
            }
        """)

        def field():
            def walk(item):
                if item.objectName() == "behaviorShortcutField" and item.isVisible():
                    return item
                for child in item.childItems():
                    found = walk(child)
                    if found is not None:
                        return found
                return None
            return walk(window.contentItem())

        editor = field()
        self.assertIsNotNone(editor)
        QTest.mouseClick(window, Qt.LeftButton, Qt.NoModifier, QPoint(5, 5))
        self.assertEqual(window.property("outsideClicks"), 1, "baseline")
        window.setProperty("outsideClicks", 0)
        scroll = window.findChild(QObject, "settingsScroll").property("contentItem")
        position = editor.mapToItem(scroll, QPointF(0, 0)).y()
        scroll.setProperty("contentY", max(0, position - 300))
        self.app.processEvents()

        for letter, finish in ((Qt.Key_B, "enter"), (Qt.Key_C, "outside")):
            editor = field()
            center = editor.mapToScene(QPointF(editor.width() / 2, editor.height() / 2))
            QTest.mouseClick(window, Qt.LeftButton, Qt.NoModifier, center.toPoint())
            self.assertTrue(editor.hasActiveFocus())
            QTest.keyClick(window, Qt.Key_A, Qt.ControlModifier)
            QTest.keyClick(window, letter)
            if finish == "enter":
                QTest.keyClick(window, Qt.Key_Return)
            else:
                QTest.mouseClick(window, Qt.LeftButton, Qt.NoModifier, QPoint(5, 5))
            self.app.processEvents()
            self.assertFalse(field().hasActiveFocus(), finish)
            saved = next(row for row in fa.list_point_event_types() if row["id"] == point_type["id"])
            self.assertEqual(saved["shortcut"], "B" if finish == "enter" else "C")
        self.assertEqual(window.property("outsideClicks"), 1)

    def test_project_gallery_threshold_refreshes_model_button_and_reason(self):
        data = self.controller.data()
        persisted = {"min_refs": 5}

        def set_app_settings(**values):
            persisted["min_refs"] = int(values["fishial_min_refs"])

        def list_species_summary(**_kwargs):
            minimum = persisted["min_refs"]
            eligible = 4 >= minimum
            return [{
                "taxon_node_id": "threshold-live",
                "scientific_name": "Species thresholdensis",
                "rank": "species",
                "crop_count": 4,
                "promotion_eligible": eligible,
                "promotion_reason": "" if eligible else (
                    f"Seuil insuffisant : 4/{minimum} image(s) validée(s)."
                ),
                "promotion_min_refs": minimum,
            }]

        old_db_ok = data._db_ok
        data._db_ok = True
        data._fishial_min_refs = 5
        with patch(
            "fish_db_stats.set_app_settings", side_effect=set_app_settings,
        ), patch(
            "fish_db_stats.list_species_summary", side_effect=list_species_summary,
        ):
            data.refreshGallery()
            window = self._create_window("""
                import QtQuick
                import QtQuick.Window
                import AquaMeasure
                Window {
                    width: 800; height: 120
                    ListView {
                        objectName: "fishialThresholdList"
                        anchors.fill: parent
                        model: Data.gallery
                        delegate: Item {
                            width: ListView.view.width
                            height: 40
                            GhostButton {
                                objectName: "fishialThresholdButton"
                                anchors.fill: parent
                                text: scientificName
                                requires: promotionEligible
                                disabledReason: promotionReason
                            }
                        }
                    }
                }
            """)
            self.app.processEvents()
            QTest.qWait(20)
            species_list = window.findChild(QObject, "fishialThresholdList")
            self.assertIsNotNone(species_list)
            self.assertEqual(data.galleryCount, 1)

            def assert_state(minimum, eligible, reason):
                self.app.processEvents()
                QTest.qWait(20)
                index = data._gallery.index(0, 0)
                self.assertEqual(
                    data._gallery.data(index, data._gallery.PromotionMinRefsRole),
                    minimum,
                )
                self.assertEqual(
                    data._gallery.data(index, data._gallery.PromotionEligibleRole),
                    eligible,
                )
                self.assertEqual(
                    data._gallery.data(index, data._gallery.PromotionReasonRole),
                    reason,
                )
                current_item = species_list.property("currentItem")
                self.assertIsNotNone(current_item)
                button = current_item.findChild(QObject, "fishialThresholdButton")
                self.assertIsNotNone(button)
                self.assertEqual(button.property("actionable"), eligible)
                self.assertEqual(button.property("disabledReason"), reason)

            assert_state(5, False, "Seuil insuffisant : 4/5 image(s) validée(s).")
            data.fishialMinRefs = 3
            self.assertEqual(persisted["min_refs"], 3)
            assert_state(3, True, "")
            data.fishialMinRefs = 5
            self.assertEqual(persisted["min_refs"], 5)
            assert_state(5, False, "Seuil insuffisant : 4/5 image(s) validée(s).")
        data._db_ok = old_db_ok

    def test_project_gallery_threshold_persistence_failure_keeps_state_and_reports_error(self):
        data = self.controller.data()

        def list_species_summary(**_kwargs):
            return [{
                "taxon_node_id": "threshold-failure",
                "scientific_name": "Species persistensis",
                "rank": "species",
                "crop_count": 4,
                "promotion_eligible": False,
                "promotion_reason": "Seuil insuffisant : 4/5 image(s) validée(s).",
                "promotion_min_refs": 5,
            }]

        old_db_ok = data._db_ok
        data._db_ok = True
        data._fishial_min_refs = 5
        with patch(
            "fish_db_stats.list_species_summary", side_effect=list_species_summary,
        ), patch(
            "fish_db_stats.set_app_settings", side_effect=OSError("stockage en lecture seule"),
        ):
            data.refreshGallery()
            data.setStatusText("")
            changed = QSignalSpy(data.fishialMinRefsChanged)

            data.fishialMinRefs = 3

            self.assertEqual(data.fishialMinRefs, 5)
            self.assertEqual(changed.count(), 0)
            index = data._gallery.index(0, 0)
            self.assertEqual(
                data._gallery.data(index, data._gallery.PromotionMinRefsRole), 5,
            )
            self.assertIn("Impossible d'enregistrer le seuil Fishial", data.statusText)
            self.assertIn("stockage en lecture seule", data.statusText)
        data._db_ok = old_db_ok

    def test_measure_button_matches_real_target_for_zero_one_two_boxes(self):
        data = self.controller.data()
        fish = self.controller.fish()
        measure = self.controller.measure()
        measure._playing = False
        fish.clearFocusBox()
        fish._manual_boxes = []
        fish._manual_by_frame.clear()
        fish._selected_fish_index = -1
        data.selectBoxIndex(-1)
        window = self._create_window("""
            import QtQuick
            import QtQuick.Window
            import AquaMeasure
            Window {
                width: 600; height: 800
                MeasureRegistryPanel { anchors.fill: parent }
            }
        """)
        button = window.findChild(QObject, "measureSelectedBoxButton")
        box = {
            "x1": 10.0, "y1": 10.0, "x2": 50.0, "y2": 50.0,
            "track_id": 1, "cls_name": "fish", "conf": 0.9,
        }

        fish._replace_last_boxes([])
        fish._publish_overlay()
        self.app.processEvents()
        self.assertFalse(button.property("actionable"))
        self.assertIsNone(fish._measure_target_box()[0])

        fish._replace_last_boxes([box])
        fish._publish_overlay()
        self.app.processEvents()
        self.assertTrue(button.property("actionable"))
        self.assertIsNotNone(fish._measure_target_box()[0])

        fish._replace_last_boxes([box, {**box, "x1": 80.0, "x2": 120.0}])
        fish._publish_overlay()
        data.selectBoxIndex(-1)
        self.app.processEvents()
        self.assertFalse(button.property("actionable"))
        self.assertIsNone(fish._measure_target_box()[0])
        self.assertIn("Cadre à ajouter", button.property("disabledReason"))

        data.selectBoxIndex(1)
        self.app.processEvents()
        self.assertTrue(button.property("actionable"))
        target, index = fish._measure_target_box()
        self.assertEqual(index, 1)
        self.assertEqual(target["x1"], 80.0)

    def test_project_error_beats_old_export_in_real_qml(self):
        data = self.controller.data()
        pro = self.controller.proTools()
        exact_error = "Embedder Fishial indisponible pour ce projet"
        data.subTab = 1
        pro._set_last_export(
            "success", 0, "C:/ancien-export", kind="fishial",
        )
        data._fishial_project_generation = 7
        data._finish_fishial_project_promotion({
            "generation": 7, "ok": False, "error": exact_error,
        })
        window = self._create_window("""
            import QtQuick
            import QtQuick.Window
            import AquaMeasure
            Window {
                width: 1200; height: 800
                DataPage { anchors.fill: parent }
            }
        """)
        self.app.processEvents()
        project = window.findChild(QObject, "fishialProjectStatusLabel")
        status = window.findChild(QObject, "dataPageStatusLine")
        self.assertIsNotNone(project)
        self.assertIsNotNone(status)
        self.assertEqual(project.property("text"), exact_error)
        self.assertEqual(status.property("text"), exact_error)
        self.assertNotIn("ancien-export", status.property("text"))
        pro._set_last_export("idle", kind="fishial")
        data._fishial_project_state = "idle"
        data.fishialProjectChanged.emit()
        data.subTab = 0

    @staticmethod
    def _visual_child(item, name):
        if item.objectName() == name:
            return item
        for child in item.childItems():
            found = MeasureQmlRuntimeTest._visual_child(child, name)
            if found is not None:
                return found
        return None

    def test_one_event_selector_marks_and_removes_an_isolated_point(self):
        pecks, measure, data, ann_id, _track, point = self._prepare_fish_chain(72, with_track=False)
        self._select_registry_row(data, ann_id)
        data.loadEventTypes()
        pecks.setTypeKey(point["key"])
        measure._frame_index = data.selectedFrameIndex
        measure.frameIndexChanged.emit()
        self.controller.currentPage = 4
        window = self._create_window("""
            import QtQuick
            import QtQuick.Window
            import AquaMeasure
            Window { width: 700; height: 900
                Row { anchors.fill: parent
                    FishWorkflowPanel { width: 300; height: parent.height }
                    CompactRegistryPanel { width: 380; height: parent.height }
                }
            }
        """)
        block = window.findChild(QQuickItem, "trackFollowBlock")
        block.setProperty("followMode", 0)
        block.setProperty("behaviorSpan", 0)
        self.app.processEvents()
        button = window.findChild(QQuickItem, "observationEventButton")
        self.assertTrue(button.property("visible"))
        self.assertTrue(button.property("actionable"))
        self.assertIsNone(window.findChild(QObject, "registryBehaviorSelector"))
        self.assertFalse(window.findChild(QQuickItem, "peckMarkButton").property("visible"))
        QMetaObject.invokeMethod(button, "clicked")
        self.app.processEvents()
        self.assertIn(point["key"], data.selectedPointEventKeys)
        self.assertEqual(data.selectedTrackDbId, "")
        self.assertIn("Retirer", button.property("text"))
        QMetaObject.invokeMethod(button, "clicked")
        self.app.processEvents()
        self.assertNotIn(point["key"], data.selectedPointEventKeys)
        self.assertIn("Marquer", button.property("text"))
        QMetaObject.invokeMethod(button, "clicked")
        self.app.processEvents()
        # La suppression depuis la liste reste possible après avoir avancé.
        measure._frame_index += 2
        measure.frameIndexChanged.emit()
        self.app.processEvents()
        remove = self._visual_child(window.contentItem(), "observationEventRemoveButton")
        self.assertIsNotNone(remove)
        QMetaObject.invokeMethod(remove, "clicked")
        self.app.processEvents()
        self.assertNotIn(point["key"], data.selectedPointEventKeys)

    def test_old_intervals_remain_in_summary_without_an_interval_selector(self):
        data = self.controller.data()
        data._selected_ann_id = "historical-ann"
        data._grazing.set_rows([{
            "event_id": "history-1", "track_db_id": "history-track", "external_track_id": 7,
            "frame_start_abs": 10, "frame_end_abs": 20,
            "event_type": "grazing", "event_label": "Broutage",
            "event_symbol": "◆", "event_color": "#f59e0b",
        }])
        data._remember_selected_row({"ann_id": "historical-ann", "track_id": "history-track"}, 0)
        data.selectedAnnIdChanged.emit()
        data.selectedEventsChanged.emit()
        window = self._create_window("""
            import QtQuick
            import QtQuick.Window
            import AquaMeasure
            Window { width: 380; height: 900
                MeasureRegistryPanel { anchors.fill: parent; compact: true }
            }
        """)
        self.assertIn("Broutage", self._visual_child(window.contentItem(), "observationEventLabel").property("text"))
        for name in ("behaviorBlock", "registryIntervalNoticeLabel", "registryBehaviorSelector",
                     "registryIntervalStartButton", "registryIntervalFinishButton"):
            self.assertIsNone(window.findChild(QObject, name))
        data._grazing.set_rows([])
        data.clearSelection()

    def _prepare_peck_fixture(self, external_id: int):
        """Vidéo, piste suivie et type ponctuel dans la base du harnais.

        Les contrôleurs sont partagés par toute la classe : l'état poussé ici
        est remis en place au démontage, sinon les tests suivants hériteraient
        d'une vidéo et d'un média qu'ils n'ont pas demandés.
        """
        import fish_annotate as fa

        from src.annodb.connection import session_scope
        from src.annodb.tracks import (
            add_track_sample,
            get_or_create_track,
            refresh_track_bounds,
        )

        measure = self.controller.measure()
        data = self.controller.data()
        pecks = self.controller.pecks()
        service = measure._service

        media = Path(self._tmp.name) / f"peck_{external_id}.avi"
        if not media.is_file():
            import cv2
            import numpy as np

            writer = cv2.VideoWriter(
                str(media), cv2.VideoWriter_fourcc(*"MJPG"), 25.0, (400, 300),
            )
            self.assertTrue(writer.isOpened())
            for index in range(40):
                frame = np.zeros((300, 400, 3), dtype=np.uint8)
                frame[:, :, 1] = (index * 5 + external_id) % 255
                writer.write(frame)
            writer.release()

        media_id = fa.resolve_media_id(str(media), create=True)
        self.assertTrue(media_id)
        with session_scope() as session:
            track = get_or_create_track(
                session, media_id=media_id, external_track_id=external_id,
            )
            track_id = track.id
            for frame in range(40):
                add_track_sample(
                    session, track_id=track_id, frame_index=frame,
                    cx=150.0, cy=150.0, bbox=(100.0, 100.0, 200.0, 200.0),
                )
            refresh_track_bounds(session, track_id)

        point_type = next(
            (row for row in fa.list_point_event_types()
             if row["label"] == "Bouchée"),
            None,
        )
        created_type = point_type is None
        if created_type:
            point_type = fa.create_behavior_type("Bouchée", "point", "●")
        previous_shortcut = point_type.get("shortcut")
        fa.set_behavior_shortcut(point_type["id"], "B")

        previous = (
            measure._left, measure._frame_count, measure._frame_index,
            service._total_frames, service._left_start,
            service._width, service._height, data._media_id,
        )

        def restore():
            for row in fa.list_behavior_points(str(media)):
                fa.delete_behavior_point(row["event_id"])
            # Le catalogue est partagé par toute la classe : laisser « Bouchée »
            # derrière soi changerait la première ligne de la page Réglages,
            # que d'autres tests inspectent.
            if created_type:
                fa.remove_behavior_type(point_type["id"])
            else:
                fa.set_behavior_shortcut(point_type["id"], previous_shortcut or "")
            self.controller.data().loadEventTypes()
            pecks.loadPointTypes()
            pecks.clearSelection()
            (
                measure._left, measure._frame_count, measure._frame_index,
                service._total_frames, service._left_start,
                service._width, service._height, data._media_id,
            ) = previous
            measure.leftVideoChanged.emit()
            measure.videoMetaChanged.emit()
            measure.frameCountChanged.emit()
            measure.frameIndexChanged.emit()
            data.mediaIdChanged.emit()

        self.addCleanup(restore)

        measure._left = str(media)
        measure._frame_count = 40
        measure._frame_index = 0
        measure._playing = False
        # Timeline alignée sur le fichier : index affiché == frame absolue.
        service._total_frames = 40
        service._left_start = 0
        service._width = 400
        service._height = 300
        measure.leftVideoChanged.emit()
        measure.videoMetaChanged.emit()
        measure.frameCountChanged.emit()
        measure.frameIndexChanged.emit()
        data._media_id = media_id
        data.mediaIdChanged.emit()
        pecks.refresh()
        return pecks, measure, track_id, point_type

    def test_peck_panel_counts_markers_and_timeline_draws_them(self):
        pecks, measure, track_id, _type = self._prepare_peck_fixture(41)
        pecks.selectTrack(track_id)
        for frame in (5, 12, 20):
            measure._frame_index = frame
            measure.frameIndexChanged.emit()
            self.assertTrue(pecks.markAtCurrentFrame())
        self.assertEqual(pecks.markerCount, 3)

        window = self._create_window("""
            import QtQuick
            import QtQuick.Window
            import AquaMeasure
            Window {
                width: 1280; height: 820
                MeasurePage { anchors.fill: parent }
            }
        """)
        self.app.processEvents()

        badge = window.findChild(QObject, "recordedEventCount")
        self.assertIsNotNone(badge)
        self.assertIn("3", badge.property("text"))

        strip = window.findChild(QObject, "measurePeckStrip")
        self.assertIsNotNone(strip)
        self.assertTrue(strip.property("visible"))
        # Les délégués d'un Repeater ne sont pas des enfants QObject de la
        # barre : seul l'arbre visuel les rattache. findChildren les rate.
        drawn = []

        def collect_markers(item):
            for child in item.childItems():
                if child.objectName() == "peckMarker":
                    drawn.append(child)
                collect_markers(child)

        collect_markers(strip)
        self.assertEqual(len(drawn), 3)
        self.assertEqual(
            sorted(int(item.property("markerFrame")) for item in drawn),
            [5, 12, 20],
        )
        # La plage de la piste est teintée : sans elle, trouver le poisson sur
        # une vidéo longue relèverait du hasard.
        band = window.findChild(QObject, "peckRangeBand")
        self.assertIsNotNone(band)
        self.assertTrue(band.property("visible"))

    def test_tracking_strip_wheel_anchors_mouse_and_keeps_marker_clicks(self):
        window = self._create_window("""
            import QtQuick
            import QtQuick.Window
            import AquaMeasure
            Window { width: 900; height: 160
                EventMarkerStrip {
                    objectName: "zoomStrip"
                    x: 50; y: 60; width: 800; height: 34; barMargin: 10
                    zoomEnabled: true; viewKey: "track-1"
                    frameCount: 100001; currentFrame: 95000
                    rangeStart: 19000; rangeEnd: 41000
                    markers: [
                        {eventId: "a", frame: 20000, symbol: "●", color: "red"},
                        {eventId: "b", frame: 40000, symbol: "●", color: "red"},
                        {eventId: "c", frame: 95000, symbol: "●", color: "red"}
                    ]
                }
            }
        """)
        strip = window.findChild(QQuickItem, "zoomStrip")
        activated = QSignalSpy(strip.markerActivated)
        seeks = QSignalSpy(strip.seekRequested)
        # 20 % du rail, exactement sur le premier marqueur (hors origine).
        pos = strip.mapToScene(QPointF(10 + 780 * 0.2, 17))

        def wheel(delta):
            event = QWheelEvent(
                pos, pos, QPoint(), QPoint(0, delta), Qt.NoButton,
                Qt.NoModifier, Qt.ScrollUpdate, False,
            )
            self.app.sendEvent(window, event)
            self.app.processEvents()

        wheel(360)
        self.assertGreater(strip.property("zoomFactor"), 1)
        self.assertAlmostEqual(
            strip.property("viewStart") + 0.2 * strip.property("viewSpan"),
            20000, places=6,
        )
        self.assertEqual(seeks.count(), 0)
        self.assertFalse(window.findChild(QObject, "peckPlayhead").property("visible"))
        def marker_items(item):
            for child in item.childItems():
                if child.objectName() == "peckMarker":
                    yield child
                yield from marker_items(child)

        drawn = {item.property("eventId"): item for item in marker_items(strip)}
        self.assertFalse(drawn["c"].isVisible())
        self.assertAlmostEqual(
            drawn["b"].x() - drawn["a"].x(),
            780 * 0.2 * strip.property("zoomFactor"), places=6,
        )
        QTest.mouseClick(window, Qt.LeftButton, Qt.NoModifier, pos.toPoint())
        self.assertEqual(activated.count(), 1)
        self.assertEqual(activated.at(0), ["a", 20000])

        # Un clic dans le rail cherche une image de la fenêtre zoomée.
        blank = strip.mapToScene(QPointF(10 + 780 * 0.6, 17))
        expected = round(strip.property("viewStart") + 0.6 * strip.property("viewSpan"))
        QTest.mouseClick(window, Qt.LeftButton, Qt.NoModifier, blank.toPoint())
        self.assertEqual(seeks.count(), 1)
        self.assertEqual(seeks.at(0), [expected])
        wheel(-360)
        self.assertAlmostEqual(strip.property("zoomFactor"), 1)
        self.assertAlmostEqual(strip.property("viewStart"), 0)
        wheel(240)
        strip.setProperty("viewKey", "track-2")
        self.assertEqual(strip.property("zoomFactor"), 1)
        self.assertEqual(strip.property("viewStart"), 0)

    def test_tracking_strip_zoom_limits_and_empty_video(self):
        window = self._create_window("""
            import QtQuick
            import QtQuick.Window
            import AquaMeasure
            Window { width: 800; height: 100
                EventMarkerStrip {
                    objectName: "limitsStrip"; anchors.fill: parent
                    frameCount: 101; zoomEnabled: true; barMargin: 0
                    function zoomForTest(factor, x) { zoomAt(factor, x) }
                }
            }
        """)
        strip = window.findChild(QQuickItem, "limitsStrip")

        def zoom(factor, x):
            QMetaObject.invokeMethod(strip, "zoomForTest", Qt.DirectConnection,
                                     Q_ARG("QVariant", factor), Q_ARG("QVariant", x))

        zoom(1e9, 800)
        self.assertEqual(strip.property("viewSpan"), 1)
        self.assertEqual(strip.property("viewEnd"), 100)
        zoom(1e-9, 0)
        self.assertEqual(strip.property("viewStart"), 0)
        self.assertEqual(strip.property("viewSpan"), 100)
        zoom(1e9, 0)
        self.assertEqual(strip.property("viewStart"), 0)
        strip.setProperty("frameCount", 1)
        zoom(2, 400)
        self.assertEqual(strip.property("zoomFactor"), 1)
        self.assertEqual(strip.property("viewSpan"), 0)
        strip.setProperty("frameCount", 0)
        zoom(2, 400)
        self.assertEqual(strip.property("viewStart"), 0)
        strip.setProperty("frameCount", 101)
        strip.setProperty("zoomEnabled", False)
        zoom(2, 400)
        self.assertEqual(strip.property("zoomFactor"), 1)

    def test_track_label_is_not_a_continuous_bite(self):
        pecks, measure, track_id, point_type = self._prepare_peck_fixture(74)
        pecks.selectTrack(track_id)
        measure._frame_index = 9
        measure.frameIndexChanged.emit()
        window = self._create_window("""
            import QtQuick
            import QtQuick.Window
            import AquaMeasure
            Window { width: 600; height: 400
                MeasureStereoView {
                    anchors.fill: parent; isLeft: true; sideLabel: "Gauche"
                    videoPath: ""; videoFrameCount: 40; frameWidth: 400; frameHeight: 300
                    fps: 25; absFrame: Measure.leftAbsFrame
                    placementEnabled: false; interactionEnabled: true
                }
            }
        """)
        canvas = window.findChild(QObject, "videoOverlayCanvas")
        self.assertNotIn("Bouchée", canvas.property("peckLabel"))
        self.assertFalse(canvas.property("peckMarked"))
        self.assertTrue(pecks.markAtCurrentFrame())
        self.app.processEvents()
        self.assertIn("Bouchée", canvas.property("peckLabel"))
        self.assertTrue(canvas.property("peckMarked"))
        measure._frame_index = 10
        measure.frameIndexChanged.emit()
        self.app.processEvents()
        self.assertNotIn("Bouchée", canvas.property("peckLabel"))
        self.assertFalse(canvas.property("peckMarked"))
        self.assertTrue(pecks.markAtCurrentFrame())
        self.assertEqual(pecks.markerCount, 2)

    def test_peck_shortcut_marks_and_shift_removes_on_the_measure_page(self):
        pecks, measure, track_id, point_type = self._prepare_peck_fixture(42)
        self.assertEqual(point_type["scope"], "instant")
        pecks.selectTrack(track_id)
        measure._frame_index = 9
        measure.frameIndexChanged.emit()
        self.controller.currentPage = 4

        window = self._create_window("""
            import QtQuick
            import QtQuick.Window
            import AquaMeasure
            Window {
                width: 1280; height: 820
                MeasurePage { anchors.fill: parent }
            }
        """)
        self.app.processEvents()

        hint = window.findChild(QObject, "transportKeysHint")
        self.assertIsNotNone(hint)
        self.assertIn("B : Bouchée", hint.property("text"))

        QTest.keyClick(window, Qt.Key_B)
        self.assertEqual(pecks.markerCount, 1)
        self.assertEqual(pecks.markers[0]["frameAbs"], 9)

        # Le transport garde la priorité : la lettre ne vole pas les flèches.
        QTest.keyClick(window, Qt.Key_Right)
        self.assertEqual(measure.frameIndex, 10)
        QTest.keyClick(window, Qt.Key_B)
        self.assertEqual(pecks.markerCount, 2)

        QTest.keyClick(window, Qt.Key_B, Qt.ShiftModifier)
        self.assertEqual(pecks.markerCount, 1)
        self.assertEqual(pecks.markers[0]["frameAbs"], 9)

    def test_peck_click_on_the_track_box_marks_the_displayed_frame(self):
        pecks, measure, track_id, _type = self._prepare_peck_fixture(43)
        fish = self.controller.fish()
        fish._last_boxes = []
        fish._manual_boxes = []
        fish._manual_by_frame.clear()
        fish._publish_overlay()
        pecks.selectTrack(track_id)
        measure._frame_index = 15
        measure.frameIndexChanged.emit()
        # Les contrôleurs sont partagés : un test précédent peut avoir laissé
        # des points de mesure posés.
        measure.clearPoints()
        self.assertEqual(measure.measureStep, 0)

        window = self._create_window("""
            import QtQuick
            import QtQuick.Window
            import AquaMeasure
            Window {
                width: 400; height: 300
                MeasureStereoView {
                    objectName: "leftView"
                    anchors.fill: parent
                    sideLabel: "G"; isLeft: true; videoPath: ""
                    videoFrameCount: 40; frameWidth: 400; frameHeight: 300
                    fps: 25; absFrame: 15; placementEnabled: true
                    interactionEnabled: true; playing: false
                    onPointPlaced: function(x, y) { Measure.placePoint(true, x, y) }
                }
            }
        """)
        self.app.processEvents()

        # La bbox de la piste couvre (100,100)-(200,200) : échelle 1, le centre
        # de la vue tombe dedans.
        QTest.mouseClick(window, Qt.LeftButton, Qt.NoModifier, QPoint(150, 150))
        self.assertEqual(pecks.markerCount, 1)
        self.assertEqual(pecks.markers[0]["frameAbs"], 15)

        # Hors de la boîte, le clic reste un clic de mesure : sinon armer une
        # piste rendrait la mesure stéréo impossible.
        QTest.mouseClick(window, Qt.LeftButton, Qt.NoModifier, QPoint(20, 20))
        self.assertEqual(pecks.markerCount, 1)
        self.assertEqual(measure.measureStep, 1)
        measure.clearPoints()


    # ── Chaîne du poisson : bandeau, suivi In/Out, bouchées ────────────

    def _select_registry_row(self, data, ann_id: str):
        data.refreshRegistry()
        row = next(
            i for i in range(data.registry.rowCount())
            if data.registry.row_at(i).get("ann_id") == ann_id
        )
        data.loadRegistryRow(row)
        self.app.processEvents()

    def _prepare_fish_chain(self, external_id: int, *, with_track: bool = True):
        """Un poisson du registre, sa piste, sa taille - l'état du client.

        Rejoue le parcours décrit mot pour mot : le poisson est enregistré et
        mesuré, il porte sa piste, et les bouchées se posent dessus.
        """
        import fish_annotate as fa

        pecks, measure, track_id, point_type = self._prepare_peck_fixture(
            external_id,
        )
        data = self.controller.data()
        created = fa.add_observation(
            str(measure.leftVideo),
            10,
            {"x1": 100.0, "y1": 100.0, "x2": 200.0, "y2": 200.0},
            source="manual",
            measurement_mm=154.2,
            track_id=track_id if with_track else None,
            frame_ref="absolute",
        )
        ann_id = created["ann_id"]

        def drop_observation():
            try:
                fa.delete_observation(ann_id)
            except Exception:
                pass
            data.clearSelection()
            data.refreshRegistry()

        self.addCleanup(drop_observation)
        self._select_registry_row(data, ann_id)
        # « Valider l'identification » : le geste réel qui pose le taxon.
        data.saveSelectedRow("Acanthuridae", "", "")
        self._select_registry_row(data, ann_id)
        return pecks, measure, data, ann_id, track_id, point_type

    def test_registry_event_text_updates_after_mark_and_delete(self):
        pecks, measure, data, ann_id, track_id, kind = self._prepare_fish_chain(81)
        window = self._create_window("""
            import QtQuick
            import QtQuick.Window
            import AquaMeasure
            Window { width: 620; height: 900
                MeasureRegistryPanel { anchors.fill: parent; compact: true }
            }
        """)
        def event_labels():
            found = []
            def walk(item):
                for child in item.childItems():
                    if child.objectName() == "registryEventText" and child.isVisible():
                        found.append(child)
                    walk(child)
            walk(window.contentItem())
            return found
        for frame in (5, 12, 20):
            measure._frame_index = frame
            measure.frameIndexChanged.emit()
            self.assertTrue(pecks.markAtCurrentFrame(), pecks.statusText)
        self.app.processEvents()
        label = next(item for item in event_labels() if "× 3" in item.property("text"))
        self.assertIn("Suivi × 1", label.property("text"))
        self.assertGreaterEqual(label.parentItem().height(), label.implicitHeight())
        for width in (380, 300):
            window.setWidth(width)
            self.app.processEvents()
            label = next(item for item in event_labels() if "× 3" in item.property("text"))
            self.assertGreaterEqual(label.width(), 88)
            self.assertGreaterEqual(label.parentItem().height(), label.implicitHeight())
        pecks.removeMarker(pecks.markers[0]["eventId"])
        self.app.processEvents()
        texts = [item.property("text") for item in event_labels()]
        self.assertTrue(any("× 2" in text for text in texts), texts)
        self.assertFalse(any("× 3" in text for text in texts), texts)

    def test_progress_banner_shows_the_four_milestones_of_the_fish(self):
        """« À la fin on obtient un poisson : sa taxonomie, sa taille, son
        tracking et ses broutes. » Le bandeau doit le dire d'un coup d'œil.
        """
        pecks, measure, data, ann_id, track_id, _type = self._prepare_fish_chain(51)
        for frame in (5, 12, 20):
            measure._frame_index = frame
            measure.frameIndexChanged.emit()
            self.assertTrue(pecks.markAtCurrentFrame(), pecks.statusText)
        self.assertEqual(pecks.markerCount, 3)
        self._select_registry_row(data, ann_id)

        window = self._create_window("""
            import QtQuick
            import QtQuick.Window
            import AquaMeasure
            Window { width: 380; height: 900
                MeasureRegistryPanel { anchors.fill: parent; compact: true }
            }
        """)
        banner = window.findChild(QObject, "fishProgressBanner")
        self.assertIsNotNone(banner)
        self.assertTrue(banner.property("visible"))

        # Les délégués d'un Repeater ne sont pas des enfants QObject du
        # bandeau : seul l'arbre visuel les rattache.
        pills = {}

        def collect(item):
            for child in item.childItems():
                if child.objectName().startswith("banner"):
                    pills[child.objectName()] = child
                collect(child)

        collect(banner)
        self.assertEqual(
            sorted(pills),
            ["bannerLength", "bannerPecks", "bannerTaxon", "bannerTrack"],
        )
        for name, pill in pills.items():
            self.assertTrue(pill.isVisible(), name)
            self.assertGreater(pill.width(), 0.0, name)

        self.assertTrue(banner.property("taxonDone"))
        self.assertIn("Acanthuridae", banner.property("taxonText"))
        self.assertEqual(banner.property("lengthText"), "154.2 mm")
        self.assertEqual(banner.property("trackText"), "Piste #51")
        self.assertEqual(banner.property("peckText"), "3 bouchée(s)")

        # Retirer une bouchée doit se voir sur le bandeau sans reselectionner.
        pecks.removeMarker(pecks.markers[0]["eventId"])
        self.app.processEvents()
        self.assertEqual(banner.property("peckText"), "2 bouchée(s)")

    def test_progress_banner_shows_short_paths_without_calling_them_gaps(self):
        """Mesurer et s'arrêter là est un parcours légitime : jalons éteints,
        aucune alerte, aucun libellé qui réclame la suite.
        """
        _pecks, _measure, data, ann_id, _track, _type = self._prepare_fish_chain(
            52, with_track=False,
        )
        self._select_registry_row(data, ann_id)

        window = self._create_window("""
            import QtQuick
            import QtQuick.Window
            import AquaMeasure
            Window { width: 300; height: 900
                MeasureRegistryPanel { anchors.fill: parent; compact: true }
            }
        """)
        banner = window.findChild(QObject, "fishProgressBanner")
        self.assertIsNotNone(banner)
        self.assertTrue(banner.property("visible"))
        self.assertTrue(banner.property("taxonDone"))
        self.assertIn("Acanthuridae", banner.property("taxonText"))
        self.assertEqual(banner.property("lengthText"), "154.2 mm")
        self.assertEqual(banner.property("trackText"), "Piste -")
        self.assertEqual(banner.property("peckText"), "Bouchées -")
        self.assertFalse(banner.property("trackDone"))

        # Aucun poisson désigné : le bandeau disparaît au lieu d'afficher
        # quatre jalons vides dans un volet qui manque de hauteur.
        data.clearSelection()
        self.app.processEvents()
        self.assertFalse(banner.property("hasFish"))
        self.assertFalse(banner.property("visible"))

    def test_peck_block_targets_the_track_of_the_selected_fish(self):
        """Plus rien à choisir : la piste du poisson est visée d'office."""
        pecks, measure, data, ann_id, track_id, _type = self._prepare_fish_chain(53)
        pecks.clearSelection()
        measure._frame_index = 7
        measure.frameIndexChanged.emit()
        self.app.processEvents()

        window = self._create_window("""
            import QtQuick
            import QtQuick.Window
            import AquaMeasure
            Window { width: 380; height: 900
                FishFollowPanel { anchors.fill: parent; compact: true }
            }
        """)
        self.app.processEvents()
        self.assertEqual(pecks.selectedTrackId, track_id)
        self.assertTrue(pecks.armed)
        # Le calage ne doit pas déplacer la vidéo : choisir une ligne du
        # registre a déjà positionné l'image sur l'observation.
        self.assertEqual(measure.frameIndex, 7)

        track_list = window.findChild(QObject, "peckTrackList")
        fish_track = window.findChild(QObject, "peckFishTrackLabel")
        no_track = window.findChild(QObject, "peckNoFishTrackLabel")
        for widget in (track_list, fish_track, no_track):
            self.assertIsNotNone(widget)
        self.assertFalse(track_list.property("visible"))
        self.assertTrue(fish_track.property("visible"))
        self.assertIn("Piste #53", fish_track.property("text"))
        self.assertFalse(no_track.property("visible"))

        # Un poisson sans piste désarme le bloc : sinon un raccourci clavier
        # poserait la bouchée sur la piste du poisson précédent.
        import fish_annotate as fa

        bare = fa.add_observation(
            str(measure.leftVideo),
            11,
            {"x1": 10.0, "y1": 10.0, "x2": 40.0, "y2": 40.0},
            source="manual",
            frame_ref="absolute",
        )
        self.addCleanup(lambda: fa.delete_observation(bare["ann_id"]))
        self._select_registry_row(data, bare["ann_id"])
        self.assertEqual(pecks.selectedTrackId, "")
        self.assertFalse(pecks.armed)
        window.findChild(QObject, "trackFollowBlock").setProperty("followMode", 0)
        self.app.processEvents()
        self.assertTrue(no_track.property("visible"))
        self.assertIn("Image de l’observation", no_track.property("text"))
        self.assertFalse(fish_track.property("visible"))
        self.assertFalse(track_list.property("visible"))

    def test_track_follow_is_the_only_tracking_gesture_of_the_panel(self):
        """Un seul cycle, une seule paire de boutons, une annulation qui marche."""
        pecks, measure, data, ann_id, _track, _type = self._prepare_fish_chain(
            54, with_track=False,
        )
        fish = self.controller.fish()
        fish._set_busy(False)
        fish.cancelGrazingAnalysis()
        # « In » ramène l'affichage sur l'image du poisson ; la détection
        # automatique à la pause s'arme alors sur un minuteur de 350 ms et
        # rend le contrôleur occupé au milieu des assertions. Le geste
        # n'en dépend pas : on la coupe pour ce test.
        previous_auto = fish.autoOnPause
        fish.autoOnPause = False
        fish._auto_detect_timer.stop()
        self.addCleanup(setattr, fish, "autoOnPause", previous_auto)
        self._select_registry_row(data, ann_id)

        window = self._create_window("""
            import QtQuick
            import QtQuick.Window
            import AquaMeasure
            Window { width: 380; height: 900
                FishFollowPanel { anchors.fill: parent; compact: true }
            }
        """)
        block = window.findChild(QObject, "trackFollowBlock")
        in_btn = window.findChild(QObject, "trackFollowInButton")
        out_btn = window.findChild(QObject, "trackFollowOutButton")
        cancel = window.findChild(QObject, "trackFollowCancelButton")
        for widget in (block, in_btn, out_btn, cancel):
            self.assertIsNotNone(widget)
        # Le doublon « Début / Fin et analyser » n'est plus dans le panneau :
        # c'est ce qui obligeait à inventer une broute pour avoir une piste.
        self.assertIsNone(
            window.findChild(QObject, "registryIntervalStartButton"),
        )
        self.assertIsNone(
            window.findChild(QObject, "registryIntervalFinishButton"),
        )

        self.assertTrue(block.property("visible"))
        self.assertTrue(in_btn.property("actionable"))
        self.assertFalse(out_btn.property("actionable"))
        self.assertIn(
            "Début (In)", out_btn.property("disabledReason"),
        )
        self.assertFalse(cancel.property("visible"))

        fish.beginTrackFollow()
        self.app.processEvents()
        self.assertTrue(fish.trackFollowStartMarked)
        self.assertTrue(fish.trackFollowActive)
        self.assertTrue(
            out_btn.property("actionable"),
            f"busy={fish.busy} état={fish.grazingWorkflowState}",
        )
        self.assertFalse(in_btn.property("actionable"))
        # « Annuler le suivi » doit rester atteignable : c'est la seule sortie
        # une fois le In posé.
        self.assertTrue(cancel.property("visible"))

        fish.cancelGrazingAnalysis()
        self.app.processEvents()
        self.assertFalse(fish.trackFollowActive)
        self.assertFalse(cancel.property("visible"))
        self.assertTrue(in_btn.property("actionable"))

        # Sans poisson enregistré, le bloc disparaît : le suivi se rattache à
        # une fiche, il n'a rien à faire sur un cadre nu.
        data.clearSelection()
        self.app.processEvents()
        self.assertFalse(block.property("visible"))

    def test_follow_and_track_points_share_one_section(self):
        pecks, measure, data, ann_id, track_id, point = self._prepare_fish_chain(73)
        self._select_registry_row(data, ann_id)
        pecks.setTypeKey(point["key"])
        self.controller.currentPage = 4
        window = self._create_window("""
            import QtQuick
            import QtQuick.Window
            import AquaMeasure
            Window { width: 820; height: 900
                Row { anchors.fill: parent
                    FishWorkflowPanel { width: 420; height: parent.height }
                    CompactRegistryPanel { width: 380; height: parent.height }
                }
            }
        """)
        registry = window.findChild(QObject, "compactRegistryPanel")
        for name in ("trackFollowInButton", "peckTypeSelector", "peckMarkButton"):
            self.assertIsNone(registry.findChild(QObject, name))
        self.assertIsNone(registry.findChild(QObject, "peckMarkerList"))
        self.assertIsNotNone(window.findChild(QObject, "peckMarkerList"))
        block = window.findChild(QQuickItem, "trackFollowBlock")
        marker = window.findChild(QQuickItem, "peckMarkButton")
        self.assertIsNone(window.findChild(QObject, "contextTrackFollowInButton"))
        self.assertEqual(len(window.findChildren(QObject, "trackFollowInButton")), 1)
        self.assertEqual(len(window.findChildren(QObject, "peckTypeSelector")), 1)
        self.assertFalse(window.findChild(QQuickItem, "observationEventButton").property("visible"))
        self.assertTrue(marker.property("visible"))
        parent = marker.parentItem()
        while parent is not None and parent is not block:
            parent = parent.parentItem()
        self.assertIs(parent, block)
        measure._frame_index = 12
        measure.frameIndexChanged.emit()
        QMetaObject.invokeMethod(marker, "clicked")
        self.app.processEvents()
        self.assertEqual(pecks.markerCount, 1)
        self.assertEqual(data.selectedTrackDbId, track_id)

    def test_registry_panel_never_overflows_at_380_nor_at_300(self):
        """Le volet fait 380 px, 300 px replié : rien ne doit en sortir.

        Deux débordements ont déjà été signalés par le client. La vérification
        porte sur l'arbre visuel entier, pas sur les seuls blocs ajoutés.
        """
        pecks, measure, data, ann_id, track_id, _type = self._prepare_fish_chain(55)
        # Le calage automatique n'a lieu qu'une fois le panneau chargé : sans
        # cette sélection, aucun marqueur ne serait posé et la liste des
        # marqueurs, la plus large du volet, resterait hors du contrôle.
        pecks.selectTrack(track_id, False)
        for frame in (5, 12, 20):
            measure._frame_index = frame
            measure.frameIndexChanged.emit()
            self.assertTrue(pecks.markAtCurrentFrame(), pecks.statusText)
        self.assertEqual(pecks.markerCount, 3)
        self._select_registry_row(data, ann_id)

        def overflowing(panel):
            """Éléments visibles dont le bord sort du panneau, chemin compris.

            Le chemin est dans le message : sans lui, un futur débordement
            renvoie un « QQuickItem » anonyme et il faut tout réinstrumenter.

            Un `QQuickItem` nu sans enfant ne peint rien : la ListView en garde
            un en réserve pour recycler ses délégués, avec une largeur héritée
            d'un état antérieur. Le signaler ferait échouer le test sur un
            objet invisible ; ses vrais délégués, eux, restent vérifiés.
            """
            out = []

            def paints(item):
                return (
                    item.metaObject().className() != "QQuickItem"
                    or bool(item.childItems())
                )

            def walk(item, offset_x, path):
                for child in item.childItems():
                    if not child.isVisible() or child.width() <= 0:
                        continue
                    left = offset_x + child.x()
                    here = "%s/%s[%s]" % (
                        path,
                        child.objectName() or child.metaObject().className(),
                        round(child.implicitWidth(), 1),
                    )
                    outside = (
                        left < -1.0
                        or left + child.width() > panel.width() + 1.0
                    )
                    if outside and paints(child):
                        out.append(
                            (here, round(left, 1), round(left + child.width(), 1))
                        )
                    walk(child, left, here)

            walk(panel, 0.0, "")
            return out

        for width in (380, 300):
            window = self._create_window("""
                import QtQuick
                import QtQuick.Window
                import AquaMeasure
                Window { width: %d; height: 820
                    MeasureRegistryPanel { anchors.fill: parent; compact: true }
                }
            """ % width)
            self.app.processEvents()
            QTest.qWait(60)
            panel = window.findChild(QQuickItem, "measureRegistryPanel")
            self.assertIsNotNone(panel)
            self.assertEqual(int(panel.width()), width)
            banner = window.findChild(QObject, "fishProgressBanner")
            self.assertTrue(banner.property("visible"))
            self.maxDiff = None
            self.assertEqual(overflowing(panel), [], f"débordement à {width} px")


if __name__ == "__main__":
    unittest.main()
