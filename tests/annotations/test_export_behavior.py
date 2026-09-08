"""Export de comportement (phase 3) : CSV type AVA et JSONL d'intervalles.

Les règles vérifiées ici décident de la validité scientifique du jeu de
données : un événement produit par une heuristique n'est pas de la vérité
terrain, et une position interpolée par-dessus un trou de suivi de dix secondes
est une invention.
"""

from __future__ import annotations

import csv
import json
import unittest
import uuid
from datetime import datetime
from pathlib import Path

from annotations.helpers import TempDbCase, set_camera_params_env, write_sync_frames

from src.annodb import export_behavior
from src.annodb.connection import init_db, session_scope
from src.annodb.event_types import create_event_type, ensure_builtin_types
from src.annodb.export_core import export_dataset
from src.annodb.models import (
    CaptureSession,
    EventType,
    MediaAsset,
    Project,
    TaxonNode,
    TemporalEvent,
    Track,
    TrackSample,
)

WIDTH, HEIGHT = 100, 50
FPS = 10.0
STAMP = datetime(2026, 8, 18, 9, 0, 0)


def _bbox(x1: float, y1: float, x2: float, y2: float) -> str:
    return json.dumps({"x_min": x1, "y_min": y1, "x_max": x2, "y_max": y2})


class BehaviorFixture(TempDbCase):
    """Un média, trois pistes, quatre intervalles aux propriétés choisies.

    - `trk-dense` : un échantillon toutes les 5 frames (0,5,…,60) — la
      densification y trouve toujours une position, exacte ou interpolée sur
      0,5 s ;
    - `trk-trou` : deux échantillons seulement, frames 0 et 40 (4 s d'écart) —
      au-delà de la seconde tolérée, les lignes intermédiaires sont sautées ;
    - `trk-muet` : aucun échantillon — même un événement bien formé ne peut
      rien produire.
    """

    def setUp(self):
        super().setUp()
        init_db(self.db_path, seed=False)
        cam = self.tmp_path / "camera_parameters"
        cam.mkdir(parents=True, exist_ok=True)
        set_camera_params_env(self, cam)

        with session_scope(self.db_path) as session:
            ensure_builtin_types(session)
            project = Project(id=str(uuid.uuid4()), name="comportement")
            session.add(project)
            session.flush()
            session.add(TaxonNode(
                id="sp-a", rank="species", scientific_name="Acanthurus nigrofuscus",
            ))
            session.add(MediaAsset(
                id="vid", project_id=project.id, media_type="video",
                rel_path=str(self.tmp_path / "videos" / "a.mp4"),
                width=WIDTH, height=HEIGHT, fps=FPS, frame_count=600,
            ))
            session.flush()
            session.add_all([
                Track(id="trk-dense", media_id="vid", external_track_id=1,
                      source="bytetrack", taxon_node_id="sp-a",
                      first_frame=0, last_frame=60),
                Track(id="trk-trou", media_id="vid", external_track_id=2,
                      source="bytetrack", first_frame=0, last_frame=40),
                Track(id="trk-muet", media_id="vid", external_track_id=3,
                      source="bytetrack", first_frame=0, last_frame=0),
            ])
            session.flush()
            for frame in range(0, 61, 5):
                session.add(TrackSample(
                    track_id="trk-dense", frame_index=frame,
                    cx=0.5, cy=0.5,
                    bbox_json=_bbox(10 + frame, 5, 30 + frame, 25),
                    origin="auto",
                ))
            session.add(TrackSample(
                track_id="trk-trou", frame_index=0, cx=0.2, cy=0.2,
                bbox_json=_bbox(0, 0, 20, 20), origin="auto",
            ))
            session.add(TrackSample(
                track_id="trk-trou", frame_index=40, cx=0.6, cy=0.6,
                bbox_json=_bbox(60, 20, 80, 40), origin="auto",
            ))

            session.add_all([
                # Manuel, piste dense : la seule source légitime de vérité.
                TemporalEvent(
                    id="ev-manuel", track_id="trk-dense", event_type="grazing",
                    frame_start=0, frame_end=30, frame_ref="absolute",
                    source="manual", author="Camille",
                    created_at=datetime(2026, 8, 1, 10, 0, 0),
                ),
                # Heuristique : présomption automatique, hors dataset.
                TemporalEvent(
                    id="ev-heuristique", track_id="trk-dense", event_type="grazing",
                    frame_start=40, frame_end=50, frame_ref="absolute",
                    source="heuristic",
                    created_at=datetime(2026, 8, 1, 10, 1, 0),
                ),
                # Manuel sur un trou de 4 s : rien à interpoler honnêtement.
                TemporalEvent(
                    id="ev-trou", track_id="trk-trou", event_type="grazing",
                    frame_start=0, frame_end=40, frame_ref="absolute",
                    source="manual", author="Camille",
                    created_at=datetime(2026, 8, 1, 10, 2, 0),
                ),
                # Manuel sur une piste sans aucune position enregistrée.
                TemporalEvent(
                    id="ev-muet", track_id="trk-muet", event_type="grazing",
                    frame_start=0, frame_end=10, frame_ref="absolute",
                    source="manual", author="Camille",
                    created_at=datetime(2026, 8, 1, 10, 3, 0),
                ),
            ])

    def export(self, name: str, **kwargs):
        options = {
            "split_by": "media",
            "taxonomy_rank": "fish",
            "fmt": "ava",
            "created_at": STAMP,
            "dataset_name": "comportement",
        }
        options.update(kwargs)
        with session_scope(self.db_path) as session:
            return export_dataset(session, self.tmp_path / name, **options)

    @staticmethod
    def read_csv(path: Path) -> list[dict]:
        with path.open(encoding="utf-8", newline="") as handle:
            return list(csv.DictReader(handle))

    @staticmethod
    def read_jsonl(path: Path) -> list[dict]:
        return [
            json.loads(line)
            for line in path.read_text(encoding="utf-8").splitlines() if line
        ]


