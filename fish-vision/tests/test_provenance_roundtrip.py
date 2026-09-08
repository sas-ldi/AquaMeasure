"""Chaine reelle `fish_annotate` : provenance, relecture, identite, cache sha256.

C'est le chemin qu'emprunte reellement un clic droit sur une bbox :
fish_controller -> data_controller -> `fish_annotate.add_observation` ->
`save_bbox_annotation`, puis la coche ✓ -> `fish_annotate.update_observation`.
"""

from __future__ import annotations

import os
import sys
import unittest

from tests.helpers import FV_ROOT, TempDbCase, set_camera_params_env, write_test_image

REPO_ROOT = FV_ROOT.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from src.annodb.app_settings import ENV_SETTINGS_PATH
from src.annodb.connection import init_db, session_scope
from src.annodb.models import SpatialAnnotation


class ProvenanceRoundTripTest(TempDbCase):
    def setUp(self):
        super().setUp()
        settings = self.tmp_path / "app_settings.json"
        previous = os.environ.get(ENV_SETTINGS_PATH)
        os.environ[ENV_SETTINGS_PATH] = str(settings)
        self.addCleanup(self._restore_env, ENV_SETTINGS_PATH, previous)

        init_db(self.db_path, seed=True)
        self.media = self.tmp_path / "frame_source.jpg"
        write_test_image(self.media, 320, 240)
        set_camera_params_env(self, None)

        import fish_annotate as fa

        fa.create_annotator("Pierrick", "0000-0002-1825-0097")

    def _add_model_box(self):
        import fish_annotate as fa

        return fa.add_observation(
            str(self.media), 1200,
            {"x1": 10.0, "y1": 20.0, "x2": 90.0, "y2": 120.0, "conf": 0.83},
            source="model",
            confidence=0.83,
            frame_ref="absolute",
            model_id="aquameasure-family",
            model_sha256="f" * 64,
            model_conf_threshold=0.3,
        )

    def _row(self, ann_id: str) -> SpatialAnnotation:
        with session_scope(self.db_path) as db:
            return db.get(SpatialAnnotation, ann_id)

    def test_detection_ecrit_provenance_auteur_et_statut(self):
        out = self._add_model_box()
        ann = self._row(out["ann_id"])
        self.assertEqual(ann.source, "model")
        self.assertEqual(ann.model_id, "aquameasure-family")
        self.assertEqual(ann.model_sha256, "f" * 64)
        self.assertAlmostEqual(ann.model_conf_threshold, 0.3)
        self.assertAlmostEqual(ann.confidence, 0.83)
        self.assertEqual(ann.identification_status, "unreviewed")
        # Plus jamais 'operator' en dur.
        self.assertEqual(ann.author, "Pierrick")
        self.assertIsNone(ann.reviewed_by)

    def test_validation_taxon_passe_a_identified_sans_perdre_le_modele(self):
        import fish_annotate as fa

        out = self._add_model_box()
        fa.update_observation(out["ann_id"], family_text="Acanthuridae")
        ann = self._row(out["ann_id"])
        self.assertEqual(ann.identification_status, "identified")
        self.assertEqual(ann.reviewed_by, "Pierrick")
        self.assertIsNotNone(ann.reviewed_at)
        self.assertEqual(ann.source, "model")
        # La provenance survit a la validation — regle d'or de la phase 1.
        self.assertEqual(ann.model_id, "aquameasure-family")
        self.assertEqual(ann.model_sha256, "f" * 64)
        self.assertAlmostEqual(ann.model_conf_threshold, 0.3)
        self.assertAlmostEqual(ann.confidence, 0.83)

    def test_validation_tout_na_passe_a_unidentifiable(self):
        """Chemin NA du registre : `taxon_node_id` = noeud « poisson generique »."""
        import fish_annotate as fa

        out = self._add_model_box()
        na_id = fa.unidentified_taxon_id()
        self.assertIsNotNone(na_id, "le noeud NA doit exister dans la taxonomie seed")
        fa.update_observation(out["ann_id"], taxon_node_id=na_id)
        ann = self._row(out["ann_id"])
        self.assertEqual(ann.identification_status, "unidentifiable")
        self.assertEqual(ann.reviewed_by, "Pierrick")
        self.assertIsNotNone(ann.reviewed_at)
        self.assertEqual(ann.source, "model")
        self.assertEqual(ann.model_id, "aquameasure-family")

    def test_mesure_seule_ne_pretend_pas_a_une_relecture(self):
        import fish_annotate as fa

        out = self._add_model_box()
        fa.update_observation(out["ann_id"], measurement_mm=123.4)
        ann = self._row(out["ann_id"])
        self.assertEqual(ann.identification_status, "unreviewed")
        self.assertIsNone(ann.reviewed_by)
        self.assertAlmostEqual(ann.measurement_mm, 123.4)

    def test_boite_manuelle_sans_provenance_de_modele(self):
        import fish_annotate as fa

        out = fa.add_observation(
            str(self.media), 1200,
            {"x1": 10.0, "y1": 20.0, "x2": 90.0, "y2": 120.0},
            source="manual", frame_ref="absolute",
            model_id="aquameasure-family", model_sha256="f" * 64,
        )
        ann = self._row(out["ann_id"])
        # Une boite tracee a la main n'a pas de modele : ne rien inventer.
        self.assertIsNone(ann.model_id)
        self.assertIsNone(ann.model_sha256)
        self.assertEqual(ann.identification_status, "unreviewed")

    def test_boite_manuelle_reste_manuelle_apres_validation_na(self):
        import fish_annotate as fa

        out = fa.add_observation(
            str(self.media), 1200,
            {"x1": 10.0, "y1": 20.0, "x2": 90.0, "y2": 120.0},
            source="manual", frame_ref="absolute",
        )
        fa.update_observation(
            out["ann_id"], taxon_node_id=fa.unidentified_taxon_id(),
        )
        ann = self._row(out["ann_id"])
        self.assertEqual(ann.source, "manual")
        self.assertEqual(ann.identification_status, "unidentifiable")

    def test_provenance_visible_dans_le_registre(self):
        import fish_annotate as fa

        out = self._add_model_box()
        fa.update_observation(out["ann_id"], family_text="Acanthuridae")
        row = next(r for r in fa.list_observations() if r["ann_id"] == out["ann_id"])
        self.assertEqual(row["source"], "model")
        self.assertEqual(row["identification_status"], "identified")
        self.assertEqual(row["reviewed_by"], "Pierrick")
        self.assertEqual(row["model_id"], "aquameasure-family")
        self.assertAlmostEqual(row["model_conf_threshold"], 0.3)


