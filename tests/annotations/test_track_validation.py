"""Validation de pistes : lecture des drapeaux, fusion, scission, suppression.

Les trois gestes de correction (`split_track`, `merge_tracks`, `delete_track`)
touchent à la fois `tracks` et `track_samples` : ce qui est vérifié ici, c'est
que les **bornes** (`first_frame` / `last_frame`) suivent l'édition. Une piste
dont les bornes mentent fausse ensuite la couverture déclarée par l'export de
suivi, et donc la lecture qu'on fait du `gt.txt`.

Le bornage des boîtes à l'écriture est testé ici aussi : c'est le correctif
amont issu de l'enquête « 146 bbox hors cadre ».
"""

from __future__ import annotations

import json
import unittest
import uuid
from datetime import datetime

from annotations.helpers import TempDbCase

from src.annodb import tracks as tracks_mod
from src.annodb.connection import init_db, session_scope
from src.annodb.export_core import CLAMP_EPSILON_PX, clamp_box_to_image
from src.annodb.ingest_cvat import normalized_bbox_in_frame
from src.annodb.models import (
    MediaAsset,
    Project,
    SpatialAnnotation,
    TaxonNode,
    TemporalEvent,
    Track,
    TrackSample,
)

WIDTH, HEIGHT = 64, 48


def _bbox(x1: float, y1: float, x2: float, y2: float) -> str:
    return json.dumps({"x_min": x1, "y_min": y1, "x_max": x2, "y_max": y2})


class TrackFixture(TempDbCase):
    """Un média, quatre pistes couvrant les cas que l'opérateur doit trier."""

    def setUp(self):
        super().setUp()
        init_db(self.db_path, seed=False)
        with session_scope(self.db_path) as session:
            project = Project(id=str(uuid.uuid4()), name="pistes")
            session.add(project)
            session.flush()
            session.add(TaxonNode(
                id="sp-a", rank="species", scientific_name="Acanthurus nigrofuscus",
            ))
            session.add(MediaAsset(
                id="vid", project_id=project.id, media_type="video",
                rel_path="videos/a.mp4", width=WIDTH, height=HEIGHT,
                fps=30.0, frame_count=6000,
            ))
            session.flush()
            session.add_all([
                # Continue, sans trou : rien à signaler.
                Track(id="trk-propre", media_id="vid", external_track_id=1,
                      source="bytetrack", taxon_node_id="sp-a",
                      first_frame=0, last_frame=9),
                # Une seule frame : faux positif probable.
                Track(id="trk-unique", media_id="vid", external_track_id=2,
                      source="bytetrack", first_frame=100, last_frame=100),
                # Trou de 200 frames au milieu.
                Track(id="trk-trou", media_id="vid", external_track_id=3,
                      source="bytetrack", first_frame=200, last_frame=404),
                # Très longue : fusion d'identités probable.
                Track(id="trk-longue", media_id="vid", external_track_id=4,
                      source="bytetrack", first_frame=1000, last_frame=4000),
            ])
            session.flush()
            for frame in range(10):
                session.add(TrackSample(
                    track_id="trk-propre", frame_index=frame, cx=0.5, cy=0.5,
                    bbox_json=_bbox(10, 10, 20, 20), origin="auto",
                ))
            session.add(TrackSample(
                track_id="trk-unique", frame_index=100, cx=0.5, cy=0.5,
                bbox_json=_bbox(1, 1, 5, 5), origin="auto",
            ))
            for frame in (200, 202, 204, 404):
                session.add(TrackSample(
                    track_id="trk-trou", frame_index=frame, cx=0.5, cy=0.5,
                    bbox_json=_bbox(10, 10, 20, 20), origin="auto",
                ))
            for frame in range(1000, 4001, 100):
                session.add(TrackSample(
                    track_id="trk-longue", frame_index=frame, cx=0.5, cy=0.5,
                    bbox_json=_bbox(30, 20, 40, 30), origin="auto",
                ))

    def overview(self, **kwargs) -> dict:
        with session_scope(self.db_path) as session:
            rows = tracks_mod.track_overview(session, "vid", **kwargs)
        return {row["track_id"]: row for row in rows}

    def bounds(self, track_id: str):
        with session_scope(self.db_path) as session:
            track = session.get(Track, track_id)
            if track is None:
                return None
            return int(track.first_frame), int(track.last_frame)


