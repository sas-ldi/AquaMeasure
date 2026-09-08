"""Proposition de grille ChArUco : visible et applicable.

Quand la grille saisie ne donne pas assez de coins, la sonde balaie les
grilles 3-13 x 3-9 et en propose une. Le resultat n'etait qu'une ligne de
console : la calibration continuait avec la mauvaise grille.
"""

from __future__ import annotations

import os
import sys
import unittest
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtWidgets import QApplication

APP_ROOT = Path(__file__).resolve().parents[1]
if str(APP_ROOT) not in sys.path:
    sys.path.insert(0, str(APP_ROOT))

from src.controllers.calibration_controller import CalibrationController  # noqa: E402
from src.controllers.settings_controller import SettingsController  # noqa: E402


class CalibCharucoSuggestionTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self):
        self.settings = SettingsController()
        self.settings.charucoSquaresX = 8
        self.settings.charucoSquaresY = 11
        self.calib = CalibrationController(self.settings)

    def test_aucune_proposition_au_depart(self):
        self.assertFalse(self.calib.charucoSuggestionAvailable)
        self.assertEqual(self.calib.charucoSuggestionLabel, "")

    def test_une_grille_differente_est_publiee(self):
        self.calib._on_suggest_config(5, 7)
        self.assertTrue(self.calib.charucoSuggestionAvailable)
        self.assertEqual(self.calib.charucoSuggestionLabel, "5×7")
        self.assertIn("Mire detectee 5x7", self.calib.logs.fullText)

    def test_une_grille_identique_n_est_pas_publiee(self):
        self.calib._on_suggest_config(8, 11)
        self.assertFalse(self.calib.charucoSuggestionAvailable)

    def test_appliquer_change_les_reglages(self):
        self.calib._on_suggest_config(5, 7)
        self.calib.applyCharucoSuggestion()
        self.assertEqual(self.settings.charucoSquaresX, 5)
        self.assertEqual(self.settings.charucoSquaresY, 7)
        self.assertFalse(
            self.calib.charucoSuggestionAvailable,
            "la proposition doit disparaitre une fois appliquee",
        )

    def test_ignorer_conserve_les_reglages(self):
        self.calib._on_suggest_config(5, 7)
        self.calib.dismissCharucoSuggestion()
        self.assertFalse(self.calib.charucoSuggestionAvailable)
        self.assertEqual(self.settings.charucoSquaresX, 8)
        self.assertEqual(self.settings.charucoSquaresY, 11)

    def test_regler_la_grille_a_la_main_retire_la_proposition(self):
        self.calib._on_suggest_config(5, 7)
        self.settings.charucoSquaresX = 5
        self.settings.charucoSquaresY = 7
        self.assertFalse(self.calib.charucoSuggestionAvailable)

    def test_le_signal_est_emis(self):
        seen = []
        self.calib.charucoSuggestionChanged.connect(lambda: seen.append(1))
        self.calib._on_suggest_config(5, 7)
        self.assertTrue(seen, "charucoSuggestionChanged jamais emis")


if __name__ == "__main__":
    unittest.main()
