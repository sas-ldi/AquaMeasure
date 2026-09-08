"""Lien annotation ↔ piste et propagation du taxon sur la piste.

`spatial_annotations.track_id` était NULL à 100 % : le worker de tracking écrit
par lots, donc la piste n'existait pas encore en base au moment de l'annotation
et `_resolve_track_for_annotation` renvoyait None en silence. La conséquence
mesurable : `tracks.taxon_node_id` vide, donc MaxN par espèce toujours vide.
"""

from __future__ import annotations

import sys
import unittest
import uuid

from tests.helpers import (
    FV_ROOT,
    TempDbCase,
    set_camera_params_env,
    write_sync_frames,
    write_test_image,
)

REPO_ROOT = FV_ROOT.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))


class MediaPathHealingTest(TempDbCase):
    """Un `rel_path` mort empêche de rouvrir une session ; le sha256 le répare."""

    def setUp(self):
        super().setUp()
        from src.annodb.connection import init_db

        init_db(self.db_path, seed=True)
        self.media = self.tmp_path / "clip_source.jpg"
        write_test_image(self.media, 64, 48)

    def test_chemin_mort_repare_quand_le_fichier_est_retrouve(self):
        import fish_annotate as fa

        from src.annodb.connection import session_scope
        from src.annodb.models import MediaAsset

        media_id = fa.resolve_media_id(str(self.media), create=True)
        with session_scope() as db:
            db.get(MediaAsset, media_id).rel_path = "D:/archive_debranchee/clip.jpg"

        again = fa.resolve_media_id(str(self.media), create=True)
        self.assertEqual(again, media_id)
        with session_scope() as db:
            self.assertEqual(db.get(MediaAsset, media_id).rel_path, str(self.media))

    def test_chemin_valide_jamais_ecrase(self):
        import fish_annotate as fa

        from src.annodb.connection import session_scope
        from src.annodb.models import MediaAsset

        media_id = fa.resolve_media_id(str(self.media), create=True)
        copy = self.tmp_path / "copie_du_meme_fichier.jpg"
        copy.write_bytes(self.media.read_bytes())

        # Même contenu, autre chemin : le chemin d'origine fonctionne encore,
        # on ne déplace pas les annotations sous les pieds de l'opérateur.
        again = fa.resolve_media_id(str(copy), create=True)
        self.assertEqual(again, media_id)
        with session_scope() as db:
            self.assertEqual(db.get(MediaAsset, media_id).rel_path, str(self.media))