class AvaDensificationTest(BehaviorFixture):
    # `instance_id` de `trk-dense` : rang de la piste dans son média, trié sur
    # `external_track_id` (1 pour trk-dense, 2 pour trk-trou, 3 pour trk-muet).
    DENSE = "1"

    def _dense_instants(self, result) -> list[float]:
        rows = self.read_csv(result.output_dir / export_behavior.EVENTS_CSV)
        return sorted(
            float(r["timestamp_s"]) for r in rows if r["track_id"] == self.DENSE
        )

    def test_hz_pilote_le_pas_de_densification(self):
        """1 Hz sur du 10 i/s = une ligne toutes les 10 frames."""
        une_hz = self.export("hz1", ava_hz=1.0)
        # `ev-manuel` couvre les frames 0..30 sur la piste dense.
        self.assertEqual(self._dense_instants(une_hz), [0.0, 1.0, 2.0, 3.0])
        self.assertEqual(une_hz.manifest["ava"]["hz"], 1.0)

        cinq_hz = self.export("hz5", ava_hz=5.0)
        # 5 Hz sur du 10 i/s : une ligne toutes les 2 frames, soit 16 instants
        # de 0,0 à 3,0 s.
        instants5 = self._dense_instants(cinq_hz)
        self.assertEqual(len(instants5), 16)
        self.assertEqual(instants5[0], 0.0)
        self.assertEqual(instants5[-1], 3.0)
        self.assertEqual(cinq_hz.manifest["ava"]["hz"], 5.0)

    def test_timestamp_est_bien_frame_absolue_sur_fps(self):
        result = self.export("timestamps", ava_hz=1.0)
        rows = self.read_csv(result.output_dir / export_behavior.EVENTS_CSV)
        self.assertTrue(rows)
        for row in rows:
            self.assertAlmostEqual(
                float(row["timestamp_s"]) * FPS,
                round(float(row["timestamp_s"]) * FPS), places=6,
            )

    def test_interpolation_bornee_a_une_seconde(self):
        """Un trou de 4 s ne devient pas une trajectoire lissée."""
        result = self.export("trou", ava_hz=1.0)
        rows = self.read_csv(result.output_dir / export_behavior.EVENTS_CSV)
        ava = result.manifest["ava"]

        # `ev-trou` : échantillons aux frames 0 et 40 seulement. Seule la
        # frame 0 (exacte) sort ; 10, 20, 30 et 40 tombent — 40 étant hors du
        # pas de densification (0, 10, 20, 30), il en reste trois sautées.
        self.assertGreater(ava["rows_skipped"], 0)
        self.assertIn(
            "trou_de_suivi_trop_large", ava["rows_skipped_by_reason"],
        )
        self.assertEqual(ava["rows_skipped_by_reason"]["trou_de_suivi_trop_large"], 3)
        # `ev-muet` : aucune position, donc rien — mais compté.
        self.assertEqual(ava["rows_skipped_by_reason"]["piste_sans_echantillon"], 2)
        self.assertEqual(len(rows), ava["row_count"])

        # Sur la piste dense (un échantillon toutes les 0,5 s), densifier à
        # 5 Hz demande des positions entre deux échantillons : là,
        # l'interpolation s'applique — l'écart reste sous la seconde tolérée.
        dense = self.export("trou_5hz", ava_hz=5.0)
        self.assertGreater(dense.manifest["ava"]["rows_interpolated"], 0)
        self.assertEqual(
            dense.manifest["ava"]["rows_skipped_by_reason"]["trou_de_suivi_trop_large"],
            19, "le trou de 4 s de `trk-trou` n'est jamais comblé",
        )

    def test_interpolation_est_lineaire_et_verifiable(self):
        """La boîte interpolée est exactement au milieu de ses voisines."""
        samples = [
            (0, (0.0, 0.0, 10.0, 10.0), "auto"),
            (10, (20.0, 20.0, 30.0, 30.0), "auto"),
        ]
        box, how = export_behavior.box_at_frame(samples, 5, max_gap_frames=10)
        self.assertEqual(how, "interpolated")
        self.assertEqual(box, (10.0, 10.0, 20.0, 20.0))

        rien, pourquoi = export_behavior.box_at_frame(samples, 5, max_gap_frames=9)
        self.assertIsNone(rien)
        self.assertEqual(pourquoi, "trou_de_suivi_trop_large")

        exact, how = export_behavior.box_at_frame(samples, 10, max_gap_frames=10)
        self.assertEqual(how, "exact")
        self.assertEqual(exact, (20.0, 20.0, 30.0, 30.0))

        dehors, pourquoi = export_behavior.box_at_frame(samples, 99, max_gap_frames=10)
        self.assertIsNone(dehors)
        self.assertEqual(pourquoi, "hors_etendue_de_la_piste")

    def test_coordonnees_normalisees_entre_0_et_1(self):
        result = self.export("normalise", ava_hz=2.0)
        rows = self.read_csv(result.output_dir / export_behavior.EVENTS_CSV)
        self.assertTrue(rows)
        for row in rows:
            for key in ("x1", "y1", "x2", "y2"):
                valeur = float(row[key])
                self.assertGreaterEqual(valeur, 0.0)
                self.assertLessEqual(valeur, 1.0)
            self.assertLess(float(row["x1"]), float(row["x2"]))
            self.assertLess(float(row["y1"]), float(row["y2"]))
        # Première ligne de la piste dense : bbox pixels (10, 5, 30, 25) sur 100×50.
        premiere = min(
            (r for r in rows if r["track_id"] == "1"),
            key=lambda r: float(r["timestamp_s"]),
        )
        self.assertAlmostEqual(float(premiere["x1"]), 0.10, places=6)
        self.assertAlmostEqual(float(premiere["y1"]), 0.10, places=6)
        self.assertAlmostEqual(float(premiere["x2"]), 0.30, places=6)
        self.assertAlmostEqual(float(premiere["y2"]), 0.50, places=6)

    def test_colonnes_dans_l_ordre_ava(self):
        result = self.export("colonnes")
        entete = (result.output_dir / export_behavior.EVENTS_CSV).read_text(
            encoding="utf-8"
        ).splitlines()[0]
        self.assertEqual(
            entete, "video_id,timestamp_s,x1,y1,x2,y2,action_id,track_id",
        )
        self.assertEqual(
            result.manifest["ava"]["columns"], list(export_behavior.AVA_COLUMNS),
        )


