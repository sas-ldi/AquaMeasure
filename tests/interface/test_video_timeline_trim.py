"""Poignees In/Out de la synchro : la vue revient apres le glissement.

Sinon une seule camera avait bouge, l'appli proposait de « valider le
nouveau decalage » et le valider plantait le flash sur la poignee.
"""

from __future__ import annotations

import os
import sys
import unittest
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import QMetaObject, QObject, Q_ARG, QUrl, Qt
from PySide6.QtQml import QQmlApplicationEngine, QQmlComponent
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication

APP_ROOT = Path(__file__).resolve().parents[2] / "src" / "interface"
if str(APP_ROOT) not in sys.path:
    sys.path.insert(0, str(APP_ROOT))

import main as app_main  # noqa: E402


class VideoTimelineTrimTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])
        cls.engine = QQmlApplicationEngine()
        cls.engine.addImportPath(str(app_main._prepare_qml_module(APP_ROOT)))

    def _timeline(self):
        component = QQmlComponent(self.engine)
        component.setData(
            b"import QtQuick\nimport AquaMeasure\n"
            b"VideoTimeline { objectName: 'tl'; width: 780; totalFrames: 600;"
            b" outFrame: 599; currentFrame: 42; property var seeks: [];"
            b" onSeekRequested: (f) => { seeks.push(f); currentFrame = f } }",
            QUrl("inline:timeline.qml"),
        )
        while component.isLoading():
            QTest.qWait(10)
        item = component.create()
        self.assertIsNotNone(item, [str(e) for e in component.errors()])
        # Garder le composant et l'objet en vie tant que le test tourne.
        self._keep = (component, item)
        return item

    def test_la_vue_revient_apres_une_poignee(self):
        tl = self._timeline()
        QMetaObject.invokeMethod(tl, "_startTrim", Qt.DirectConnection,
                                 Q_ARG("QVariant", 1), Q_ARG("QVariant", 300))
        self.assertEqual(tl.property("currentFrame"), 300)
        QMetaObject.invokeMethod(tl, "_endTrim", Qt.DirectConnection)
        self.assertEqual(tl.property("currentFrame"), 42)
        self.assertEqual(tl.property("_drag"), 0)

    def test_les_poignees_passent_au_dessus_du_flash(self):
        tl = self._timeline()
        z = {}
        for item in tl.findChildren(QObject):
            cls = item.metaObject().className()
            for name in ("TrimHandle", "FlashPinHandle"):
                if name in cls:
                    z.setdefault(name, set()).add(item.property("z"))
        self.assertGreater(min(z["TrimHandle"]), max(z["FlashPinHandle"]))


if __name__ == "__main__":
    unittest.main()