class AnnotationTrackLinkTest(TempDbCase):
    def setUp(self):
        super().setUp()
        from src.annodb.connection import init_db

        init_db(self.db_path, seed=True)
        self.media = self.tmp_path / "clip_source.jpg"
        write_test_image(self.media, 320, 240)
        cam = self.tmp_path / "camera_parameters"
        write_sync_frames(cam, 2440, 2552)
        set_camera_params_env(self, cam)

    def _register_track(self, external_id: int = 7) -> str:
        """Simule ce que fait le flush du TrackingWorker avant l'annotation."""
        import fish_annotate as fa

        from src.annodb.connection import session_scope
        from src.annodb.tracks import get_or_create_track

        media_id = fa.resolve_media_id(str(self.media), create=True)
        self.assertIsNotNone(media_id)
        with session_scope() as db:
            track = get_or_create_track(
                db, media_id=media_id, external_track_id=external_id,
                source="bytetrack",
            )
            return track.id

    def test_observation_sur_bbox_trackee_porte_son_track_id(self):
        import fish_annotate as fa

        track_db_id = self._register_track(7)
        # La façade retrouve la piste par son numéro ByteTrack, comme le
        # contrôleur après avoir forcé le flush du worker.
        resolved = fa.resolve_track_db_id(str(self.media), 7)
        self.assertEqual(resolved, track_db_id)

        out = fa.add_observation(
            str(self.media), 3940,
            {"x1": 10.0, "y1": 20.0, "x2": 110.0, "y2": 140.0},
            source="model", track_id=resolved, frame_ref="absolute",
        )
        row = next(r for r in fa.list_observations() if r["ann_id"] == out["ann_id"])
        self.assertEqual(row["track_id"], track_db_id)

    def test_piste_inconnue_laisse_le_lien_vide_plutot_que_de_casser(self):
        import fish_annotate as fa

        out = fa.add_observation(
            str(self.media), 3940,
            {"x1": 10.0, "y1": 20.0, "x2": 110.0, "y2": 140.0},
            source="model", track_id="piste-inexistante", frame_ref="absolute",
        )
        row = next(r for r in fa.list_observations() if r["ann_id"] == out["ann_id"])
        self.assertIsNone(row["track_id"])

    def test_validation_du_taxon_propage_sur_la_piste(self):
        import fish_annotate as fa

        from src.annodb.connection import session_scope
        from src.annodb.models import Track

        track_db_id = self._register_track(7)
        out = fa.add_observation(
            str(self.media), 3940,
            {"x1": 10.0, "y1": 20.0, "x2": 110.0, "y2": 140.0},
            source="model", track_id=track_db_id, frame_ref="absolute",
        )
        with session_scope() as db:
            self.assertIsNone(db.get(Track, track_db_id).taxon_node_id)

        taxon_id = fa.ensure_species_known(
            "Scarus ghobban", genus_name="Scarus", family_name="Scaridae",
        )
        self.assertIsNotNone(taxon_id)
        fa.update_observation(out["ann_id"], taxon_node_id=taxon_id)

        with session_scope() as db:
            track = db.get(Track, track_db_id)
            self.assertEqual(track.taxon_node_id, taxon_id)
            self.assertEqual(track.identification_status, "identified")

    def test_facade_assign_track_taxon_recalcule_l_autorite(self):
        """La façade ne peut pas imposer un taxon absent des observations."""
        import fish_annotate as fa
        import fish_db_stats as fdb

        from src.annodb.connection import session_scope
        from src.annodb.models import Track

        track_db_id = self._register_track(9)
        taxon_id = fa.ensure_species_known("Scarus niger", genus_name="Scarus")
        self.assertTrue(fdb.assign_track_taxon(track_db_id, taxon_id))
        with session_scope() as db:
            track = db.get(Track, track_db_id)
            self.assertIsNone(track.taxon_node_id)
            self.assertIsNone(track.identification_status)
        self.assertFalse(fdb.assign_track_taxon(str(uuid.uuid4()), taxon_id))

    def test_delete_only_identification_clears_track_stats_and_export_authority(self):
        import fish_annotate as fa

        from src.annodb.connection import session_scope
        from src.annodb.export_core import build_class_plan_for_tracks
        from src.annodb.models import MediaAsset, Track, TrackSample
        from src.annodb.session_stats import compute_session_stats

        track_id = self._register_track(17)
        out = fa.add_observation(
            str(self.media), 3940,
            {"x1": 10.0, "y1": 20.0, "x2": 110.0, "y2": 140.0},
            source="manual", track_id=track_id, frame_ref="absolute",
        )
        taxon_id = fa.ensure_species_known(
            "Scarus rubroviolaceus", genus_name="Scarus", family_name="Scaridae",
        )
        fa.update_observation(out["ann_id"], taxon_node_id=taxon_id)
        with session_scope() as db:
            db.add(TrackSample(
                track_id=track_id, frame_index=3940, cx=0.2, cy=0.3,
                bbox_json='{"x_min":10,"y_min":20,"x_max":110,"y_max":140}',
            ))

        fa.delete_observation(out["ann_id"])
        with session_scope() as db:
            track = db.get(Track, track_id)
            self.assertIsNone(track.taxon_node_id)
            self.assertIsNone(track.identification_status)
            media = db.get(MediaAsset, track.media_id)
            stats = compute_session_stats(db, media)
            plan = build_class_plan_for_tracks(
                db, [track], "species", min_instances=1, min_media=1,
            )
        self.assertEqual(stats["species_count"], 0)
        self.assertEqual(stats["max_per_species"], [])
        self.assertIsNone(plan.class_of_annotation.get(track_id))

    def test_reclassification_with_conflicting_annotations_is_deterministic(self):
        import fish_annotate as fa

        from src.annodb.connection import session_scope
        from src.annodb.models import Track

        track_id = self._register_track(18)
        species_a = fa.ensure_species_known(
            "Scarus ghobban", genus_name="Scarus", family_name="Scaridae",
        )
        species_b = fa.ensure_species_known(
            "Scarus niger", genus_name="Scarus", family_name="Scaridae",
        )
        annotations = []
        for frame in (3940, 3941):
            annotations.append(fa.add_observation(
                str(self.media), frame,
                {"x1": 10.0, "y1": 20.0, "x2": 110.0, "y2": 140.0},
                source="manual", track_id=track_id, frame_ref="absolute",
            )["ann_id"])
        fa.update_observation(annotations[0], taxon_node_id=species_a)
        fa.update_observation(annotations[1], taxon_node_id=species_b)
        with session_scope() as db:
            track = db.get(Track, track_id)
            self.assertIsNone(track.taxon_node_id)
            self.assertEqual(track.identification_status, "ambiguous")

        fa.reclassify_annotation(annotations[0], species_b)
        with session_scope() as db:
            track = db.get(Track, track_id)
            self.assertEqual(track.taxon_node_id, species_b)
            self.assertEqual(track.identification_status, "identified")


if __name__ == "__main__":
    unittest.main()
