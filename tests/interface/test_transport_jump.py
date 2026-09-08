"""Avance et recul rapides de la barre de transport, et leur reglage.

Ce que le test protege
----------------------
- le pas est reglable en images **ou** en secondes, et les deux quantites
  sont retenues separement : basculer d'unite ne doit pas effacer l'autre ;
- en secondes, le pas est converti avec la cadence reelle de la video, et
  jamais a zero image - sans quoi le bouton semblerait mort ;
- le reglage survit a la fermeture : il est relu au demarrage suivant ;
- la barre expose bien cinq boutons (recul rapide, image precedente,
  lecture, image suivante, avance rapide) et emet le bon delta signe.
"""

from __future__ import annotations

import os
import sys
import tempfile
import unittest
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import QUrl
from PySide6.QtQml import QQmlApplicationEngine, QQmlComponent
from PySide6.QtQuickControls2 import QQuickStyle
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication

APP_ROOT = Path(__file__).resolve().parents[2] / "src" / "interface"
if str(APP_ROOT) not in sys.path:
    sys.path.insert(0, str(APP_ROOT))

import main as app_main  # noqa: E402
from src.controllers.settings_controller import SettingsController  # noqa: E402
from src.util import paths  # noqa: E402


WINDOW_QML = """
import QtQuick
import QtQuick.Controls
import QtQuick.Layouts
import AquaMeasure

ApplicationWindow {
    id: win
    width: 900; height: 120

    property int lastDelta: 0
    property int stepCalls: 0

    ColumnLayout {
        anchors.fill: parent
        anchors.margins: 12

        VideoTransportBar {
            id: bar
            objectName: "transportBar"
            frameIndex: 500
            frameCount: 3000
            fps: 25
            onStepBy: function(d) {
                win.lastDelta = d
                win.stepCalls += 1
            }
        }
    }
}
"""


class TransportJumpTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])
        QQuickStyle.setStyle("Basic")
        cls.qml_root = app_main._prepare_qml_module(APP_ROOT)
        cls._orig_cam_dir = paths.camera_params_dir

    @classmethod
    def tearDownClass(cls):
        paths.camera_params_dir = cls._orig_cam_dir
        cls.app.processEvents()

    def setUp(self):
        # Aucun test ne doit ecrire dans les reglages reels de l'utilisateur.
        self.tmp = tempfile.TemporaryDirectory()
        cam = Path(self.tmp.name) / "camera_parameters"
        paths.camera_params_dir = staticmethod(lambda: cam)
        self.settings = SettingsController()

    def tearDown(self):
        self.tmp.cleanup()

    # -- reglage cote controleur ------------------------------------------

    def test_valeurs_par_defaut(self):
        self.assertEqual(self.settings.transportJumpUnit, "frames")
        self.assertEqual(self.settings.transportJumpFrames, 30)
        self.assertEqual(self.settings.transportJumpStep(25.0), 30)
        self.assertEqual(self.settings.transportJumpLabel, "30 img")

    def test_pas_en_secondes_suit_la_cadence(self):
        self.settings.setTransportJump("seconds", 10)
        self.assertEqual(self.settings.transportJumpStep(25.0), 250)
        self.assertEqual(self.settings.transportJumpStep(60.0), 600)
        self.assertEqual(self.settings.transportJumpLabel, "10 s")

    def test_cadence_absente_retombe_sur_30_images_par_seconde(self):
        self.settings.setTransportJump("seconds", 2)
        self.assertEqual(self.settings.transportJumpStep(0.0), 60)
        self.assertEqual(self.settings.transportJumpStep(-1.0), 60)

    def test_pas_jamais_nul(self):
        """Une duree minuscule doit deplacer d'au moins une image."""
        self.settings.setTransportJump("seconds", 0.1)
        self.assertGreaterEqual(self.settings.transportJumpStep(5.0), 1)

    def test_les_deux_quantites_sont_retenues_separement(self):
        self.settings.setTransportJump("frames", 15)
        self.settings.setTransportJump("seconds", 3)
        self.assertEqual(self.settings.transportJumpSeconds, 3.0)
        self.settings.transportJumpUnit = "frames"
        self.assertEqual(
            self.settings.transportJumpFrames,
            15,
            "revenir aux images doit retrouver la quantite reglee en images",
        )

    def test_valeurs_hors_bornes_ramenees_dans_le_domaine(self):
        self.settings.transportJumpFrames = 0
        self.assertEqual(self.settings.transportJumpFrames, 1)
        self.settings.transportJumpFrames = 999999
        self.assertEqual(self.settings.transportJumpFrames, 9999)
        self.settings.transportJumpSeconds = 0
        self.assertEqual(self.settings.transportJumpSeconds, 0.1)

    def test_le_reglage_est_relu_au_demarrage_suivant(self):
        self.settings.setTransportJump("seconds", 15)
        relu = SettingsController()
        self.assertEqual(relu.transportJumpUnit, "seconds")
        self.assertEqual(relu.transportJumpSeconds, 15.0)
        self.assertEqual(relu.transportJumpLabel, "15 s")

    # -- barre de transport cote QML ---------------------------------------

    def _build_window(self):
        engine = QQmlApplicationEngine()
        engine.addImportPath(str(self.qml_root))
        engine.rootContext().setContextProperty("Settings", self.settings)
        engine.rootContext().setContextProperty("Sync", _FakeSync())
        component = QQmlComponent(engine)
        component.setData(WINDOW_QML.encode("utf-8"), QUrl("inline:transport.qml"))
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
        return engine, component, window

    def test_la_barre_expose_cinq_boutons_de_transport(self):
        engine, component, window = self._build_window()
        try:
            bar = window.findChild(object, "transportBar")
            self.assertIsNotNone(bar)
            kinds = [
                child.metaObject().className()
                for child in bar.children()
                if child.metaObject().className().startswith(
                    ("TransportIconButton", "TransportJumpButton")
                )
            ]
            jumps = [k for k in kinds if k.startswith("TransportJumpButton")]
            steps = [k for k in kinds if k.startswith("TransportIconButton")]
            self.assertEqual(len(jumps), 2, f"boutons de saut attendus : 2, vus {kinds}")
            self.assertEqual(len(steps), 3, f"boutons pas a pas attendus : 3, vus {kinds}")
        finally:
            window.close()
            window.deleteLater()
            component.deleteLater()
            del engine
            self.app.processEvents()

    def test_le_saut_emet_un_delta_signe_en_images(self):
        self.settings.setTransportJump("frames", 30)
        engine, component, window = self._build_window()
        try:
            bar = window.findChild(object, "transportBar")
            jumps = [
                child for child in bar.children()
                if child.metaObject().className().startswith("TransportJumpButton")
            ]
            self.assertEqual(len(jumps), 2)
            # Ordre de la barre : recul a gauche, avance a droite.
            back, forward = sorted(jumps, key=lambda item: item.property("direction"))
            back.jump.emit(-30)
            QTest.qWait(30)
            self.assertEqual(window.property("lastDelta"), -30)
            forward.jump.emit(30)
            QTest.qWait(30)
            self.assertEqual(window.property("lastDelta"), 30)
            self.assertEqual(window.property("stepCalls"), 2)
        finally:
            window.close()
            window.deleteLater()
            component.deleteLater()
            del engine
            self.app.processEvents()


class _FakeSync:
    """`VideoTransportBar` demande un timecode a `Sync` pour son libelle."""

    def formatTimecode(self, frame, fps):  # noqa: N802 - contrat QML
        return "00:00:00"


if __name__ == "__main__":
    unittest.main()
