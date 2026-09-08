"""Keyboard and mouse regression tests for leaving a numeric editor."""

from __future__ import annotations

import os
import sys
import unittest
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import QObject, QPoint, QUrl, Qt
from PySide6.QtQml import QQmlApplicationEngine, QQmlComponent
from PySide6.QtQuickControls2 import QQuickStyle
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication

APP_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(APP_ROOT))
import main as app_main


class AppSpinBoxFocusTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])
        QQuickStyle.setStyle("Basic")
        cls.engine = QQmlApplicationEngine()
        cls.engine.addImportPath(str(app_main._prepare_qml_module(APP_ROOT)))

    def setUp(self):
        self.component = QQmlComponent(self.engine)
        self.component.setData(b"""
            import QtQuick
            import QtQuick.Controls
            import AquaMeasure
            ApplicationWindow {
                width: 420; height: 300
                property int savedValue: 30
                property int modifications: 0
                property int detectedValue: -1
                AppSpinBox {
                    objectName: "spin"
                    x: 20; y: 20; width: 260
                    from: 5; to: 95; value: 30
                    onValueModified: {
                        savedValue = value
                        modifications++
                    }
                }
                Button {
                    x: 20; y: 100; width: 260; height: 40
                    focusPolicy: Qt.NoFocus
                    text: "Detect"
                    onClicked: detectedValue = savedValue
                }
                AppTextField {
                    objectName: "otherEditor"
                    x: 20; y: 160; width: 260; height: 40
                }
            }
        """, QUrl("inline:spin-focus.qml"))
        for _ in range(100):
            if not self.component.isLoading():
                break
            QTest.qWait(20)
        self.window = self.component.create()
        self.assertIsNotNone(self.window, [str(e) for e in self.component.errors()])
        self.window.show()
        self.window.requestActivate()
        QTest.qWait(80)
        self.spin = self.window.findChild(QObject, "spin")
        self.editor = self.spin.property("contentItem")

    def tearDown(self):
        self.window.close()
        self.window.deleteLater()
        self.component.deleteLater()
        self.app.processEvents()

    def click(self, x, y):
        QTest.mouseClick(self.window, Qt.LeftButton, Qt.NoModifier, QPoint(x, y))
        self.app.processEvents()

    def type_value(self):
        self.click(150, 40)
        self.assertTrue(self.editor.property("activeFocus"))
        QTest.keyClick(self.window, Qt.Key_A, Qt.ControlModifier)
        QTest.keyClick(self.window, Qt.Key_6)
        QTest.keyClick(self.window, Qt.Key_5)

    def assert_committed_and_unfocused(self):
        self.assertEqual(self.spin.property("value"), 65)
        self.assertEqual(self.window.property("savedValue"), 65)
        self.assertEqual(self.window.property("modifications"), 1)
        self.assertFalse(self.editor.property("activeFocus"))
        self.assertFalse(self.spin.property("activeFocus"))

    def test_return_commits_and_releases_focus(self):
        self.type_value()
        QTest.keyClick(self.window, Qt.Key_Return)
        self.assert_committed_and_unfocused()

    def test_keypad_enter_commits_and_releases_focus(self):
        self.type_value()
        QTest.keyClick(self.window, Qt.Key_Enter)
        self.assert_committed_and_unfocused()

    def test_blank_click_commits_and_releases_focus(self):
        self.type_value()
        self.click(360, 250)
        self.assert_committed_and_unfocused()

    def test_button_uses_committed_value_on_first_click(self):
        self.type_value()
        self.click(150, 120)
        self.assert_committed_and_unfocused()
        self.assertEqual(self.window.property("detectedValue"), 65)

    def test_other_editor_keeps_focus_and_accepts_typing(self):
        self.type_value()
        self.click(150, 180)
        self.assert_committed_and_unfocused()
        other = self.window.findChild(QObject, "otherEditor")
        self.assertTrue(other.property("activeFocus"))
        QTest.keyClick(self.window, Qt.Key_A)
        self.assertEqual(other.property("text"), "a")

    def test_click_inside_editor_does_not_end_editing(self):
        self.type_value()
        self.click(140, 40)
        self.assertTrue(self.editor.property("activeFocus"))
        self.assertEqual(self.editor.property("text"), "65")

    def test_step_buttons_still_work(self):
        self.click(260, 40)
        self.assertEqual(self.window.property("savedValue"), 31)
        self.click(40, 40)
        self.assertEqual(self.window.property("savedValue"), 30)

    def test_empty_input_can_be_left_with_return(self):
        self.type_value()
        QTest.keyClick(self.window, Qt.Key_A, Qt.ControlModifier)
        QTest.keyClick(self.window, Qt.Key_Backspace)
        QTest.keyClick(self.window, Qt.Key_Return)
        self.assertFalse(self.editor.property("activeFocus"))
        self.assertGreaterEqual(self.spin.property("value"), 5)
        self.assertLessEqual(self.spin.property("value"), 95)
        self.assertEqual(self.editor.property("text"), str(self.spin.property("value")))

    def test_tab_still_moves_to_next_editor(self):
        self.type_value()
        QTest.keyClick(self.window, Qt.Key_Tab)
        self.assert_committed_and_unfocused()
        other = self.window.findChild(QObject, "otherEditor")
        self.assertTrue(other.property("activeFocus"))

    def test_right_click_outside_also_commits(self):
        self.type_value()
        QTest.mouseClick(self.window, Qt.RightButton, Qt.NoModifier, QPoint(360, 250))
        self.assert_committed_and_unfocused()


if __name__ == "__main__":
    unittest.main()
