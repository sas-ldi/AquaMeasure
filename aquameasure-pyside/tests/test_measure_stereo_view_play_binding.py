"""Pendant la lecture, la vue stereo ne repousse plus de frame au lecteur.

Regression : `player.frame` etait lie a absFrame, lui-meme pilote par le
playhead du lecteur gauche. Chaque notification de position reimposait donc
une frame aux deux lecteurs, et SyncVideoPlayer.applySeek faisait un
pause/seek/play a chaque fois - la vue droite, qui decode a son propre
rythme, saccadait.
"""

from __future__ import annotations

import os
import sys
import unittest
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import QObject, QUrl
from PySide6.QtQml import QQmlApplicationEngine, QQmlComponent
from PySide6.QtQuickControls2 import QQuickStyle
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication

APP_ROOT = Path(__file__).resolve().parents[1]
if str(APP_ROOT) not in sys.path:
    sys.path.insert(0, str(APP_ROOT))

import main as app_main  # noqa: E402
from src.controllers.app_controller import AppController  # noqa: E402


WINDOW_QML = """
import QtQuick
import QtQuick.Controls
import AquaMeasure

ApplicationWindow {
    id: win
    width: 480; height: 320
    property int absFrame: 0
    property bool play: false

    MeasureStereoView {
        id: view
        anchors.fill: parent
        sideLabel: "Gauche"
        isLeft: true
        videoPath: ""
        videoFrameCount: 600
        frameWidth: 1920
        frameHeight: 1080
        fps: 30
        absFrame: win.absFrame
        placementEnabled: false
        interactionEnabled: false
        playing: win.play
    }
}
"""


class MeasureStereoViewPlayBindingTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])
        QQuickStyle.setStyle("Basic")
        cls.qml_root = app_main._prepare_qml_module(APP_ROOT)
        cls.ctrl = AppController()
        cls.engine = QQmlApplicationEngine()
        cls.engine.addImportPath(str(cls.qml_root))
        ctx = cls.engine.rootContext()
        ctx.setContextProperty("App", cls.ctrl)
        ctx.setContextProperty("Measure", cls.ctrl.measure())
        ctx.setContextProperty("Fish", cls.ctrl.fish())
        ctx.setContextProperty("Data", cls.ctrl.data())
        ctx.setContextProperty("Settings", cls.ctrl.settings())
        ctx.setContextProperty("AppSansFont", "Segoe UI")
        ctx.setContextProperty("AppMonoFont", "Consolas")

    def setUp(self):
        component = QQmlComponent(self.engine)
        component.setData(WINDOW_QML.encode("utf-8"), QUrl("inline:stereo.qml"))
        deadline = 100
        while component.isLoading() and deadline > 0:
            QTest.qWait(20)
            deadline -= 1
        window = component.create()
        self.assertIsNotNone(
            window,
            {
                "status": str(component.status()),
                "errors": [str(e) for e in component.errors()],
            },
        )
        window.show()
        QTest.qWait(60)
        self.component = component
        self.window = window
        self.player = window.findChild(QObject, "stereoVideoPlayer")
        self.assertIsNotNone(self.player, "lecteur introuvable dans la vue stereo")

    def tearDown(self):
        self.window.close()
        self.window.deleteLater()
        self.component.deleteLater()
        self.app.processEvents()

    def test_a_l_arret_la_frame_suit_absframe(self):
        self.window.setProperty("absFrame", 120)
        QTest.qWait(40)
        self.assertEqual(self.player.property("frame"), 120)

    def test_pendant_la_lecture_la_frame_n_est_plus_poussee(self):
        self.window.setProperty("absFrame", 100)
        QTest.qWait(40)
        self.window.setProperty("play", True)
        QTest.qWait(40)
        for frame in (101, 102, 105, 140):
            self.window.setProperty("absFrame", frame)
            QTest.qWait(10)
        self.assertEqual(
            self.player.property("frame"), 100,
            "la vue repousse encore une frame au lecteur pendant la lecture",
        )

    def test_le_retour_en_pause_realigne_le_lecteur(self):
        self.window.setProperty("absFrame", 100)
        QTest.qWait(40)
        self.window.setProperty("play", True)
        QTest.qWait(40)
        self.window.setProperty("absFrame", 240)
        QTest.qWait(20)
        self.window.setProperty("play", False)
        QTest.qWait(60)
        self.assertEqual(
            self.player.property("frame"), 240,
            "le lecteur n'est pas realigne sur la frame courante a la pause",
        )


if __name__ == "__main__":
    unittest.main()
