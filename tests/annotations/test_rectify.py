"""Rectification gauche : chargement du profil, transformation, provenance."""

from __future__ import annotations

import unittest

from annotations.helpers import (
    TempDirCase,
    set_camera_params_env,
    write_fake_calibration,
    write_test_image,
)

from src.annodb import rectify


class ProfileResolutionTest(TempDirCase):
    def test_profil_classique_a_la_racine(self):
        cam = self.tmp_path / "camera_parameters"
        write_fake_calibration(cam, profile="classic")
        set_camera_params_env(self, cam)

        self.assertEqual(rectify.active_profile_name(), "classic")
        calib = rectify.load_calibration_profile()
        self.assertIsNotNone(calib)
        self.assertEqual(calib.directory, cam)

    def test_profil_nomme_dans_profiles(self):
        cam = self.tmp_path / "camera_parameters"
        write_fake_calibration(cam, profile="fast_v2")
        set_camera_params_env(self, cam)

        self.assertEqual(rectify.active_profile_name(), "fast_v2")
        calib = rectify.load_calibration_profile()
        self.assertIsNotNone(calib)
        self.assertEqual(calib.directory, cam / "profiles" / "fast_v2")
        self.assertEqual(calib.name, "fast_v2")

    def test_sans_calibration_pas_de_rectifieur(self):
        cam = self.tmp_path / "camera_parameters"
        cam.mkdir(parents=True, exist_ok=True)
        set_camera_params_env(self, cam)

        self.assertIsNone(rectify.load_calibration_profile())
        self.assertIsNone(rectify.load_left_rectifier())
        meta = rectify.transform_metadata(None)
        self.assertEqual(meta["image_space"], rectify.IMAGE_SPACE_RAW)


class CalibrationHashTest(TempDirCase):
    def test_sha256_stable_et_sensible(self):
        import numpy as np

        cam = self.tmp_path / "camera_parameters"
        write_fake_calibration(cam, profile="classic")
        set_camera_params_env(self, cam)

        first = rectify.load_calibration_profile()
        second = rectify.load_calibration_profile()
        self.assertEqual(first.sha256, second.sha256)
        self.assertEqual(len(first.sha256), 64)
        self.assertEqual(set(first.file_sha256), set(rectify.CALIB_FILES))

        # Recalibrer doit changer le condensé : c'est ce qui permettra de
        # détecter qu'un export ne correspond plus aux boîtes stockées.
        np.save(cam / "T.npy", np.array([[-70.0], [0.0], [0.0]]))
        third = rectify.load_calibration_profile()
        self.assertNotEqual(first.sha256, third.sha256)


class LeftRectifierTest(TempDirCase):
    def setUp(self):
        super().setUp()
        self.cam = self.tmp_path / "camera_parameters"
        write_fake_calibration(self.cam, width=64, height=48, profile="classic")
        set_camera_params_env(self, self.cam)

    def test_transformation_applicable_et_metadonnees(self):
        img = write_test_image(self.tmp_path / "frame.png", 64, 48)
        rectifier = rectify.load_left_rectifier()
        self.assertIsNotNone(rectifier)

        out = rectifier(img)
        self.assertEqual(out.shape, img.shape)
        self.assertEqual(out.dtype, img.dtype)

        meta = rectifier.metadata()
        self.assertEqual(meta["image_space"], rectify.IMAGE_SPACE_RECTIFIED_LEFT)
        self.assertEqual(meta["profile_name"], "classic")
        self.assertEqual(len(meta["calibration_sha256"]), 64)
        self.assertEqual(meta["image_sizes"], [[64, 48]])

    def test_banc_aligne_sans_distorsion_est_quasi_neutre(self):
        """Deux caméras identiques et alignées : la rectification ne bouge presque rien."""
        import numpy as np

        img = write_test_image(self.tmp_path / "frame.png", 64, 48)
        out = rectify.load_left_rectifier()(img)
        # Bords exclus : le remap y interpole contre l'extérieur de l'image.
        inner = (slice(4, -4), slice(4, -4))
        diff = np.abs(out[inner].astype(np.int16) - img[inner].astype(np.int16))
        self.assertLess(float(diff.mean()), 1.0)

    def test_plusieurs_tailles_d_image(self):
        big = write_test_image(self.tmp_path / "big.png", 128, 96)
        small = write_test_image(self.tmp_path / "small.png", 64, 48)
        rectifier = rectify.load_left_rectifier()
        self.assertEqual(rectifier(big).shape, big.shape)
        self.assertEqual(rectifier(small).shape, small.shape)
        self.assertEqual(rectifier.image_sizes, [(64, 48), (128, 96)])


if __name__ == "__main__":
    unittest.main()
