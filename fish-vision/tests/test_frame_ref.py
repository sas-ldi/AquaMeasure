"""Conversion d'index de frame : absolu vs timeline historique."""

from __future__ import annotations

import unittest

from tests.helpers import TempDirCase, set_camera_params_env, write_sync_frames

from src.annodb import frame_ref as fref


class NormalizeTest(unittest.TestCase):
    def test_absolu_reconnu(self):
        self.assertEqual(fref.normalize("absolute"), fref.FRAME_REF_ABSOLUTE)
        self.assertFalse(fref.is_legacy("absolute"))

    def test_valeur_absente_vaut_historique(self):
        # Supposer « absolu » sur une ligne non marquée reproduirait exactement
        # le décalage que la colonne existe pour empêcher.
        for value in (None, "", "   ", "inconnu"):
            self.assertEqual(fref.normalize(value), fref.FRAME_REF_TIMELINE_LEGACY)
            self.assertTrue(fref.is_legacy(value))


class TimelineOffsetTest(TempDirCase):
    def test_offset_depuis_sync_frames(self):
        cam = self.tmp_path / "camera_parameters"
        write_sync_frames(cam, 2440, 2552)
        set_camera_params_env(self, cam)
        # left_start = max(left_sync, -(right_sync - left_sync)) = 2440
        self.assertEqual(fref.timeline_offset(), 2440)

    def test_offset_quand_la_droite_demarre_avant(self):
        cam = self.tmp_path / "camera_parameters"
        write_sync_frames(cam, 100, 20)
        set_camera_params_env(self, cam)
        # delta = -80 -> left_start = max(100, 80) = 100
        self.assertEqual(fref.timeline_offset(), 100)

    def test_offset_nul_sans_decalage(self):
        cam = self.tmp_path / "camera_parameters"
        write_sync_frames(cam, 0, 50)
        set_camera_params_env(self, cam)
        self.assertEqual(fref.timeline_offset(), 0)

    def test_offset_indeterminable_sans_fichier(self):
        cam = self.tmp_path / "camera_parameters"
        cam.mkdir(parents=True, exist_ok=True)
        set_camera_params_env(self, cam)
        self.assertIsNone(fref.timeline_offset())


class ConversionTest(unittest.TestCase):
    def test_ligne_absolue_inchangee(self):
        self.assertEqual(fref.to_absolute(1500, "absolute", 2440), 1500)
        self.assertEqual(fref.to_absolute(1500, "absolute", None), 1500)

    def test_ligne_historique_decalee(self):
        self.assertEqual(fref.to_absolute(0, "timeline_legacy", 2440), 2440)
        self.assertEqual(fref.to_absolute(1500, "timeline_legacy", 2440), 3940)

    def test_ligne_historique_sans_offset_non_convertible(self):
        self.assertIsNone(fref.to_absolute(1500, "timeline_legacy", None))

    def test_aller_retour_timeline(self):
        offset = 2440
        for timeline in (0, 1, 999, 12345):
            absolute = fref.to_absolute(timeline, "timeline_legacy", offset)
            self.assertEqual(fref.to_timeline(absolute, offset), timeline)

    def test_description_explicite(self):
        self.assertIn("absolu", fref.describe("absolute", 2440))
        self.assertIn("converti", fref.describe("timeline_legacy", 2440))
        self.assertIn("indisponible", fref.describe("timeline_legacy", None))


if __name__ == "__main__":
    unittest.main()
