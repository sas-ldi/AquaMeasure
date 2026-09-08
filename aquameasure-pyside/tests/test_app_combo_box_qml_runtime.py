"""Interactions souris réelles du sélecteur QML partagé."""

from __future__ import annotations

import os
import sys
import unittest
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import QPoint, QUrl, Qt
from PySide6.QtQml import QQmlApplicationEngine, QQmlComponent
from PySide6.QtQuickControls2 import QQuickStyle
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication


APP_ROOT = Path(__file__).resolve().parents[1]
if str(APP_ROOT) not in sys.path:
    sys.path.insert(0, str(APP_ROOT))

import main as app_main  # noqa: E402


class AppComboBoxQmlRuntimeTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])
        QQuickStyle.setStyle("Basic")
        cls.qml_root = app_main._prepare_qml_module(APP_ROOT)
        cls.engine = QQmlApplicationEngine()
        cls.engine.addImportPath(str(cls.qml_root))
        cls._qml_objects = []

    @classmethod
    def tearDownClass(cls):
        for obj in reversed(cls._qml_objects):
            if hasattr(obj, "close"):
                obj.close()
            obj.deleteLater()
        cls._qml_objects.clear()
        cls.app.processEvents()
        del cls.engine

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

    def test_editable_combo_arrow_opens_popup_inside_dialog(self):
        window = self._create_window("""
            import QtQuick
            import QtQuick.Controls
            import AquaMeasure
            ApplicationWindow {
                id: win
                width: 640; height: 480
                property bool comboPopupVisible: combo.popup.visible
                property int comboCurrentIndex: combo.currentIndex
                property point arrowPoint: Qt.point(
                    dlg.x + dlg.leftPadding + combo.x + combo.width - 18,
                    dlg.y + dlg.topPadding + combo.y + combo.height / 2)
                property point firstItemPoint: Qt.point(
                    dlg.x + dlg.leftPadding + combo.x + combo.width / 2,
                    dlg.y + dlg.topPadding + combo.y + combo.height + 4
                        + combo.popup.padding + 19)

                Dialog {
                    id: dlg
                    anchors.centerIn: parent
                    width: 440; height: 220
                    modal: true
                    contentItem: Item {
                        AppComboBox {
                            id: combo
                            x: 20; y: 20
                            width: 360; height: 40
                            editable: true
                            model: ["Alice Martin", "Bob Dupont"]
                            currentIndex: -1
                        }
                    }
                }
                Component.onCompleted: dlg.open()
            }
        """)

        self.assertFalse(window.property("comboPopupVisible"))
        point = window.property("arrowPoint")
        QTest.mouseClick(
            window,
            Qt.LeftButton,
            Qt.NoModifier,
            QPoint(round(point.x()), round(point.y())),
        )
        QTest.qWait(80)
        self.assertTrue(window.property("comboPopupVisible"))

        point = window.property("firstItemPoint")
        QTest.mouseClick(
            window,
            Qt.LeftButton,
            Qt.NoModifier,
            QPoint(round(point.x()), round(point.y())),
        )
        QTest.qWait(80)
        self.assertFalse(window.property("comboPopupVisible"))
        self.assertEqual(window.property("comboCurrentIndex"), 0)

    def test_non_editable_combo_opens_from_field_surface(self):
        window = self._create_window("""
            import QtQuick
            import QtQuick.Controls
            import AquaMeasure
            ApplicationWindow {
                id: win
                width: 500; height: 300
                property bool comboPopupVisible: combo.popup.visible
                property point fieldPoint: combo.mapToItem(
                    win.contentItem, combo.width / 2, combo.height / 2)

                AppComboBox {
                    id: combo
                    x: 60; y: 60
                    width: 260; height: 40
                    model: ["Premier", "Second"]
                }
            }
        """)

        point = window.property("fieldPoint")
        QTest.mouseClick(
            window,
            Qt.LeftButton,
            Qt.NoModifier,
            QPoint(round(point.x()), round(point.y())),
        )
        QTest.qWait(80)
        self.assertTrue(window.property("comboPopupVisible"))


    def test_popup_elargi_pour_des_libelles_longs(self):
        """Le menu deroulant ne doit pas elider ce que le champ elide deja.

        Sa largeur etait bornee a celle du champ : dans le volet lateral,
        « AquaMeasure - famille (maison) » et « AquaMeasure - famille (ONNX,
        sans torch) » s'affichaient tous deux « AquaMeasure - famille (... ».
        """
        window = self._create_window("""
            import QtQuick
            import QtQuick.Controls
            import AquaMeasure
            ApplicationWindow {
                width: 900; height: 400
                property real fieldWidth: combo.width
                property real popupWidth: combo.popup.width
                property real popupX: combo.popup.x
                property real comboX: combo.x
                // Largeur reelle du plus long libelle, mesuree avec la meme
                // police : le menu doit pouvoir l'afficher en entier.
                property real longestLabelWidth: metrics.width
                TextMetrics {
                    id: metrics
                    font: combo.font
                    text: "AquaMeasure - famille (ONNX, sans torch)"
                }
                AppComboBox {
                    id: combo
                    objectName: "longLabelCombo"
                    x: 12; y: 12; width: 200
                    textRole: "label"
                    model: [
                        { label: "AquaMeasure - famille (maison)" },
                        { label: "AquaMeasure - famille (ONNX, sans torch)" },
                        { label: "AquaMeasure - public (repli)" }
                    ]
                    Component.onCompleted: Qt.callLater(function() { combo.popup.open() })
                }
            }
        """)
        QTest.qWait(150)
        field = window.property("fieldWidth")
        popup = window.property("popupWidth")
        longest = window.property("longestLabelWidth")
        self.assertGreater(
            popup, field,
            "le menu doit s'elargir au-dela du champ pour montrer les libelles",
        )
        self.assertGreaterEqual(
            popup, longest,
            "le menu doit tenir le plus long libelle sans l'elider "
            f"(menu {popup:.0f} px, texte {longest:.0f} px)",
        )
        self.assertLessEqual(
            popup, window.property("width"),
            "le menu ne doit jamais deborder de la fenetre",
        )

    def test_popup_recale_quand_il_deborderait_a_droite(self):
        window = self._create_window("""
            import QtQuick
            import QtQuick.Controls
            import AquaMeasure
            ApplicationWindow {
                width: 400; height: 300
                property real popupWidth: combo.popup.width
                property real popupX: combo.popup.x
                property real comboX: combo.x
                AppComboBox {
                    id: combo
                    objectName: "edgeCombo"
                    x: 240; y: 12; width: 140
                    textRole: "label"
                    model: [
                        { label: "Un libelle vraiment tres long qui deborderait" },
                        { label: "Un autre libelle tout aussi interminable ici" }
                    ]
                    Component.onCompleted: Qt.callLater(function() { combo.popup.open() })
                }
            }
        """)
        QTest.qWait(150)
        right = (window.property("comboX") + window.property("popupX")
                 + window.property("popupWidth"))
        self.assertLessEqual(
            right, window.property("width"),
            "le menu elargi doit etre recale vers la gauche, pas sortir de "
            "la fenetre",
        )


if __name__ == "__main__":
    unittest.main()