class TrackOverviewTest(TrackFixture):
    def test_drapeaux_designent_les_pistes_douteuses(self):
        rows = self.overview()
        self.assertTrue(rows["trk-unique"]["single_frame"])
        self.assertFalse(rows["trk-propre"]["single_frame"])
        self.assertTrue(rows["trk-longue"]["very_long"])
        self.assertFalse(rows["trk-propre"]["very_long"])
        self.assertTrue(rows["trk-trou"]["has_gaps"])
        self.assertFalse(rows["trk-propre"]["has_gaps"])

    def test_echantillonnage_regulier_n_est_pas_un_trou(self):
        """Une frame sur deux est le rythme normal du suivi, pas une perte.

        `trk-longue` n'a qu'un échantillon toutes les 100 frames, mais
        **régulièrement** : c'est un pas d'échantillonnage, pas une piste
        interrompue. Compter cela comme un trou noierait les vraies pertes.
        """
        rows = self.overview()
        self.assertEqual(rows["trk-longue"]["gap_count"], 0)
        self.assertFalse(rows["trk-longue"]["has_gaps"])
        # `trk-trou` : pas médian de 2 frames, puis un saut de 200.
        self.assertEqual(rows["trk-trou"]["gap_count"], 1)
        self.assertEqual(rows["trk-trou"]["max_gap"], 198)

    def test_couverture_et_span_mesures_sur_les_echantillons(self):
        rows = self.overview()
        self.assertEqual(rows["trk-propre"]["span"], 10)
        self.assertEqual(rows["trk-propre"]["sample_count"], 10)
        self.assertEqual(rows["trk-propre"]["coverage"], 1.0)
        self.assertEqual(rows["trk-trou"]["span"], 205)
        self.assertEqual(rows["trk-trou"]["sample_count"], 4)
        self.assertLess(rows["trk-trou"]["coverage"], 0.05)

    def test_tri_par_suspicion_decroissante(self):
        with session_scope(self.db_path) as session:
            rows = tracks_mod.track_overview(session, "vid")
        scores = [row["suspicion"] for row in rows]
        self.assertEqual(scores, sorted(scores, reverse=True))
        # La piste propre ferme la marche, sans aucun drapeau.
        self.assertEqual(rows[-1]["track_id"], "trk-propre")
        self.assertEqual(rows[-1]["suspicion"], 0.0)

    def test_seuils_parametrables(self):
        serre = self.overview(long_track_frames=100)
        self.assertTrue(serre["trk-trou"]["very_long"])
        large = self.overview(gap_threshold=500)
        self.assertFalse(large["trk-trou"]["has_gaps"])

    def test_taxon_propage_est_affiche(self):
        rows = self.overview()
        self.assertEqual(rows["trk-propre"]["taxon"], "Acanthurus nigrofuscus")
        self.assertIsNone(rows["trk-unique"]["taxon"])

    def test_media_sans_piste_rend_une_liste_vide(self):
        with session_scope(self.db_path) as session:
            self.assertEqual(tracks_mod.track_overview(session, "inconnu"), [])


class SplitTrackTest(TrackFixture):
    def test_scission_recale_les_bornes_des_deux_pistes(self):
        with session_scope(self.db_path) as session:
            created = tracks_mod.split_track(session, "trk-propre", 5)
            self.assertIsNotNone(created)
            new_id = created.id
            tracks_mod.refresh_track_bounds(session, "trk-propre")
            tracks_mod.refresh_track_bounds(session, new_id)

        self.assertEqual(self.bounds("trk-propre"), (0, 4))
        self.assertEqual(self.bounds(new_id), (5, 9))
        rows = self.overview()
        self.assertEqual(rows["trk-propre"]["sample_count"], 5)
        self.assertEqual(rows[new_id]["sample_count"], 5)
        # La nouvelle piste hérite du taxon : on ne perd pas l'identification.
        self.assertEqual(rows[new_id]["taxon"], "Acanthurus nigrofuscus")

    def test_scission_sans_position_a_partir_de_la_frame_ne_fait_rien(self):
        with session_scope(self.db_path) as session:
            self.assertIsNone(tracks_mod.split_track(session, "trk-propre", 999))
        self.assertEqual(self.bounds("trk-propre"), (0, 9))

    def test_scission_donne_un_numero_externe_libre(self):
        with session_scope(self.db_path) as session:
            created = tracks_mod.split_track(session, "trk-longue", 2000)
            self.assertGreater(created.external_track_id, 4)


