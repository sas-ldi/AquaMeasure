"""Chemin d'écriture réel (fish_annotate) : index absolu + géométrie déclarée."""

from __future__ import annotations

import sys
import unittest

from annotations.helpers import (
    FV_ROOT,
    TempDbCase,
    set_camera_params_env,
    write_sync_frames,
    write_test_image,
)

# fish_annotate vit à la racine du dépôt, au-dessus de src/annotations/.
REPO_ROOT = FV_ROOT.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

WIDTH, HEIGHT = 1920, 1080


class AddObservationRoundTripTest(TempDbCase):
    def setUp(self):
        super().setUp()
        from src.annodb.connection import init_db

        init_db(self.db_path, seed=True)
        self.media = self.tmp_path / "frame_source.jpg"
        write_test_image(self.media, 320, 240)
        cam = self.tmp_path / "camera_parameters"
        write_sync_frames(cam, 2440, 2552)
        set_camera_params_env(self, cam)

    def _add(self, **kwargs):
        import fish_annotate as fa

        bbox = {"x1": 100.0, "y1": 200.0, "x2": 340.0, "y2": 460.0}
        return fa.add_observation(
            str(self.media), 3940, bbox, source="manual", **kwargs,
        )

    def test_ecriture_declare_espace_unites_et_index_absolu(self):
        import fish_annotate as fa

        out = self._add(
            image_space="stereo_rectified_left",
            ref_width=WIDTH,
            ref_height=HEIGHT,
            frame_ref="absolute",
        )
        rows = [
            r for r in fa.list_observations() if r["ann_id"] == out["ann_id"]
        ]
        self.assertEqual(len(rows), 1)
        row = rows[0]

        self.assertEqual(row["frame_index"], 3940)
        self.assertEqual(row["frame_ref"], "absolute")
        # Ligne absolue : aucune conversion ne doit s'y appliquer.
        self.assertEqual(row["frame_index_abs"], 3940)

        geom = row["geometry"]
        self.assertEqual(geom["units"], "px")
        self.assertEqual(geom["space"], "stereo_rectified_left")
        self.assertEqual(geom["ref_width"], WIDTH)
        self.assertEqual(geom["ref_height"], HEIGHT)
        self.assertEqual(
            (geom["x_min"], geom["y_min"], geom["x_max"], geom["y_max"]),
            (100.0, 200.0, 340.0, 460.0),
        )
        self.assertEqual(row["geometry_space"], "stereo_rectified_left")

    def test_espace_par_defaut_brut_et_dimensions_du_media(self):
        """Sans calibration active, l'écriture déclare 'raw' — jamais du rectifié."""
        import fish_annotate as fa

        out = self._add()
        row = next(r for r in fa.list_observations() if r["ann_id"] == out["ann_id"])
        geom = row["geometry"]
        self.assertEqual(geom["space"], "raw")
        self.assertEqual(geom["units"], "px")
        # Repli sur les dimensions enregistrées du média (image 320x240).
        self.assertEqual((geom["ref_width"], geom["ref_height"]), (320, 240))

    def test_relecture_yolo_de_la_geometrie_ecrite(self):
        import fish_annotate as fa
        from src.annodb.spatial import bbox_from_geometry

        out = self._add(
            image_space="stereo_rectified_left",
            ref_width=WIDTH, ref_height=HEIGHT, frame_ref="absolute",
        )
        row = next(r for r in fa.list_observations() if r["ann_id"] == out["ann_id"])
        cx, cy, bw, bh = bbox_from_geometry(row["geometry"], WIDTH, HEIGHT)
        self.assertAlmostEqual(cx, 220.0 / WIDTH, places=6)
        self.assertAlmostEqual(cy, 330.0 / HEIGHT, places=6)
        self.assertAlmostEqual(bw, 240.0 / WIDTH, places=6)
        self.assertAlmostEqual(bh, 260.0 / HEIGHT, places=6)


if __name__ == "__main__":
    unittest.main()
