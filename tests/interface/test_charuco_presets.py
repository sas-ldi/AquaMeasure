"""Mires du projet : choix en un clic et memoire entre deux lancements.

La grande mire (84 cm) devait etre ressaisie a la main a chaque demarrage,
avec le risque d'inverser colonnes et lignes.
"""

from __future__ import annotations

import os
import sys
import tempfile
import unittest
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtWidgets import QApplication

APP_ROOT = Path(__file__).resolve().parents[2] / "src" / "interface"
if str(APP_ROOT) not in sys.path:
    sys.path.insert(0, str(APP_ROOT))

from src.controllers.settings_controller import SettingsController  # noqa: E402
from src.util import paths  # noqa: E402


class CharucoPresetsTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        cam = Path(self.tmp.name)
        self._orig = paths.camera_params_dir
        paths.camera_params_dir = staticmethod(lambda: cam)

    def tearDown(self):
        paths.camera_params_dir = self._orig
        self.tmp.cleanup()

    def test_petite_mire_par_defaut(self):
        self.assertEqual(SettingsController().charucoPresetId, "a3")

    def test_grande_mire_colonnes_dans_la_largeur(self):
        s = SettingsController()
        s.applyCharucoPreset("long")
        self.assertEqual((s.charucoSquaresX, s.charucoSquaresY), (20, 7))
        self.assertEqual((s.charucoSquareLengthMm, s.charucoMarkerLengthMm), (39.0, 29.0))
        self.assertEqual(s.charucoDictName, "DICT_5X5_100")

    def test_la_mire_est_retenue_au_redemarrage(self):
        SettingsController().applyCharucoPreset("long")
        self.assertEqual(SettingsController().charucoPresetId, "long")

    def test_saisie_manuelle_est_personnalisee(self):
        s = SettingsController()
        s.charucoSquaresX = 8
        self.assertEqual(s.charucoPresetId, "")
        self.assertEqual(SettingsController().charucoSquaresX, 8)

    def test_la_liste_suit_les_reglages_apres_un_choix(self):
        # Un vrai clic dans la liste ne doit pas figer currentIndex : la
        # liste suit encore les reglages changes ailleurs.
        from PySide6.QtCore import Property, QObject, QPointF, Qt, QUrl
        from PySide6.QtQml import QQmlApplicationEngine, QQmlComponent
        from PySide6.QtQuickControls2 import QQuickStyle
        from PySide6.QtTest import QTest

        import main as app_main

        class _Calib(QObject):
            @Property(bool, constant=True)
            def busy(self):
                return False

        QQuickStyle.setStyle("Basic")
        settings, calib = SettingsController(), _Calib()
        engine = QQmlApplicationEngine()
        engine.addImportPath(str(app_main._prepare_qml_module(APP_ROOT)))
        engine.rootContext().setContextProperty("Settings", settings)
        engine.rootContext().setContextProperty("Calib", calib)
        component = QQmlComponent(engine)
        component.setData(
            b"import QtQuick\nimport QtQuick.Controls\nimport AquaMeasure\n"
            b"ApplicationWindow { width: 640; height: 640\n"
            b"  CalibCharucoSettingsDialog { id: dlg } Component.onCompleted: dlg.open() }",
            QUrl("inline:charuco-dialog.qml"),
        )
        while component.isLoading():
            QTest.qWait(20)
        window = component.create()
        self.assertIsNotNone(window, [str(e) for e in component.errors()])
        window.show()
        QTest.qWait(300)
        box = next(
            item for item in window.findChildren(QObject)
            if item.objectName() == "charucoPresetBox"
        )
        long_index = [p["id"] for p in settings.charucoPresets].index("long")
        center = box.mapToScene(QPointF(box.width() / 2, box.height() / 2))
        QTest.mouseClick(window, Qt.LeftButton, Qt.NoModifier, center.toPoint())
        QTest.qWait(300)
        # Les elements de la liste ont la hauteur du champ, juste en dessous.
        item = box.mapToScene(QPointF(
            box.width() / 2, box.height() * (long_index + 1.5) + 4))
        QTest.mouseClick(window, Qt.LeftButton, Qt.NoModifier, item.toPoint())
        QTest.qWait(300)
        self.assertEqual(settings.charucoPresetId, "long")
        self.assertEqual(box.property("currentIndex"), long_index)
        settings.applyCharucoPreset("a3")
        self.assertEqual(box.property("currentIndex"), 0)
        window.close()
        window.deleteLater()
        QTest.qWait(20)


if __name__ == "__main__":
    unittest.main()
