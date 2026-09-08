"""Etat reel de la console QML quand le LogModel se remplit.

Regression : les bindings appelaient logModel.rowCount(), une methode donc non
notifiante. Le placeholder << Aucun message >>, le compteur et le bouton
<< Effacer >> restaient bloques sur l'etat initial (console vide).
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
from src.util.log_model import LogModel  # noqa: E402


WINDOW_QML = """
import QtQuick
import QtQuick.Controls
import QtQuick.Layouts
import AquaMeasure

ApplicationWindow {
    id: win
    width: 720; height: 320
    property var model: null
    property int logCount: panel.logCount

    ColumnLayout {
        anchors.fill: parent
        anchors.margins: 12
        ConsolePanel {
            id: panel
            logModel: win.model
        }
    }
}
"""


class ConsolePanelQmlRuntimeTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])
        QQuickStyle.setStyle("Basic")
        cls.qml_root = app_main._prepare_qml_module(APP_ROOT)
        cls.engine = QQmlApplicationEngine()
        cls.engine.addImportPath(str(cls.qml_root))

    @classmethod
    def tearDownClass(cls):
        cls.app.processEvents()
        del cls.engine

    def setUp(self):
        self.model = LogModel()
        component = QQmlComponent(self.engine)
        component.setData(WINDOW_QML.encode("utf-8"), QUrl("inline:console.qml"))
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
        window.setProperty("model", self.model)
        window.show()
        QTest.qWait(80)
        self.component = component
        self.window = window
        self.placeholder = window.findChild(QObject, "consoleEmptyPlaceholder")
        self.assertIsNotNone(self.placeholder, "placeholder introuvable dans la console")

    def tearDown(self):
        self.window.close()
        self.window.deleteLater()
        self.component.deleteLater()
        self.app.processEvents()

    def test_console_vide_affiche_le_placeholder(self):
        self.assertEqual(self.window.property("logCount"), 0)
        self.assertTrue(self.placeholder.property("visible"))

    def test_placeholder_disparait_des_la_premiere_ligne(self):
        self.model.append("Elagage pre-stereo : ignore")
        QTest.qWait(60)
        self.assertEqual(self.window.property("logCount"), 1)
        self.assertFalse(
            self.placeholder.property("visible"),
            "le placeholder << Aucun message >> reste affiche alors que la "
            "console contient une ligne",
        )

    def test_compteur_suit_les_lignes_ajoutees(self):
        self.model.append("ligne 1\nligne 2\nligne 3")
        QTest.qWait(60)
        self.assertEqual(self.window.property("logCount"), 3)

    def test_clear_restaure_le_placeholder(self):
        self.model.append("ligne 1\nligne 2")
        QTest.qWait(60)
        self.assertEqual(self.window.property("logCount"), 2)
        self.model.clear()
        QTest.qWait(60)
        self.assertEqual(self.window.property("logCount"), 0)
        self.assertTrue(self.placeholder.property("visible"))


if __name__ == "__main__":
    unittest.main()