class AvaSourceRuleTest(BehaviorFixture):
    def test_seuls_les_evenements_manuels_entrent_au_dataset(self):
        result = self.export("manuel")
        rows = self.read_csv(result.output_dir / export_behavior.EVENTS_CSV)
        exclus = {
            row["annotation_id"]: row["reason"] for row in result.exclusions
        }
        self.assertEqual(exclus.get("ev-heuristique"), "evenement_non_manuel")
        self.assertEqual(result.manifest["ava"]["events_excluded"]["non_manuels"], 1)

        # Aucune ligne ne provient de l'intervalle heuristique : sur la piste
        # dense, celui-ci couvre les frames 40-50 (t = 4,0 à 5,0 s), or rien
        # n'y sort. La ligne à t = 4,0 s de la piste `trk-trou` (instance 2)
        # vient, elle, d'un intervalle manuel — d'où le filtre sur la piste.
        dense = [float(row["timestamp_s"]) for row in rows if row["track_id"] == "1"]
        self.assertTrue(dense)
        self.assertEqual(max(dense), 3.0)

    def test_le_jsonl_montre_ce_que_le_dataset_ecarte(self):
        result = self.export("jsonl")
        lignes = self.read_jsonl(result.output_dir / export_behavior.EVENTS_JSONL)
        par_id = {row["event_id"]: row for row in lignes}
        self.assertEqual(len(lignes), 4)
        self.assertFalse(par_id["ev-heuristique"]["in_ava_dataset"])
        self.assertTrue(par_id["ev-manuel"]["in_ava_dataset"])
        self.assertEqual(par_id["ev-heuristique"]["source"], "heuristic")


