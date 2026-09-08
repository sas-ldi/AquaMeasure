"""Table `calibrations` : dedoublonnage par sha256 et lien media/session."""

from __future__ import annotations

import unittest
import uuid

from annotations.helpers import TempDbCase, set_camera_params_env, write_fake_calibration

from src.annodb import calibrations, sessions as sessions_mod
from src.annodb.connection import init_db, session_scope
from src.annodb.models import Calibration, MediaAsset, Project


class CalibrationTableTest(TempDbCase):
    def setUp(self):
        super().setUp()
        init_db(self.db_path, seed=False)

    def test_meme_sha_ne_cree_pas_deux_lignes(self):
        with session_scope(self.db_path) as db:
            first = calibrations.register_calibration(
                db, profile_name="classic", sha256="c" * 64, alpha=0.0,
                image_width=1920, image_height=1080, baseline_mm=60.0,
            )
            second = calibrations.register_calibration(
                db, profile_name="classic", sha256="c" * 64,
            )
            self.assertEqual(first.id, second.id)
        with session_scope(self.db_path) as db:
            self.assertEqual(len(db.query(Calibration).all()), 1)

    def test_sha_different_cree_une_nouvelle_ligne(self):
        with session_scope(self.db_path) as db:
            calibrations.register_calibration(db, profile_name="classic", sha256="a" * 64)
            calibrations.register_calibration(db, profile_name="fast_v2", sha256="b" * 64)
        with session_scope(self.db_path) as db:
            self.assertEqual(len(db.query(Calibration).all()), 2)

    def test_ligne_existante_completee_mais_jamais_ecrasee(self):
        with session_scope(self.db_path) as db:
            calibrations.register_calibration(
                db, profile_name="classic", sha256="c" * 64, stereo_rmse=0.42,
            )
            row = calibrations.register_calibration(
                db, profile_name="classic", sha256="c" * 64,
                image_width=1920, image_height=1080, stereo_rmse=9.99,
            )
            self.assertEqual(row.image_width, 1920)
            # La RMSE deja connue n'est pas remplacee sous une session qui s'en sert.
            self.assertAlmostEqual(row.stereo_rmse, 0.42)

    def test_sans_sha_aucune_ligne(self):
        with session_scope(self.db_path) as db:
            self.assertIsNone(
                calibrations.register_calibration(db, profile_name="classic", sha256="")
            )

    def test_unicite_du_sha_garantie_par_le_schema(self):
        import sqlite3

        with session_scope(self.db_path) as db:
            calibrations.register_calibration(db, profile_name="classic", sha256="c" * 64)
        conn = sqlite3.connect(self.db_path)
        with self.assertRaises(sqlite3.IntegrityError):
            conn.execute(
                "INSERT INTO calibrations (id, profile_name, sha256, created_at) "
                "VALUES ('x', 'classic', ?, '2026-01-01')", ("c" * 64,),
            )
        conn.close()


class CalibrationFromProfileTest(TempDbCase):
    """Le profil actif sur disque doit alimenter la table sans intermediaire."""

    def setUp(self):
        super().setUp()
        init_db(self.db_path, seed=False)
        self.params = self.tmp_path / "camera_parameters"
        write_fake_calibration(self.params, width=64, height=48, baseline_mm=60.0)
        set_camera_params_env(self, self.params)

    def test_profil_actif_enregistre_avec_baseline(self):
        with session_scope(self.db_path) as db:
            row = calibrations.register_active_calibration(
                db, image_width=64, image_height=48,
            )
            self.assertIsNotNone(row)
            self.assertEqual(row.profile_name, "classic")
            self.assertEqual(len(row.sha256), 64)
            self.assertAlmostEqual(row.alpha, 0.0)
            self.assertAlmostEqual(row.baseline_mm, 60.0, places=6)

    def test_rmse_lue_dans_le_profil(self):
        import numpy as np

        np.save(self.params / "stereo_rmse.npy", np.array([0.37]))
        with session_scope(self.db_path) as db:
            row = calibrations.register_active_calibration(db)
            self.assertAlmostEqual(row.stereo_rmse, 0.37, places=5)


class SessionCalibrationLinkTest(TempDbCase):
    def setUp(self):
        super().setUp()
        init_db(self.db_path, seed=False)
        with session_scope(self.db_path) as db:
            proj = Project(id=str(uuid.uuid4()), name="test")
            db.add(proj)
            db.flush()
            self.left_id = str(uuid.uuid4())
            db.add(MediaAsset(
                id=self.left_id, project_id=proj.id, media_type="video",
                rel_path="gauche.mp4",
            ))
            self.session_id = sessions_mod.create_session(
                db, name="S", site="Recif", session_date="2026-08-20",
            ).id

    def test_attache_pose_la_calibration_sur_session_et_media(self):
        with session_scope(self.db_path) as db:
            calib = calibrations.register_calibration(
                db, profile_name="classic", sha256="d" * 64,
            )
            sessions_mod.attach_media_pair(
                db, self.session_id, left_media_id=self.left_id,
                calibration_profile="classic", calibration_sha256="d" * 64,
                calibration_id=calib.id,
            )
            calib_id = calib.id
        with session_scope(self.db_path) as db:
            from src.annodb.models import CaptureSession

            row = db.get(CaptureSession, self.session_id)
            self.assertEqual(row.calibration_id, calib_id)
            self.assertEqual(db.get(MediaAsset, self.left_id).calibration_id, calib_id)


if __name__ == "__main__":
    unittest.main()