class SessionOperatorTest(TempDbCase):
    """`sessions.operator` vient des `annotators` — pas d'un champ libre.

    Decision superviseur (0bis) : une seule identite, reutilisee partout.
    """

    def setUp(self):
        super().setUp()
        settings = self.tmp_path / "app_settings.json"
        previous = os.environ.get(ENV_SETTINGS_PATH)
        os.environ[ENV_SETTINGS_PATH] = str(settings)
        self.addCleanup(self._restore_env, ENV_SETTINGS_PATH, previous)
        init_db(self.db_path, seed=False)

    def test_creation_de_session_signee_par_l_annotateur_courant(self):
        import fish_annotate as fa

        fa.create_annotator("Alice Terrain")
        row = fa.create_session(name="Sortie", site="Récif Nord", session_date="2026-08-20")
        self.assertEqual(row["operator"], "Alice Terrain")

    def test_operator_vide_sans_identite(self):
        import fish_annotate as fa

        row = fa.create_session(name="Sortie", site="Récif Nord", session_date="2026-08-20")
        self.assertEqual(row["operator"], "")

    def test_attache_renseigne_l_operator_reste_vide(self):
        """Une session creee avant l'identification recupere l'operateur a l'attache."""
        import fish_annotate as fa
        from src.annodb.models import CaptureSession

        created = fa.create_session(
            name="Sortie", site="Récif Nord", session_date="2026-08-20",
        )
        session_id = created["session_id"]
        self.assertEqual(created["operator"], "")

        fa.create_annotator("Bob Terrain")
        media = self.tmp_path / "clip.jpg"
        write_test_image(media, 64, 48)
        fa.attach_media_pair(session_id, str(media))

        with session_scope(self.db_path) as db:
            self.assertEqual(db.get(CaptureSession, session_id).operator, "Bob Terrain")


class MediaSha256CacheTest(TempDbCase):
    """L'empreinte d'un media ne doit etre calculee qu'une fois."""

    def setUp(self):
        super().setUp()
        init_db(self.db_path, seed=False)
        self.media = self.tmp_path / "clip.jpg"
        write_test_image(self.media, 64, 48)

    def test_signature_memorisee_et_reutilisee(self):
        from src.annodb import projects
        from src.annodb.models import MediaAsset

        with session_scope(self.db_path) as db:
            proj = projects.get_or_create_project(db, "test")
            media = projects.register_media(
                db, project_id=proj.id, file_path=self.media,
                media_type="image", copy_into_store=False,
            )
            sha, media_id = media.sha256, media.id
            self.assertTrue(sha)
            self.assertIsNotNone(media.sha256_size)
            self.assertIsNotNone(media.sha256_mtime)

        calls = {"n": 0}
        real = projects._sha256_file

        def counting(path):
            calls["n"] += 1
            return real(path)

        projects._sha256_file = counting
        self.addCleanup(setattr, projects, "_sha256_file", real)
        with session_scope(self.db_path) as db:
            again = projects.sha256_for_path(db, self.media)
        self.assertEqual(again, sha)
        self.assertEqual(calls["n"], 0, "l'empreinte en cache doit etre reutilisee")

        with session_scope(self.db_path) as db:
            self.assertEqual(db.get(MediaAsset, media_id).sha256, sha)

    def test_fichier_modifie_invalide_le_cache(self):
        from src.annodb import projects

        with session_scope(self.db_path) as db:
            proj = projects.get_or_create_project(db, "test")
            first = projects.register_media(
                db, project_id=proj.id, file_path=self.media,
                media_type="image", copy_into_store=False,
            ).sha256

        # Contenu different -> taille et mtime changent -> recalcul.
        write_test_image(self.media, 96, 72)
        with session_scope(self.db_path) as db:
            second = projects.sha256_for_path(db, self.media)
        self.assertNotEqual(first, second)


if __name__ == "__main__":
    unittest.main()