class MergeTracksTest(TrackFixture):
    def test_fusion_recolle_et_recale_les_bornes(self):
        with session_scope(self.db_path) as session:
            moved = tracks_mod.merge_tracks(session, "trk-unique", "trk-propre")
            self.assertEqual(moved, 1)
        # La cible couvre désormais 0 → 100.
        self.assertEqual(self.bounds("trk-propre"), (0, 100))
        self.assertIsNone(self.bounds("trk-unique"))
        rows = self.overview()
        self.assertNotIn("trk-unique", rows)
        self.assertEqual(rows["trk-propre"]["sample_count"], 11)

    def test_collision_de_frame_garde_l_echantillon_de_la_cible(self):
        with session_scope(self.db_path) as session:
            session.add(Track(
                id="trk-double", media_id="vid", external_track_id=9,
                source="bytetrack", first_frame=0, last_frame=0,
            ))
            session.flush()
            session.add(TrackSample(
                track_id="trk-double", frame_index=0, cx=0.9, cy=0.9,
                bbox_json=_bbox(50, 40, 60, 45), origin="auto",
            ))
        with session_scope(self.db_path) as session:
            tracks_mod.merge_tracks(session, "trk-double", "trk-propre")
        with session_scope(self.db_path) as session:
            samples = tracks_mod.list_track_samples(session, "trk-propre")
            frame0 = next(s for s in samples if s.frame_index == 0)
            self.assertEqual(frame0.cx, 0.5, "l'échantillon de la cible gagne")
            self.assertEqual(len(samples), 10)

    def test_fusion_sur_soi_meme_ne_fait_rien(self):
        with session_scope(self.db_path) as session:
            self.assertEqual(
                tracks_mod.merge_tracks(session, "trk-propre", "trk-propre"), 0,
            )
        self.assertEqual(self.bounds("trk-propre"), (0, 9))


class DeleteTrackTest(TrackFixture):
    def test_suppression_retire_piste_et_echantillons(self):
        with session_scope(self.db_path) as session:
            outcome = tracks_mod.delete_track(session, "trk-unique")
        self.assertTrue(outcome["deleted"])
        self.assertEqual(outcome["samples"], 1)
        self.assertIsNone(self.bounds("trk-unique"))
        with session_scope(self.db_path) as session:
            restants = session.query(TrackSample).filter_by(
                track_id="trk-unique"
            ).count()
            self.assertEqual(restants, 0)

    def test_observation_humaine_est_detachee_jamais_supprimee(self):
        """Une identification à la main vaut indépendamment de la trajectoire."""
        with session_scope(self.db_path) as session:
            session.add(SpatialAnnotation(
                id="ann-1", media_id="vid", frame_index=3, frame_ref="absolute",
                geom_type="bbox",
                geometry_json=json.dumps({
                    "x_min": 10, "y_min": 10, "x_max": 20, "y_max": 20,
                    "units": "px", "space": "stereo_rectified_left",
                }),
                taxon_node_id="sp-a", track_id="trk-propre", source="validated",
            ))
        with session_scope(self.db_path) as session:
            outcome = tracks_mod.delete_track(session, "trk-propre")
        self.assertTrue(outcome["deleted"])
        self.assertEqual(outcome["annotations_detached"], 1)
        with session_scope(self.db_path) as session:
            ann = session.get(SpatialAnnotation, "ann-1")
            self.assertIsNotNone(ann, "l'observation ne doit pas disparaître")
            self.assertIsNone(ann.track_id)
            self.assertEqual(ann.taxon_node_id, "sp-a")

    def test_evenement_manuel_bloque_la_suppression_sans_confirmation(self):
        with session_scope(self.db_path) as session:
            session.add(TemporalEvent(
                id="ev-1", track_id="trk-propre", event_type="grazing",
                frame_start=0, frame_end=5, frame_ref="absolute",
                source="manual", author="Camille",
                created_at=datetime(2026, 8, 1, 10, 0, 0),
            ))
        with session_scope(self.db_path) as session:
            refus = tracks_mod.delete_track(session, "trk-propre")
        self.assertFalse(refus["deleted"])
        self.assertIn("annoté", refus["reason"])
        self.assertEqual(refus["manual_events"], 1)
        self.assertEqual(self.bounds("trk-propre"), (0, 9))

        with session_scope(self.db_path) as session:
            force = tracks_mod.delete_track(session, "trk-propre", force=True)
        self.assertTrue(force["deleted"])
        self.assertEqual(force["events"], 1)
        self.assertIsNone(self.bounds("trk-propre"))

    def test_evenement_heuristique_part_avec_la_piste(self):
        """Une présomption automatique n'a aucun sens sans sa trajectoire."""
        with session_scope(self.db_path) as session:
            session.add(TemporalEvent(
                id="ev-h", track_id="trk-propre", event_type="grazing",
                frame_start=0, frame_end=5, frame_ref="absolute",
                source="heuristic",
                created_at=datetime(2026, 8, 1, 10, 0, 0),
            ))
        with session_scope(self.db_path) as session:
            outcome = tracks_mod.delete_track(session, "trk-propre")
        self.assertTrue(outcome["deleted"])
        self.assertEqual(outcome["events"], 1)
        with session_scope(self.db_path) as session:
            self.assertIsNone(session.get(TemporalEvent, "ev-h"))

    def test_piste_inconnue_repond_sans_lever(self):
        with session_scope(self.db_path) as session:
            outcome = tracks_mod.delete_track(session, "inexistante")
        self.assertFalse(outcome["deleted"])
        self.assertIn("introuvable", outcome["reason"])


