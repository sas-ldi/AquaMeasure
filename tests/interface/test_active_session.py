"""La session choisie dans l'onglet Sessions devient la session active.

selectSession() ne remplissait qu'un panneau de detail sur sa propre page :
choisir une session ne changeait rien ailleurs, et le volet Mesure continuait
d'afficher « Pas encore de session pour cette paire de videos ».
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

from src.controllers.app_controller import AppController  # noqa: E402
from src.util import paths  # noqa: E402

LEFT = "C:/videos/LEFT_Runcam6_0000.MP4"
RIGHT = "C:/videos/RIGHT_Runcam6_0014.MP4"


class ActiveSessionTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])
        # selectSession() mémorise la session active : jamais dans le vrai
        # dossier camera_parameters/.
        cls.tmp = tempfile.TemporaryDirectory(prefix="active_session_")
        cls._orig_dir = paths.camera_params_dir
        paths.camera_params_dir = staticmethod(lambda: Path(cls.tmp.name))

    @classmethod
    def tearDownClass(cls):
        paths.camera_params_dir = cls._orig_dir
        cls.tmp.cleanup()

    def setUp(self):
        self.ctrl = AppController()
        # SyncController programme un QTimer.singleShot(0) a la construction.
        # Sans le laisser partir tout de suite, il se declenche pendant un
        # autre test, apres destruction du controleur : « Signal source has
        # been deleted ».
        self.app.processEvents()
        self.sessions = self.ctrl.sessions()
        self.measure = self.ctrl.measure()

    def tearDown(self):
        self.app.processEvents()
        self.ctrl.deleteLater()
        self.app.processEvents()

    def _select(self, **row):
        """Simule le clic sur une ligne de la liste des sessions."""
        self.sessions._selected = dict(row)
        self.sessions._selected_row = 0
        self.sessions.selectedChanged.emit()
        self.sessions.activeSessionChanged.emit()

    def _load_pair(self, left=LEFT, right=RIGHT):
        self.measure._left = left
        self.measure._right = right
        self.measure.leftVideoChanged.emit()
        self.measure.rightVideoChanged.emit()

    def test_sans_selection_aucune_session_active(self):
        self.assertFalse(self.sessions.hasActiveSession)
        self.assertFalse(self.sessions.activePairLoaded)
        self.assertEqual(self.sessions.activeSessionName, "")

    def test_selectionner_active_la_session(self):
        self._select(session_id="abc", name="aquarium", has_pair=False)
        self.assertTrue(self.sessions.hasActiveSession)
        self.assertEqual(self.sessions.activeSessionName, "aquarium")

    def test_session_sans_paire_est_signalee(self):
        self._load_pair()
        self._select(session_id="abc", name="aquarium", has_pair=False)
        self.assertFalse(self.sessions.activeSessionHasPair)
        self.assertFalse(self.sessions.activePairLoaded)
        self.assertEqual(self.sessions.activeSessionHint, "sans paire de vidéos")

    def test_paire_chargee_differente_est_signalee(self):
        self._load_pair(left="C:/videos/AUTRE.MP4")
        self._select(
            session_id="abc", name="aquarium", has_pair=True,
            left_path=LEFT, right_path=RIGHT,
        )
        self.assertFalse(self.sessions.activePairLoaded)
        self.assertEqual(self.sessions.activeSessionHint, "autre paire chargée")

    def test_paire_de_la_session_est_reconnue(self):
        self._load_pair()
        self._select(
            session_id="abc", name="aquarium", has_pair=True,
            left_path=LEFT, right_path=RIGHT,
        )
        self.assertTrue(self.sessions.activePairLoaded)
        self.assertEqual(self.sessions.activeSessionHint, "")

    def test_changer_de_paire_met_a_jour_l_etat(self):
        self._load_pair()
        self._select(
            session_id="abc", name="aquarium", has_pair=True,
            left_path=LEFT, right_path=RIGHT,
        )
        self.assertTrue(self.sessions.activePairLoaded)
        seen = []
        self.sessions.activeSessionChanged.connect(lambda: seen.append(1))
        self.measure._left = "C:/videos/AUTRE.MP4"
        self.measure.leftVideoChanged.emit()
        self.assertTrue(seen, "changer de video doit reevaluer la session active")
        self.assertFalse(self.sessions.activePairLoaded)

    def test_selectionner_emet_le_signal(self):
        seen = []
        self.sessions.activeSessionChanged.connect(lambda: seen.append(1))
        self.sessions._sessions.set_rows([
            {"session_id": "abc", "name": "aquarium", "has_pair": False}
        ])
        self.sessions.selectSession(0)
        self.assertTrue(seen, "activeSessionChanged jamais emis")
        self.assertEqual(self.sessions.activeSessionName, "aquarium")


if __name__ == "__main__":
    unittest.main()
