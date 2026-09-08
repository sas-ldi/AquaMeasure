"""La valeur d'un AppSpinBox reste lisible, meme dans un volet etroit.

Regression : les deux boutons occupent 84 px a eux seuls. A 72 px (le reglage
<< Confiance min (%) >>) le champ de saisie tombait a une largeur negative et
le nombre disparaissait purement et simplement ; a 88 px (comptage par frame)
il n'en restait qu'un chiffre.
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


WINDOW_QML = """
import QtQuick
import QtQuick.Controls
import QtQuick.Layouts
import AquaMeasure

ApplicationWindow {
    id: win
    width: 400; height: 240
    property int askedWidth: 72

    ColumnLayout {
        anchors.fill: parent
        anchors.margins: 8
        AppSpinBox {
            objectName: "spin"
            from: 5
            to: 995
            value: 425
            Layout.preferredWidth: win.askedWidth
        }
        Item { Layout.fillHeight: true }
    }
}
"""


class AppSpinBoxReadabilityTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])
        QQuickStyle.setStyle("Basic")
        cls.qml_root = app_main._prepare_qml_module(APP_ROOT)
        cls.engine = QQmlApplicationEngine()
        cls.engine.addImportPath(str(cls.qml_root))

    def _spin(self, asked_width: int):
        component = QQmlComponent(self.engine)
        component.setData(WINDOW_QML.encode("utf-8"), QUrl("inline:spin.qml"))
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
        window.setProperty("askedWidth", asked_width)
        window.show()
        QTest.qWait(80)
        spin = window.findChild(QObject, "spin")
        self.assertIsNotNone(spin)
        self._keep = (component, window)
        return spin

    def tearDown(self):
        component, window = self._keep
        window.close()
        window.deleteLater()
        component.deleteLater()
        self.app.processEvents()

    def _value_width(self, spin) -> float:
        content = spin.property("contentItem")
        self.assertIsNotNone(content, "contentItem introuvable")
        return float(content.property("width"))

    def test_largeur_demandee_trop_petite_reste_lisible(self):
        # 72 px : la largeur historique du reglage « Confiance min (%) ».
        spin = self._spin(72)
        self.assertGreaterEqual(
            self._value_width(spin), 30,
            "le champ de valeur est ecrase : le nombre n'est pas lisible",
        )

    def test_largeur_confortable_conservee(self):
        spin = self._spin(148)
        self.assertGreaterEqual(self._value_width(spin), 30)
        self.assertAlmostEqual(float(spin.property("width")), 148, delta=1)

    def test_trois_chiffres_tiennent_dans_le_champ(self):
        spin = self._spin(88)
        content = spin.property("contentItem")
        text = str(content.property("text"))
        self.assertEqual(text, "425")
        # Le champ doit etre plus large que le texte qu'il affiche.
        self.assertGreaterEqual(
            self._value_width(spin), float(content.property("contentWidth")),
            "le nombre deborde du champ et se retrouve tronque",
        )


if __name__ == "__main__":
    unittest.main()
