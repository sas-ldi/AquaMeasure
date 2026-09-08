"""Aller-retour de geometry_json : unités, espace image, taille de référence."""

from __future__ import annotations

import json
import unittest

from src.annodb.spatial import (
    UNITS_NORMALIZED,
    UNITS_PX,
    bbox_from_geometry,
    bbox_pixels_from_geometry,
    geometry_is_declared,
    geometry_ref_size,
    geometry_space,
    geometry_units,
    make_bbox_geometry,
)

W, H = 1920, 1080


class DeclaredGeometryTest(unittest.TestCase):
    def test_ecriture_puis_relecture_conserve_les_pixels(self):
        geom = make_bbox_geometry(
            100, 200, 340, 460,
            space="stereo_rectified_left", ref_width=W, ref_height=H,
        )
        # Le format doit survivre au passage par JSON (c'est ainsi qu'il est
        # stocké dans la colonne geometry_json).
        geom = json.loads(json.dumps(geom))

        self.assertTrue(geometry_is_declared(geom))
        self.assertEqual(geometry_units(geom), UNITS_PX)
        self.assertEqual(geometry_space(geom), "stereo_rectified_left")
        self.assertEqual(geometry_ref_size(geom), (W, H))

        x1, y1, x2, y2 = bbox_pixels_from_geometry(geom, W, H)
        self.assertAlmostEqual(x1, 100, places=6)
        self.assertAlmostEqual(y1, 200, places=6)
        self.assertAlmostEqual(x2, 340, places=6)
        self.assertAlmostEqual(y2, 460, places=6)

    def test_conversion_yolo_normalisee(self):
        geom = make_bbox_geometry(
            0, 0, 192, 108, space="raw", ref_width=W, ref_height=H,
        )
        cx, cy, bw, bh = bbox_from_geometry(geom, W, H)
        self.assertAlmostEqual(cx, 0.05, places=6)
        self.assertAlmostEqual(cy, 0.05, places=6)
        self.assertAlmostEqual(bw, 0.1, places=6)
        self.assertAlmostEqual(bh, 0.1, places=6)

    def test_taille_de_reference_prime_sur_la_taille_exportee(self):
        """Image ré-échantillonnée : la boîte doit suivre, pas dériver."""
        geom = make_bbox_geometry(
            960, 540, 1152, 648, space="stereo_rectified_left",
            ref_width=W, ref_height=H,
        )
        # Export en demi-résolution : les pixels doivent être divisés par deux.
        x1, y1, x2, y2 = bbox_pixels_from_geometry(geom, W // 2, H // 2)
        self.assertAlmostEqual(x1, 480, places=6)
        self.assertAlmostEqual(y1, 270, places=6)
        self.assertAlmostEqual(x2, 576, places=6)
        self.assertAlmostEqual(y2, 324, places=6)

    def test_unites_normalisees_declarees(self):
        geom = make_bbox_geometry(
            0.25, 0.25, 0.75, 0.75, space="raw", units=UNITS_NORMALIZED,
        )
        cx, cy, bw, bh = bbox_from_geometry(geom, W, H)
        self.assertAlmostEqual(cx, 0.5, places=6)
        self.assertAlmostEqual(cy, 0.5, places=6)
        self.assertAlmostEqual(bw, 0.5, places=6)
        self.assertAlmostEqual(bh, 0.5, places=6)

    def test_espace_par_defaut_declare(self):
        geom = make_bbox_geometry(1, 2, 3, 4)
        self.assertEqual(geometry_space(geom), "raw")


class LegacyGeometryTest(unittest.TestCase):
    """Les lignes écrites avant la déclaration d'unités restent lisibles."""

    def test_pixels_sans_units_toujours_lus(self):
        geom = {"x_min": 960.0, "y_min": 540.0, "x_max": 1152.0, "y_max": 648.0}
        self.assertFalse(geometry_is_declared(geom))
        self.assertIsNone(geometry_space(geom))
        cx, cy, bw, bh = bbox_from_geometry(geom, W, H)
        self.assertAlmostEqual(cx, (960 + 1152) / 2 / W, places=6)
        self.assertAlmostEqual(bw, 192 / W, places=6)

    def test_normalise_sans_units_toujours_lu(self):
        geom = {"cx": 0.5, "cy": 0.5, "w": 0.2, "h": 0.1, "normalized": True}
        cx, cy, bw, bh = bbox_from_geometry(geom, W, H)
        self.assertEqual((cx, cy, bw, bh), (0.5, 0.5, 0.2, 0.1))

    def test_reniflage_de_magnitude_reserve_au_legacy(self):
        """Une boîte minuscule déclarée en px n'est plus prise pour du normalisé."""
        geom = make_bbox_geometry(
            0.0, 0.0, 1.0, 1.0, space="raw", ref_width=W, ref_height=H,
        )
        cx, cy, bw, bh = bbox_from_geometry(geom, W, H)
        self.assertAlmostEqual(bw, 1.0 / W, places=9)
        self.assertAlmostEqual(bh, 1.0 / H, places=9)

        legacy = {"x_min": 0.0, "y_min": 0.0, "x_max": 1.0, "y_max": 1.0}
        cx, cy, bw, bh = bbox_from_geometry(legacy, W, H)
        self.assertAlmostEqual(bw, 1.0, places=9)

    def test_geometrie_inconnue_leve(self):
        with self.assertRaises(ValueError):
            bbox_from_geometry({"foo": 1}, W, H)


if __name__ == "__main__":
    unittest.main()
