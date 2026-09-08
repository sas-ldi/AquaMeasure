"""Le bouton de fin de calibration ouvre vraiment la bonne paire en Mesure."""

from __future__ import annotations

import json
import os
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
APP_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(APP_ROOT))
import main as app_main

app_main._setup_paths()

import cv2
import numpy as np
from PySide6.QtCore import QObject, QPointF, Qt, QUrl
from PySide6.QtGui import QFont, QFontDatabase
from PySide6.QtQml import QQmlApplicationEngine, QQmlComponent
from PySide6.QtQuickControls2 import QQuickStyle
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication

from src.controllers.app_controller import AppController
from src.controllers.fish_controller import FishController
from src.imaging.frame_image_provider import FrameImageProvider


class CalibrationMeasureButtonQmlTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])
        QQuickStyle.setStyle("Basic")
        if os.name == "nt":
            for filename in ("segoeui.ttf", "segoeuib.ttf", "consola.ttf"):
                QFontDatabase.addApplicationFont(str(Path("C:/Windows/Fonts") / filename))
            cls.app.setFont(QFont("Segoe UI"))
        cls.tmp = tempfile.TemporaryDirectory(prefix="calibration_button_")
        cls.root = Path(cls.tmp.name)
        (cls.root / "storage.json").write_text(
            json.dumps({"data_root": str(cls.root)}), encoding="utf-8"
        )
        cls.env = patch.dict(os.environ, {
            "FISH_VISION_DB": str(cls.root / "annotations.db"),
            "FISH_VISION_SETTINGS": str(cls.root / "settings.json"),
            "AQUAMEASURE_STORAGE_CONFIG": str(cls.root / "storage.json"),
        })
        cls.env.start()
        cls.cam = cls.root / "camera_parameters"
        cls.cam.mkdir()
        cls.left, cls.right, cls.previous = [cls.root / name for name in
                                              ("left.avi", "right.avi", "previous.avi")]
        for number, path in enumerate((cls.left, cls.right, cls.previous)):
            writer = cv2.VideoWriter(str(path), cv2.VideoWriter_fourcc(*"MJPG"), 30, (64, 48))
            assert writer.isOpened(), str(path)
            for frame in range(12):
                writer.write(np.full((48, 64, 3), number * 40 + frame, np.uint8))
            writer.release()
        cls.warmup = patch.object(FishController, "warm_fishial")
        cls.auto_detect = patch.object(FishController, "_schedule_auto_detect")
        cls.warmup.start()
        cls.auto_detect.start()
        cls.controller = AppController()
        cls.images = FrameImageProvider()
        cls.controller.measure().set_image_provider(cls.images)
        cls.controller.calibration().set_image_provider(cls.images)
        cls.engine = QQmlApplicationEngine()
        cls.engine.addImportPath(str(app_main._prepare_qml_module(APP_ROOT)))
        cls.engine.addImageProvider("frames", cls.images)
        for name, value in (
            ("App", cls.controller), ("Measure", cls.controller.measure()),
            ("Calib", cls.controller.calibration()), ("Sync", cls.controller.sync()),
            ("Settings", cls.controller.settings()), ("AppSansFont", "Segoe UI"),
            ("AppMonoFont", "Consolas"),
        ):
            cls.engine.rootContext().setContextProperty(name, value)
        cls.warnings = []
        cls.engine.warnings.connect(lambda errors: cls.warnings.extend(str(e) for e in errors))

    @classmethod
    def tearDownClass(cls):
        cls.controller.measure().resetForNewSession()
        cls.engine.deleteLater()
        cls.controller.deleteLater()
        cls.app.processEvents()
        from src.annodb import connection
        if connection._engine is not None:
            connection._engine.dispose()
            connection._engine = connection._SessionLocal = None
        connection._migrated_paths.clear()
        cls.auto_detect.stop()
        cls.warmup.stop()
        cls.env.stop()
        cls.tmp.cleanup()

    def setUp(self):
        self.controller.currentPage = 3
        self.controller.calibration()._busy = False
        self.controller.measure().resetForNewSession()
        self.warnings.clear()
        mtx = np.array([[60., 0, 32.], [0, 60., 24.], [0, 0, 1.]])
        for name, value in {
            "mtx1.npy": mtx, "mtx2.npy": mtx, "dist1.npy": np.zeros(5),
            "dist2.npy": np.zeros(5), "R.npy": np.eye(3),
            "T.npy": np.array([[-600.], [0.], [0.]]),
            "stereo_rmse.npy": np.array([0.87]), "sync_frames.npy": np.array([0, 0]),
        }.items():
            np.save(self.cam / name, value)
        (self.cam / "videos.txt").write_text(f"{self.left}\n{self.right}\n", encoding="utf-8")
        self.controller.calibration().importFromSync(str(self.left), str(self.right))
        self.controller.calibration().savedCalibrationChanged.emit()
        self.component = QQmlComponent(self.engine)
        self.component.setData(b"""
            import QtQuick
            import QtQuick.Window
            import AquaMeasure
            Window { width: 480; height: 920; color: "#0b1217"
                CalibResultsPanel { anchors.fill: parent }
            }
        """, QUrl("inline:calibration-measure-button.qml"))
        while self.component.isLoading():
            QTest.qWait(20)
        self.window = self.component.create()
        self.assertIsNotNone(self.window, [str(e) for e in self.component.errors()])
        self.window.show()
        QTest.qWait(80)
        self.button = self.window.findChild(QObject, "calibrationGoToMeasureButton")
        self.assertIsNotNone(self.button)

    def tearDown(self):
        self.window.close()
        self.window.deleteLater()
        self.component.deleteLater()
        self.app.processEvents()

    def click_button(self):
        pos = self.button.mapToScene(QPointF(self.button.width() / 2, self.button.height() / 2))
        self.assertTrue(0 < pos.y() < self.window.height(), pos)
        QTest.mouseClick(self.window, Qt.LeftButton, Qt.NoModifier, pos.toPoint())
        QTest.qWait(60)

    def assert_measure_open(self):
        self.assertEqual(self.controller.currentPage, 4, self.warnings)
        measure = self.controller.measure()
        self.assertEqual(Path(measure.leftVideo), self.left)
        self.assertEqual(Path(measure.rightVideo), self.right)
        self.assertEqual(measure.frameCount, 12)
        self.assertTrue(measure._service.calibration_loaded())
        self.assertIn("RMSE 0.87 px", measure.calibrationSummary)
        self.assertEqual(self.warnings, [])

    def test_click_loads_videos_calibration_and_opens_measure(self):
        self.click_button()
        self.assert_measure_open()

    def test_click_replaces_the_previous_measurement_pair(self):
        self.controller.measure().refresh(str(self.previous), str(self.previous))
        self.click_button()
        self.assert_measure_open()

    def test_click_is_unavailable_without_a_saved_calibration(self):
        (self.cam / "T.npy").unlink()
        self.controller.calibration().savedCalibrationChanged.emit()
        QTest.qWait(20)
        self.assertFalse(self.button.property("actionable"))
        self.click_button()
        self.assertEqual(self.controller.currentPage, 3)

    def test_click_is_unavailable_while_calibration_runs(self):
        self.controller.calibration()._busy = True
        self.controller.calibration().busyChanged.emit()
        QTest.qWait(20)
        self.assertFalse(self.button.property("actionable"))
        self.click_button()
        self.assertEqual(self.controller.currentPage, 3)


if __name__ == "__main__":
    unittest.main()
