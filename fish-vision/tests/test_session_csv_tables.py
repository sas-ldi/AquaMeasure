"""Recette des données exportées pour les statistiques, sans dépendre des vidéos."""

import csv
import json

from sqlalchemy import delete, select

from tests.test_session_export_workflow import SessionExportFixture, _json_result
from src.annodb.connection import session_scope
from src.annodb.export_session_tables import TABLES, export_session_tables
from src.annodb.models import (
    CaptureSession, EventType, FrameAbundance, MediaAsset, SessionMediaPair,
    SpatialAnnotation, SpatialBehaviorFlag, TemporalEvent, Track, TrackSample,
)
from src.annodb import sessions


class SessionCsvTablesTest(SessionExportFixture):
    # La fixture de session multi-prises est partagée ; seuls les tests de cette
    # classe ajoutent mesures, comptages et actions.
    def setUp(self):
        super().setUp()
        with session_scope(self.db_path) as db:
            for mid in self.media_ids:
                media = db.get(MediaAsset, mid)
                media.fps, media.frame_count = 10.0, 80
            db.add_all([
                Track(id="track-a", media_id=self.media_ids[0], external_track_id=7,
                      taxon_node_id="species-test", identification_status="identified",
                      first_frame=4, last_frame=8, source="manual"),
                Track(id="track-empty", media_id=self.media_ids[2], external_track_id=7,
                      first_frame=1, last_frame=2, source="bytetrack"),
                Track(id="outside-track", media_id="outside-media", external_track_id=7),
                EventType(id="ray-type", key="ray", label="Passage de raie", scope="instant",
                          is_active=False, symbol="◆", color="#123456"),
            ])
            db.flush()
            for frame in (4, 6, 8):
                db.add(TrackSample(track_id="track-a", frame_index=frame, cx=0.25, cy=0.5,
                    bbox_json=json.dumps({"x_min": 1.123456, "y_min": 2, "x_max": 30, "y_max": 20}),
                    position_x_mm=-15.25, position_y_mm=0, position_z_mm=400.5,
                    match_score=0.78, match_method="stereo", origin="keyframe", edited_by="Camille"))
            ann = db.get(SpatialAnnotation, "session-ann-1")
            ann.track_id = "track-a"
            ann.measurement_mm = 123.4567
            ann.frame_index, ann.frame_ref = 0, "timeline_legacy"
            ann.position_x_mm, ann.position_y_mm = -5.25, 0
            ann.author, ann.reviewed_by = "Camille", "Camille"
            db.add(SpatialAnnotation(id="second-measure", media_id=self.media_ids[0],
                frame_index=8, frame_ref="absolute", geom_type="bbox", geometry_json=ann.geometry_json,
                measurement_mm=130.125, track_id="track-a", taxon_node_id="species-test",
                identification_status="identified", source="manual"))
            db.add_all([
                TemporalEvent(id="bite-exact", track_id="track-a", event_type="bite",
                              frame_start=5, frame_end=5, frame_ref="absolute", source="manual", author="Camille"),
                TemporalEvent(id="grazing", track_id="track-a", event_type="grazing",
                              frame_start=0, frame_end=4, frame_ref="timeline_legacy", source="manual"),
                TemporalEvent(id="ray-empty", track_id="track-empty", event_type="ray",
                              frame_start=1, frame_end=1, frame_ref="absolute", source="manual"),
                TemporalEvent(id="automatic", track_id="track-a", event_type="grazing",
                              frame_start=4, frame_end=8, frame_ref="absolute", source="heuristic", confidence=0.3),
                TemporalEvent(id="outside-event", track_id="outside-track", event_type="bite",
                              frame_start=0, frame_end=0, frame_ref="absolute"),
                SpatialBehaviorFlag(spatial_annotation_id="session-ann-2", event_type="ray", author="Léa"),
            ])
            for mid, frame, ai, manual, validated in (
                (self.media_ids[0], 0, 20, 0, True),
                (self.media_ids[0], 1, 99, 99, False),
                (self.media_ids[0], 2, 4, None, True),
                (self.media_ids[2], 0, 8, 6, True),
                ("outside-media", 0, 200, 200, True),
            ):
                db.add(FrameAbundance(media_id=mid, frame_index=frame, frame_ref="absolute",
                                      ai_count=ai, manual_count=manual, validated=validated))

    def export_tables(self, name="tables"):
        output = self.tmp_path / name
        with session_scope(self.db_path) as db:
            capture = db.get(CaptureSession, self.session_id)
            preview = sessions.session_as_dict(db, capture)
            media = []
            for pair in preview["pairs"]:
                for role in ("left", "right"):
                    media.append({**pair[role], "pair_number": pair["position"] + 1,
                                  "role": "gauche" if role == "left" else "droite"})
            result = export_session_tables(db, capture, preview, media, output)
        return output, result

    @staticmethod
    def rows(output, name):
        with (output / name).open(encoding="utf-8-sig", newline="") as handle:
            return list(csv.DictReader(handle))

    def test_mesures_completes_sans_dupliquer_les_actions(self):
        output, result = self.export_tables()
        observations = {row["annotation_id"]: row for row in self.rows(output, "session.csv")}
        self.assertEqual(set(observations), {"session-ann-1", "session-ann-2", "second-measure"})
        first = observations["session-ann-1"]
        self.assertEqual(first["measurement_mm"], "123.4567")
        self.assertEqual(first["position_x_mm"], "-5.25")
        self.assertEqual(first["position_y_mm"], "0.0")
        self.assertEqual(first["has_tracking"], "1")
        self.assertEqual(first["track_sample_count"], "3")
        self.assertEqual(first["has_track_actions"], "1")
        self.assertEqual(first["fish_key"], observations["second-measure"]["fish_key"])
        self.assertEqual(observations["session-ann-2"]["has_observation_actions"], "1")
        self.assertEqual(observations["session-ann-2"]["measurement_mm"], "")
        events = self.rows(output, "evenements.csv")
        self.assertEqual(len(events), 5)  # 4 événements de piste + 1 action de fiche.
        self.assertEqual(len({row["event_id"] for row in events}), 5)
        self.assertEqual(result["files"]["positions_pistes.csv"], 3)
        self.assertEqual(result["files"]["pistes.csv"], 2)

    def test_frames_exactes_points_durees_et_types_personnalises(self):
        output, _ = self.export_tables()
        events = {row["event_id"]: row for row in self.rows(output, "evenements.csv")}
        bite = events["bite-exact"]
        self.assertEqual((bite["frame_start"], bite["start_time_s"]), ("5", "0.5"))
        self.assertEqual(bite["scope"], "instant")
        self.assertEqual(bite["duration_s"], "")
        grazing = events["grazing"]
        self.assertEqual((grazing["frame_start"], grazing["frame_end"]), ("4", "8"))
        self.assertEqual((grazing["duration_frames"], grazing["duration_s"]), ("5", "0.5"))
        self.assertEqual(grazing["frame_ref_source"], "timeline_legacy")
        ray = events["ray-empty"]
        self.assertEqual(ray["label"], "Passage de raie")
        self.assertEqual(ray["type_active"], "0")
        self.assertEqual(ray["track_id"], "track-empty")
        self.assertEqual(events["automatic"]["source"], "heuristic")
        self.assertEqual(events["automatic"]["confidence"], "0.3")
        point = events["observation:session-ann-2:ray"]
        self.assertEqual(point["annotation_id"], "session-ann-2")
        self.assertEqual(point["author"], "Léa")

    def test_maxn_validations_zero_manuel_et_toutes_les_prises(self):
        output, result = self.export_tables()
        counts = {(row["media_id"], row["frame_index"]): row for row in self.rows(output, "comptages.csv")}
        self.assertEqual(len(counts), 4)
        self.assertEqual(counts[(self.media_ids[0], "0")]["count_used"], "0")
        self.assertEqual(counts[(self.media_ids[0], "1")]["validated"], "0")
        self.assertEqual(counts[(self.media_ids[0], "2")]["manual_count"], "")
        videos = {row["media_id"]: row for row in self.rows(output, "videos.csv")}
        self.assertEqual(videos[self.media_ids[0]]["max_n_media"], "4")
        self.assertEqual(videos[self.media_ids[2]]["max_n_media"], "6")
        self.assertEqual(videos[self.media_ids[1]]["max_n_media"], "")
        self.assertEqual(result["max_n_session"], 6)
        summary = self.rows(output, "resume_session.csv")[0]
        self.assertEqual(summary["measurement_count"], "2")
        self.assertEqual(summary["validated_frame_count"], "3")
        with session_scope(self.db_path) as db:
            self.assertEqual(sessions.session_counts(db, db.get(CaptureSession, self.session_id))["max_n"], 6)

    def test_aucune_video_necessaire_et_positions_sans_arrondi(self):
        for path in self.paths:
            path.unlink()
        output, _ = self.export_tables()
        self.assertTrue(all(row["video_available"] == "0" for row in self.rows(output, "videos.csv")))
        positions = self.rows(output, "positions_pistes.csv")
        self.assertEqual([row["frame_index"] for row in positions], ["4", "6", "8"])
        self.assertEqual(positions[0]["bbox_x1"], "1.123456")
        self.assertEqual(positions[0]["position_x_mm"], "-15.25")
        self.assertEqual(positions[0]["edited_by"], "Camille")

    def test_export_cli_livre_toutes_les_tables_sans_video(self):
        for path in self.paths:
            path.unlink()
        process = self._run(self.tmp_path / "csv-cli", "--format", "csv")
        self.assertEqual(process.returncode, 0, process.stdout + process.stderr)
        from pathlib import Path
        output = Path(_json_result(process.stdout)["output_path"])
        summary = json.loads((output / "session.json").read_text(encoding="utf-8"))
        self.assertEqual(summary["csv_tables"]["files"]["evenements.csv"], 5)
        self.assertEqual(summary["session"]["event_count"], 5)
        self.assertEqual(summary["session"]["max_n"], 6)
        self.assertEqual(summary["videos_skipped"], [])
        self.assertEqual(len(self.rows(output, "session.csv")), 3)

    def test_offset_inconnu_conserve_la_ligne_sans_inventer_une_frame(self):
        with session_scope(self.db_path) as db:
            capture = db.get(CaptureSession, self.session_id)
            capture.frame_offset = None
            for pair in db.scalars(select(SessionMediaPair).where(SessionMediaPair.session_id == capture.id)):
                pair.frame_offset = None
        output, result = self.export_tables()
        first = next(row for row in self.rows(output, "session.csv") if row["annotation_id"] == "session-ann-1")
        self.assertEqual((first["frame_index"], first["time_s"]), ("", ""))
        self.assertEqual((first["frame_index_source"], first["frame_status"]), ("0", "offset_unknown"))
        event = next(row for row in self.rows(output, "evenements.csv") if row["event_id"] == "grazing")
        self.assertEqual(event["frame_start"], "")
        self.assertEqual(event["frame_end_source"], "4")
        self.assertEqual(result["unresolved_event_frames"], 1)

    def test_zero_maxn_est_distinct_de_l_absence_de_comptage(self):
        with session_scope(self.db_path) as db:
            db.execute(delete(FrameAbundance))
        output, result = self.export_tables("empty-counts")
        self.assertIsNone(result["max_n_session"])
        self.assertEqual(self.rows(output, "resume_session.csv")[0]["max_n_session"], "")
        with session_scope(self.db_path) as db:
            db.add(FrameAbundance(media_id=self.media_ids[0], frame_index=0, frame_ref="absolute",
                                  ai_count=30, manual_count=0, validated=True))
        output, result = self.export_tables("zero-count")
        self.assertEqual(result["max_n_session"], 0)
        self.assertEqual(self.rows(output, "resume_session.csv")[0]["max_n_session"], "0")

    def test_colonnes_documentees_texte_excel_et_entetes_stables(self):
        with session_scope(self.db_path) as db:
            db.get(CaptureSession, self.session_id).notes = '=1+1, "texte"\nligne suivante'
        output, result = self.export_tables()
        dictionary = self.rows(output, "dictionnaire.csv")
        for name, (fields, _grain) in TABLES.items():
            self.assertEqual([row["column"] for row in dictionary if row["file"] == name], fields)
            self.assertEqual(result["files"][name], len(self.rows(output, name)))
            self.assertTrue((output / name).read_bytes().startswith(b"\xef\xbb\xbf"))
        self.assertEqual(self.rows(output, "resume_session.csv")[0]["notes"], '\'=1+1, "texte"\nligne suivante')