class ActionsTableTest(BehaviorFixture):
    def test_actions_csv_vient_de_event_types(self):
        with session_scope(self.db_path) as session:
            create_event_type(session, label="Fuite", description="Le poisson fuit.")
        result = self.export("actions")
        rows = self.read_csv(result.output_dir / export_behavior.ACTIONS_CSV)
        cles = {row["key"] for row in rows}
        self.assertEqual(cles, {"grazing", "bite", "fuite"})
        libelles = {row["key"]: row["label"] for row in rows}
        self.assertEqual(libelles["grazing"], "Broutage")
        self.assertEqual(libelles["bite"], "Bouchée")
        self.assertEqual(libelles["fuite"], "Fuite")
        # `action_id` dense et l'identité durable présente à côté.
        self.assertEqual(
            sorted(int(row["action_id"]) for row in rows), [1, 2, 3],
        )
        for row in rows:
            self.assertTrue(row["event_type_id"])

    def test_type_desactive_sort_de_la_table(self):
        with session_scope(self.db_path) as session:
            autre = create_event_type(session, label="Ponte")
            autre.is_active = False
        result = self.export("desactive")
        rows = self.read_csv(result.output_dir / export_behavior.ACTIONS_CSV)
        self.assertEqual({row["key"] for row in rows}, {"grazing", "bite"})

    def test_action_id_reference_par_events_csv(self):
        result = self.export("reference")
        actions = {
            int(row["action_id"]): row["key"]
            for row in self.read_csv(result.output_dir / export_behavior.ACTIONS_CSV)
        }
        rows = self.read_csv(result.output_dir / export_behavior.EVENTS_CSV)
        self.assertTrue(rows)
        for row in rows:
            self.assertEqual(actions[int(row["action_id"])], "grazing")

    def test_evenement_historique_garde_son_type_apres_desactivation(self):
        with session_scope(self.db_path) as session:
            grazing = session.query(EventType).filter_by(key="grazing").one()
            grazing.is_active = False
        result = self.export("inactif")
        exclus = {row["reason"] for row in result.exclusions}
        self.assertNotIn("type_d_evenement_inconnu_ou_inactif", exclus)
        actions = {
            row["key"] for row in self.read_csv(
                result.output_dir / export_behavior.ACTIONS_CSV
            )
        }
        self.assertIn("grazing", actions)
        rows = self.read_csv(result.output_dir / export_behavior.EVENTS_CSV)
        self.assertTrue(rows)
        self.assertTrue(all(row["track_id"] for row in rows))