class BboxWriteClampTest(unittest.TestCase):
    """Correctifs amont issus de l'enquête « 146 bbox hors cadre ».

    Cause établie sur la vraie base (`scripts/audit_bbox_bounds.py`) : les 146
    boîtes hors cadre viennent **toutes** de `source='cvat'`, dépassent de
    5 × 10⁻⁷ en unités normalisées (deux dix-millièmes de pixel), et
    proviennent des labels publics Roboflow qui arrondissent centre et taille
    séparément à six décimales. Ni la saisie manuelle ni le tracker n'y sont
    pour quelque chose.
    """

    def test_arrondi_des_labels_publics_est_borne_a_l_import(self):
        # Le motif exact relevé en base : cx = 0,028646 et w = 0,057293,
        # donc cx − w/2 = −5 × 10⁻⁷.
        (cx, cy, bw, bh), corrige = normalized_bbox_in_frame(
            0.028646, 0.5, 0.057293, 0.2,
        )
        self.assertTrue(corrige)
        self.assertGreaterEqual(cx - bw / 2, 0.0)
        self.assertLessEqual(cx + bw / 2, 1.0)
        # La correction est infinitésimale : la largeur ne perd que le
        # demi-millionième qui débordait, soit deux dix-millièmes de pixel.
        self.assertAlmostEqual(bw, 0.057293, delta=1e-6)
        self.assertAlmostEqual(cy, 0.5, places=9)

    def test_boite_deja_dans_le_cadre_n_est_pas_touchee(self):
        avant = (0.5, 0.5, 0.2, 0.2)
        (cx, cy, bw, bh), corrige = normalized_bbox_in_frame(*avant)
        self.assertFalse(corrige)
        self.assertEqual((cx, cy, bw, bh), avant)

    def test_debordement_franc_est_ramene_dans_le_cadre(self):
        (cx, cy, bw, bh), corrige = normalized_bbox_in_frame(0.0, 0.5, 0.4, 0.2)
        self.assertTrue(corrige)
        self.assertAlmostEqual(cx - bw / 2, 0.0, places=9)
        self.assertAlmostEqual(bw, 0.2, places=9)

    def test_le_compteur_d_export_ignore_le_bruit_d_arrondi(self):
        """Signaler un dépassement d'un dix-millième de pixel noie le vrai défaut."""
        # Débordement de 5 × 10⁻⁷ normalisé sur 416 px ≈ 2 × 10⁻⁴ px.
        _, bruit = clamp_box_to_image(0.028646, 0.5, 0.057293, 0.2, 416, 416)
        self.assertFalse(bruit)
        # Un vrai débordement, lui, est bien signalé.
        _, reel = clamp_box_to_image(0.5, 0.5, 2.0, 2.0, 416, 416)
        self.assertTrue(reel)
        # Et le seuil est explicite, pas une constante magique enfouie.
        self.assertGreater(CLAMP_EPSILON_PX, 0.0)
        self.assertLess(CLAMP_EPSILON_PX, 1.0)


if __name__ == "__main__":
    unittest.main()
