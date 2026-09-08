"""La barre de comptage dit ou atterrit une validation : le MaxN de session.

Le client demandait << comment on valide un max N sur une frame ? >>. Le lien
entre << Valider comptage >> et le MaxN n'apparaissait nulle part a l'ecran :
il n'existait que dans une infobulle et dans les stats de session.
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
import QtQuick.Layouts
import AquaMeasure

ApplicationWindow {
    width: 900; height: 120
    property alias bar: barItem
    ColumnLayout {
        anchors.fill: parent
        FrameAbundanceBar {
            id: barItem
            objectName: "abundanceBar"
            Layout.fillWidth: true
            Layout.preferredHeight: 52
        }
        Item { Layout.fillHeight: true }
    }
}
"""


class FrameAbundanceMaxNTest(unittest.TestCase):
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
        self.data = self.ctrl.data()
        component = QQmlComponent(self.engine)
        component.setData(WINDOW_QML.encode("utf-8"), QUrl("inline:abundance.qml"))
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
        self._keep = (component, window)
        self.bar = window.findChild(QObject, "abundanceBar")
        self.assertIsNotNone(self.bar)

    def tearDown(self):
        component, window = self._keep
        window.close()
        window.deleteLater()
        component.deleteLater()
        self.app.processEvents()

    def _state(self, manual: int, maxn: int, validated: bool):
        self.data._frame_manual_count = manual
        self.data.frameManualCountChanged.emit()
        self.data._session_max_fish = maxn
        self.data.sessionMaxVisibleFishChanged.emit()
        self.data._frame_count_validated = validated
        self.data.frameCountValidatedChanged.emit()
        QTest.qWait(40)

    def test_le_maxn_de_session_est_expose(self):
        self._state(manual=4, maxn=6, validated=True)
        self.assertEqual(self.data.sessionMaxVisibleFish, 6)

    def test_image_non_validee_ne_porte_pas_le_maxn(self):
        self._state(manual=7, maxn=7, validated=False)
        self.assertFalse(
            self.bar.property("isSessionMax"),
            "une image non validee ne compte pas dans le MaxN",
        )

    def test_image_validee_au_maxn_est_signalee(self):
        self._state(manual=7, maxn=7, validated=True)
        self.assertTrue(self.bar.property("isSessionMax"))

    def test_image_validee_sous_le_maxn_n_est_pas_signalee(self):
        self._state(manual=3, maxn=7, validated=True)
        self.assertFalse(self.bar.property("isSessionMax"))

    def test_sans_maxn_connu_rien_n_est_signale(self):
        self._state(manual=0, maxn=-1, validated=True)
        self.assertFalse(self.bar.property("isSessionMax"))

    def test_le_bouton_valider_est_present(self):
        button = self._keep[1].findChild(QObject, "validateFrameCountButton")
        self.assertIsNotNone(button, "bouton « Valider comptage » introuvable")


if __name__ == "__main__":
    unittest.main()