class EventsJsonlTest(BehaviorFixture):
    def test_champs_de_la_vue_intervalles(self):
        result = self.export("champs", fmt="events_jsonl")
        lignes = self.read_jsonl(result.output_dir / export_behavior.EVENTS_JSONL)
        ligne = next(row for row in lignes if row["event_id"] == "ev-manuel")
        for champ in (
            "event_id", "event_type", "track_id", "media_id", "session_id",
            "frame_start", "frame_end", "duration_s", "taxon", "source",
            "author", "created_at",
        ):
            self.assertIn(champ, ligne)
        self.assertEqual(ligne["event_type"], "grazing")
        self.assertEqual(ligne["frame_start"], 0)
        self.assertEqual(ligne["frame_end"], 30)
        # Convention inclusive : 31 frames à 10 i/s = 3,1 s.
        self.assertEqual(ligne["duration_frames"], 31)
        self.assertAlmostEqual(ligne["duration_s"], 3.1, places=3)
        self.assertEqual(ligne["taxon"], "Acanthurus nigrofuscus")
        self.assertEqual(ligne["author"], "Camille")
        self.assertEqual(ligne["frame_index_convention"], "absolute")
        self.assertIsNone(ligne["session_id"])

    def test_session_renseignee_quand_le_media_en_a_une(self):
        with session_scope(self.db_path) as session:
            session.add(CaptureSession(
                id="sess-1", name="Sortie", site="Récif Est",
                session_date=datetime(2026, 5, 1), status="done",
                left_media_id="vid", frame_offset=0,
            ))
        result = self.export("session", fmt="events_jsonl")
        lignes = self.read_jsonl(result.output_dir / export_behavior.EVENTS_JSONL)
        self.assertTrue(all(row["session_id"] == "sess-1" for row in lignes))

    def test_metriques_ecologiques_au_manifeste(self):
        manifest = self.export("metriques", fmt="events_jsonl").manifest
        broute = manifest["metrics"]["by_type"]["grazing"]
        self.assertEqual(broute["events"], 4)
        self.assertGreater(broute["total_duration_s"], 0)
        self.assertIsNotNone(broute["events_per_minute"])

    def test_events_jsonl_n_ecrit_pas_de_csv_ava(self):
        result = self.export("sans_ava", fmt="events_jsonl")
        self.assertFalse((result.output_dir / export_behavior.EVENTS_CSV).exists())
        self.assertTrue((result.output_dir / export_behavior.EVENTS_JSONL).exists())

    def test_export_run_trace_le_format(self):
        from sqlalchemy import select

        from src.annodb.models import ExportRun

        result = self.export("run", fmt="events_jsonl")
        with session_scope(self.db_path) as session:
            row = session.scalar(select(ExportRun).where(ExportRun.id == result.run.id))
            self.assertEqual(row.format, "events_jsonl")
            self.assertEqual(row.status, "completed")
            self.assertEqual(row.annotation_count, 4)
            self.assertEqual(row.image_count, 0)


