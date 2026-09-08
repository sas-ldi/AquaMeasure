"""Smoke test du parcours volontairement court Fin de session / Fishial."""

from __future__ import annotations

import os
import sys
import tempfile
import unittest
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import QObject, QPointF, QUrl
from PySide6.QtGui import QFont, QFontDatabase
from PySide6.QtQml import QQmlApplicationEngine, QQmlComponent
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication


APP_ROOT = Path(__file__).resolve().parents[1]
if str(APP_ROOT) not in sys.path:
    sys.path.insert(0, str(APP_ROOT))

import main as app_main  # noqa: E402
from src.controllers.app_controller import AppController  # noqa: E402
from src.imaging.frame_image_provider import FrameImageProvider  # noqa: E402


class SimpleSessionExportQmlTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])
        if os.name == "nt":
            for filename in ("segoeui.ttf", "segoeuib.ttf", "consola.ttf"):
                QFontDatabase.addApplicationFont(str(Path(os.environ.get("WINDIR", "C:/Windows"))/"Fonts"/filename))
            cls.app.setFont(QFont("Segoe UI"))
        cls.tmp = tempfile.TemporaryDirectory(prefix="simple_export_qml_")
        root = Path(cls.tmp.name)
        cls.old_db = os.environ.get("FISH_VISION_DB")
        cls.old_storage = os.environ.get("AQUAMEASURE_STORAGE_CONFIG")
        cls.old_settings = os.environ.get("FISH_VISION_SETTINGS")
        os.environ["FISH_VISION_DB"] = str(root / "annotations.db")
        os.environ["AQUAMEASURE_STORAGE_CONFIG"] = str(root / "storage.json")
        os.environ["FISH_VISION_SETTINGS"] = str(root / "settings.json")

        cls.qml_root = app_main._prepare_qml_module(APP_ROOT)
        cls.controller = AppController()
        cls.images = FrameImageProvider()
        cls.controller.sync().set_image_provider(cls.images)
        cls.controller.calibration().set_image_provider(cls.images)
        cls.controller.measure().set_image_provider(cls.images)
        cls.engine = QQmlApplicationEngine()
        cls.engine.addImportPath(str(cls.qml_root))
        cls.engine.addImageProvider("frames", cls.images)
        context = cls.engine.rootContext()
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
            ("Storage", cls.controller.storage()),
            ("AppLogoUrl", QUrl()),
            ("AppSansFont", "Segoe UI"),
            ("AppMonoFont", "Consolas"),
        ):
            context.setContextProperty(name, value)

    @classmethod
    def tearDownClass(cls):
        del cls.engine
        del cls.controller
        del cls.images
        from src.annodb import connection

        if connection._engine is not None:
            connection._engine.dispose()
            connection._engine = None
            connection._SessionLocal = None
        connection._migrated_paths.clear()
        if cls.old_db is None:
            os.environ.pop("FISH_VISION_DB", None)
        else:
            os.environ["FISH_VISION_DB"] = cls.old_db
        if cls.old_storage is None:
            os.environ.pop("AQUAMEASURE_STORAGE_CONFIG", None)
        else:
            os.environ["AQUAMEASURE_STORAGE_CONFIG"] = cls.old_storage
        if cls.old_settings is None:
            os.environ.pop("FISH_VISION_SETTINGS", None)
        else:
            os.environ["FISH_VISION_SETTINGS"] = cls.old_settings
        cls.tmp.cleanup()

    def test_deux_onglets_session_et_fishial_seulement(self):
        component = QQmlComponent(self.engine)
        component.setData(
            b"""
                import QtQuick
                import QtQuick.Window
                import AquaMeasure
                Window {
                    width: 1200; height: 800
                    DataPage { anchors.fill: parent }
                }
            """,
            QUrl("inline:simple-session-export.qml"),
        )
        while component.isLoading():
            QTest.qWait(20)
        window = component.create()
        self.assertIsNotNone(
            window,
            [str(error) for error in component.errors()],
        )
        window.show()
        QTest.qWait(80)
        self.assertIsNotNone(window.findChild(QObject, "sessionExportButton"))
        self.assertIsNotNone(window.findChild(QObject, "sessionExportFormatSelector"))
        self.assertIsNotNone(window.findChild(QObject, "fishialLibraryList"))
        nav_qml = (
            APP_ROOT / "qml" / "components" / "DataSubTabBar.qml"
        ).read_text(encoding="utf-8")
        self.assertIn('qsTr("Session")', nav_qml)
        self.assertIn('qsTr("Fishial")', nav_qml)
        self.assertNotIn('qsTr("Explorateur")', nav_qml)
        self.assertNotIn('qsTr("Pistes")', nav_qml)
        self.assertNotIn('qsTr("Fin de session")', nav_qml)
        self.controller.data().subTab = 1
        self.assertEqual(self.controller.data().subTab, 1)
        window.close()
        window.deleteLater()
        component.deleteLater()

    def test_aucun_ancien_export_global_dans_la_page_simple(self):
        export_qml = (
            APP_ROOT / "qml" / "pages" / "data" / "DataExportTab.qml"
        ).read_text(encoding="utf-8")
        self.assertIn("Exporter en COCO", export_qml)
        self.assertIn("COCO - détection", export_qml)
        self.assertIn("COCO-VID - tracking", export_qml)
        self.assertIn("CSV - données de session", export_qml)
        self.assertNotIn("—", export_qml)
        self.assertIn("GridLayout", export_qml)
        self.assertIn("columns: width >= 420 ? 3 : 2", export_qml)
        self.assertNotIn("Inclure le suivi", export_qml)
        self.assertNotIn("exportDbCoco", export_qml)
        self.assertNotIn("exportSessionCrops", export_qml)
        self.assertNotIn("toute la base", export_qml.lower())

    def test_transfert_fishial_disponible_sur_poste_vide_et_panneau_etroit(self):
        for width in (420, 1200):
            component = QQmlComponent(self.engine)
            component.setData((
                'import QtQuick\nimport QtQuick.Window\nimport AquaMeasure\n'
                f'Window {{ width: {width}; height: 900; FishialLibraryTab {{ anchors.fill: parent }} }}'
            ).encode(), QUrl("inline:fishial-transfer.qml"))
            window = component.create()
            self.assertIsNotNone(window, [str(error) for error in component.errors()])
            window.show()
            QTest.qWait(80)
            for name in ("fishialLocalExportButton", "fishialLocalImportButton"):
                button = window.findChild(QObject, name)
                self.assertIsNotNone(button, name)
                origin = button.mapToItem(window.contentItem(), QPointF(0, 0))
                self.assertGreaterEqual(origin.x(), 0, name)
                self.assertLessEqual(origin.x() + button.property("width"), width + .5, name)
            self.assertTrue(window.findChild(QObject,"fishialLocalImportButton").property("requires"))
            window.close()
            window.deleteLater()
            component.deleteLater()

    def test_panneau_export_etroit_ne_coupe_plus_les_commandes(self):
        component = QQmlComponent(self.engine)
        component.setData(
            b"""
                import QtQuick
                import QtQuick.Window
                import AquaMeasure
                Window {
                    width: 396; height: 720
                    DataExportTab { anchors.fill: parent }
                }
            """,
            QUrl("inline:narrow-session-export.qml"),
        )
        while component.isLoading():
            QTest.qWait(20)
        window = component.create()
        self.assertIsNotNone(window, [str(error) for error in component.errors()])
        window.show()
        QTest.qWait(80)

        for object_name in (
            "sessionExportFormatSelector",
            "sessionExportButton",
            "openFishialButton",
            "advancedExportSettingsButton",
        ):
            item = window.findChild(QObject, object_name)
            self.assertIsNotNone(item, object_name)
            origin = item.mapToItem(window.contentItem(), QPointF(0, 0))
            self.assertGreaterEqual(origin.x(), 0, object_name)
            self.assertLessEqual(
                origin.x() + item.property("width"),
                window.property("width") + 0.5,
                object_name,
            )

        window.close()
        window.deleteLater()
        component.deleteLater()

    def test_export_reste_visible_quand_quarante_especes_defilent(self):
        sessions = self.controller.sessions()
        saved_selection = sessions._selected
        try:
            for width, height in ((350, 540), (396, 720), (500, 900)):
                component = QQmlComponent(self.engine)
                component.setData((
                    'import QtQuick\nimport QtQuick.Window\nimport AquaMeasure\n'
                    f'Window {{ width: {width}; height: {height}; DataExportTab {{ anchors.fill: parent }} }}'
                ).encode(), QUrl("inline:fixed-session-export.qml"))
                while component.isLoading():
                    QTest.qWait(20)
                window = component.create()
                self.assertIsNotNone(window, [str(error) for error in component.errors()])
                sessions._selected = {"session_id": "demo", "name": "Démo", "pair_count": 1,
                    "species_summary": [{"name": f"Espèce de démonstration {i}", "count": i+1} for i in range(40)]}
                sessions.selectedChanged.emit()
                window.show()
                QTest.qWait(80)
                button = window.findChild(QObject, "sessionExportButton")
                scroller = window.findChild(QObject, "dataExportScrollView")
                flick = scroller.property("contentItem")
                self.assertGreater(flick.property("contentHeight"), flick.property("height"))
                origin = button.mapToItem(window.contentItem(), QPointF(0, 0))
                self.assertGreaterEqual(origin.y(), 0)
                self.assertLessEqual(origin.y() + button.property("height"), height)
                flick.setProperty("contentY", flick.property("contentHeight") - flick.property("height"))
                QTest.qWait(30)
                self.assertEqual(button.mapToItem(window.contentItem(), QPointF(0, 0)), origin)
                window.findChild(QObject, "advancedExportSettingsButton").clicked.emit()
                QTest.qWait(30)
                self.assertEqual(button.mapToItem(window.contentItem(), QPointF(0, 0)), origin)
                self.assertEqual(flick.property("contentY"), 0)
                window.close()
                window.deleteLater()
                component.deleteLater()
        finally:
            sessions._selected = saved_selection
            sessions.selectedChanged.emit()

    def test_export_reussi_ouvre_une_confirmation_avec_le_dossier(self):
        component = QQmlComponent(self.engine)
        component.setData(
            b"""
                import QtQuick
                import QtQuick.Window
                import AquaMeasure
                Window {
                    width: 1200; height: 800
                    DataExportTab { anchors.fill: parent }
                }
            """,
            QUrl("inline:session-export-confirmation.qml"),
        )
        while component.isLoading():
            QTest.qWait(20)
        window = component.create()
        self.assertIsNotNone(window, [str(error) for error in component.errors()])
        window.show()
        QTest.qWait(40)

        dialog = window.findChild(QObject, "sessionExportSuccessDialog")
        button = window.findChild(QObject, "sessionExportOpenFolderButton")
        self.assertIsNotNone(dialog)
        self.assertIsNotNone(button)
        self.controller.proTools()._set_last_export(
            "success", 0, "C:/exports/session-test", kind="session",
        )
        QTest.qWait(40)
        self.assertTrue(dialog.property("visible"))
        self.assertEqual(button.property("text"), "Ouvrir le dossier")
        dialog.close()
        window.close()
        window.deleteLater()
        component.deleteLater()

    def test_nouvelle_session_vide_reellement_le_contexte_visible(self):
        data = self.controller.data()
        measure = self.controller.measure()
        sessions = self.controller.sessions()
        data._registry.set_rows([{
            "ann_id": "ancienne", "frame_index": 18,
            "media_id": "ancien-media",
        }])
        data.selectedTrackLabel = "42"
        data._session_max_fish = 9
        data._frame_ai_count = 7
        measure._left = "ancienne-gauche.mp4"
        measure._right = "ancienne-droite.mp4"
        measure._frame_count = 120

        sessions.formName = "Nouvelle journée"
        sessions.formSite = "Récif test"
        sessions.formDate = "2026-09-03"
        sessions.createSession()

        self.assertTrue(sessions.selectedId)
        self.assertEqual(data.sessionId, sessions.selectedId)
        self.assertEqual(data.registry.rowCount(), 0)
        self.assertEqual(data.selectedTrackLabel, "")
        self.assertEqual(data.sessionMaxVisibleFish, -1)
        self.assertEqual(data.frameAiCount, 0)
        self.assertEqual(measure.leftVideo, "")
        self.assertEqual(measure.rightVideo, "")
        self.assertEqual(measure.frameCount, 0)
        self.assertIn("Nouvelle journée", sessions.statusText)


if __name__ == "__main__":
    unittest.main()