class DatapackageInSealedExportTest(BehaviorFixture):
    """Phase 4a : le descripteur Frictionless entre dans l'export scellé."""

    def test_datapackage_ecrit_et_dans_files_du_manifeste(self):
        from src.annodb.export_core import DATAPACKAGE_NAME

        result = self.export("descripteur")
        path = result.output_dir / DATAPACKAGE_NAME
        self.assertTrue(path.is_file())

        # Le manifeste hache l'export fichier à fichier : le descripteur doit
        # y figurer, sans quoi on pourrait le modifier sans que rien ne bouge.
        noms = {entry["path"] for entry in result.manifest["files"]}
        self.assertIn(DATAPACKAGE_NAME, noms)

        payload = json.loads(path.read_text(encoding="utf-8"))
        chemins = [r["path"] for r in payload["resources"]]
        self.assertEqual(
            chemins, [export_behavior.EVENTS_CSV, export_behavior.ACTIONS_CSV]
        )
        # `created` vient de l'horodatage fourni par l'appelant, jamais de
        # `datetime.now()` : deux exports identiques restent identiques.
        self.assertEqual(payload["created"], STAMP.isoformat(timespec="seconds"))

    def test_les_colonnes_declarees_sont_celles_du_csv_ecrit(self):
        from src.annodb import datapackage

        result = self.export("colonnes")
        for name, kind in (
            (export_behavior.EVENTS_CSV, "ava_events"),
            (export_behavior.ACTIONS_CSV, "ava_actions"),
        ):
            with (result.output_dir / name).open(encoding="utf-8", newline="") as handle:
                header = next(csv.reader(handle))
            self.assertEqual(header, datapackage.field_names(kind), name)

    def test_events_jsonl_seul_n_ecrit_pas_de_descripteur(self):
        """Sans CSV, pas de descripteur de tables : on ne décrit pas du vide."""
        from src.annodb.export_core import DATAPACKAGE_NAME

        result = self.export("sans_csv", fmt="events_jsonl")
        self.assertFalse((result.output_dir / DATAPACKAGE_NAME).exists())


class LegacyFrameRefTest(BehaviorFixture):
    def test_index_timeline_historique_converti_en_absolu(self):
        write_sync_frames(Path(self.tmp_path / "camera_parameters"), 100, 100)
        with session_scope(self.db_path) as session:
            session.add(TemporalEvent(
                id="ev-legacy", track_id="trk-dense", event_type="grazing",
                frame_start=0, frame_end=10, frame_ref="timeline_legacy",
                source="manual", author="Camille",
                created_at=datetime(2026, 8, 1, 11, 0, 0),
            ))
        result = self.export("legacy", fmt="events_jsonl")
        lignes = {
            row["event_id"]: row
            for row in self.read_jsonl(result.output_dir / export_behavior.EVENTS_JSONL)
        }
        legacy = lignes["ev-legacy"]
        self.assertEqual(legacy["frame_ref_source"], "timeline_legacy")
        self.assertEqual(legacy["frame_start"], 100)
        self.assertEqual(legacy["frame_end"], 110)
        # Les lignes déjà absolues ne bougent pas.
        self.assertEqual(lignes["ev-manuel"]["frame_start"], 0)

    def test_offset_indeterminable_exclut_avec_sa_raison(self):
        with session_scope(self.db_path) as session:
            session.add(TemporalEvent(
                id="ev-sans-offset", track_id="trk-dense", event_type="grazing",
                frame_start=0, frame_end=10, frame_ref="timeline_legacy",
                source="manual", created_at=datetime(2026, 8, 1, 11, 0, 0),
            ))
        result = self.export("sans_offset", fmt="events_jsonl")
        exclus = {row["annotation_id"]: row["reason"] for row in result.exclusions}
        self.assertEqual(
            exclus.get("ev-sans-offset"), "index_timeline_legacy_sans_offset_sync",
        )


class BehaviorSplitTest(BehaviorFixture):
    def test_le_decoupage_par_groupe_est_ecrit_et_ne_fuit_pas(self):
        result = self.export("split")
        splits = json.loads(
            (result.output_dir / "splits.json").read_text(encoding="utf-8")
        )
        self.assertEqual(splits["split_by"], "media")
        self.assertEqual(splits["unit"], "temporal_events")
        listes = [g for names in splits["group_ids"].values() for g in names]
        self.assertEqual(len(listes), len(set(listes)))
        # Un seul média : il ne peut être que d'un seul côté.
        medias = [
            m for noms in splits["media_ids"].values() for m in noms
        ]
        self.assertEqual(medias, ["vid"])


if __name__ == "__main__":
    unittest.main()
