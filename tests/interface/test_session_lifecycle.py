"""Cycle de vie de la session active : une seule règle, Sessions.selectedId.

La session suit la vidéo, sinon la vidéo rejoint la session. La session
active survit au redémarrage ; ses champs s'enregistrent sans bouton ; une
prise rattachée garde sa synchro figée et peut changer de session.
"""

from __future__ import annotations

import json
import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import numpy as np
from PySide6.QtWidgets import QApplication

APP_ROOT = Path(__file__).resolve().parents[2] / "src" / "interface"
REPO_ROOT = APP_ROOT.parent
FV_ROOT = REPO_ROOT / "annotations"
for candidate in (APP_ROOT, REPO_ROOT, FV_ROOT):
    if str(candidate) not in sys.path:
        sys.path.insert(0, str(candidate))

import src  # noqa: E402

for namespace_part in (APP_ROOT / "src", FV_ROOT / "src"):
    if str(namespace_part) not in src.__path__:
        src.__path__.append(str(namespace_part))

from src.annodb.connection import init_db  # noqa: E402
from src.controllers.app_controller import AppController  # noqa: E402
from src.controllers.session_controller import ACTIVE_SESSION_FILE  # noqa: E402
from src.util import paths  # noqa: E402


def _write_video(path: Path, shade: int) -> None:
    import cv2

    writer = cv2.VideoWriter(
        str(path), cv2.VideoWriter_fourcc(*"mp4v"), 30.0, (64, 48)
    )
    assert writer.isOpened(), f"encodeur indisponible pour {path}"
    for _ in range(5):
        writer.write(np.full((48, 64, 3), shade, dtype=np.uint8))
    writer.release()


class _ImmediateThread:
    """L'attache part dans un thread : ici, elle s'exécute sur place."""

    def __init__(self, *, target, daemon: bool):
        self._target = target

    def start(self):
        self._target()


class SessionLifecycleTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory(prefix="session_lifecycle_")
        self.addCleanup(self._tmp.cleanup)
        self.root = Path(self._tmp.name)
        self.cam = self.root / "camera_parameters"
        self.cam.mkdir()
        from src.annodb.rectify import ENV_CAMERA_PARAMS

        for name, value in (
            (ENV_CAMERA_PARAMS, str(self.cam)),
            ("FISH_VISION_DB", str(self.root / "annotations.db")),
            ("AQUAMEASURE_STORAGE_CONFIG", str(self.root / "storage.json")),
            ("FISH_VISION_SETTINGS", str(self.root / "settings.json")),
        ):
            self._set_env(name, value)
        from src.annodb import storage_config

        storage_config.invalidate_cache()
        self.addCleanup(storage_config.invalidate_cache)
        orig_dir = paths.camera_params_dir
        paths.camera_params_dir = staticmethod(lambda: self.cam)
        self.addCleanup(setattr, paths, "camera_params_dir", orig_dir)
        init_db(self.root / "annotations.db", seed=True)

        self.videos = {}
        for shade, name in enumerate(("L1", "R1", "L2", "R2")):
            path = self.root / f"{name}.mp4"
            _write_video(path, 40 * shade)
            self.videos[name] = str(path)
        self.ctrl = self._new_controller()

    def tearDown(self):
        self._drop_controller(self.ctrl)
        from src.annodb import connection

        if connection._engine is not None:
            connection._engine.dispose()
            connection._engine = None
            connection._SessionLocal = None
        connection._migrated_paths.clear()

    # ── outils ─────────────────────────────────────────────────────

    def _set_env(self, name: str, value: str) -> None:
        previous = os.environ.get(name)
        os.environ[name] = value
        if previous is None:
            self.addCleanup(os.environ.pop, name, None)
        else:
            self.addCleanup(os.environ.__setitem__, name, previous)

    def _new_controller(self) -> AppController:
        ctrl = AppController()
        self.app.processEvents()
        return ctrl

    def _drop_controller(self, ctrl: AppController) -> None:
        self.app.processEvents()
        ctrl.deleteLater()
        self.app.processEvents()

    def _create(self, site: str) -> str:
        import fish_annotate as fa

        return fa.create_session(site=site, session_date="2026-09-20")["session_id"]

    def _select(self, session_id: str) -> None:
        sessions = self.ctrl.sessions()
        sessions.refresh()
        sessions.selectSession(sessions._sessions.index_of(session_id))

    def _load(self, left: str, right: str = "") -> None:
        measure = self.ctrl.measure()
        measure._left = left
        measure._right = right
        with patch("src.controllers.session_controller.threading.Thread", _ImmediateThread):
            measure.leftVideoChanged.emit()
            measure.rightVideoChanged.emit()
            for _ in range(3):
                self.app.processEvents()

    def _attach(self, session_id: str, left: str, right: str, **kw) -> dict:
        import fish_annotate as fa

        return fa.attach_media_pair(session_id, self.videos[left], self.videos[right], **kw)

    # ── session mémorisée ──────────────────────────────────────────

    def test_session_active_restauree_apres_recreation(self):
        sid = self._create("Récif A")
        self._select(sid)
        self._drop_controller(self.ctrl)
        self.ctrl = self._new_controller()
        self.assertEqual(self.ctrl.sessions().selectedId, sid)
        self.assertEqual(self.ctrl.data().sessionId, sid)
        self.assertEqual(self.ctrl.data().sessionSite, "Récif A")

    def test_session_memorisee_supprimee_est_ignoree(self):
        (self.cam / ACTIVE_SESSION_FILE).write_text(
            json.dumps({"session_id": "disparue"}), encoding="utf-8"
        )
        self._drop_controller(self.ctrl)
        self.ctrl = self._new_controller()
        self.assertEqual(self.ctrl.sessions().selectedId, "")
        self.assertEqual(self.ctrl.data().sessionId, "")

    def test_suppression_vide_la_session_courante(self):
        sid = self._create("Récif A")
        self._select(sid)
        self.assertEqual(self.ctrl.data().sessionId, sid)
        self.ctrl.sessions().deleteSelectedSession()
        self.assertEqual(self.ctrl.sessions().selectedId, "")
        self.assertEqual(self.ctrl.data().sessionId, "")
        self.assertFalse((self.cam / ACTIVE_SESSION_FILE).exists())

    # ── champs enregistrés sans bouton ─────────────────────────────

    def test_champs_enregistres_en_base_et_sur_les_medias(self):
        import fish_annotate as fa
        import fish_db_stats as fdb

        sid = self._create("Récif A")
        row = self._attach(sid, "L1", "R1")
        self._select(sid)
        data = self.ctrl.data()
        data.sessionSite = "Récif B"
        data.sessionNotes = "houle"
        data.saveSessionFields()
        stored = fa.get_session(sid)
        self.assertEqual(stored["site"], "Récif B")
        self.assertEqual(stored["notes"], "houle")
        for media_id in (row["left_media_id"], row["right_media_id"]):
            self.assertEqual(fdb.get_session_metadata(media_id)["site"], "Récif B")

    def test_lieu_vide_refuse_et_valeur_restauree(self):
        import fish_annotate as fa

        sid = self._create("Récif A")
        self._select(sid)
        data = self.ctrl.data()
        data.sessionSite = ""
        data.saveSessionFields()
        self.assertEqual(data.statusText, "Lieu et date obligatoires")
        self.assertEqual(data.sessionSite, "Récif A")
        self.assertEqual(fa.get_session(sid)["site"], "Récif A")

    # ── la session suit la vidéo, sinon la vidéo rejoint la session ──

    def test_paire_d_une_autre_session_la_rend_active(self):
        owner = self._create("Récif A")
        other = self._create("Récif B")
        self._attach(owner, "L1", "R1")
        self._select(other)
        self._load(self.videos["L1"], self.videos["R1"])
        self.assertEqual(self.ctrl.sessions().selectedId, owner)
        self.assertEqual(self.ctrl.data().sessionId, owner)
        self.assertIn("ouverte", self.ctrl.sessions().statusText)

    def test_demi_paire_non_attachee(self):
        import fish_annotate as fa

        sid = self._create("Récif A")
        self._select(sid)
        self._load(self.videos["L1"])
        self.assertEqual(fa.get_session(sid)["pair_count"], 0)
        # Le geste explicite refuse lui aussi la demi-paire.
        with patch("src.controllers.session_controller.threading.Thread", _ImmediateThread):
            self.ctrl.sessions().attachCurrentPair()
        self.assertEqual(fa.get_session(sid)["pair_count"], 0)

    def test_changer_une_seule_video_remplace_celle_de_la_prise(self):
        import fish_annotate as fa

        sid = self._create("Récif A")
        self._attach(sid, "L1", "R1", frame_offset=5)
        self._select(sid)
        self._load(self.videos["L1"], self.videos["R2"])
        stored = fa.get_session(sid)
        self.assertEqual(stored["pair_count"], 1)
        self.assertEqual(stored["pairs"][0]["right_media_id"], fa.resolve_media_id(self.videos["R2"]))
        # L'ancien offset décrivait l'ancienne paire.
        self.assertIsNone(stored["pairs"][0]["frame_offset"])

    def test_videos_de_deux_prises_differentes_ne_bougent_pas(self):
        import fish_annotate as fa

        first = self._create("Récif A")
        second = self._create("Récif B")
        self._attach(first, "L1", "R1")
        self._attach(second, "L2", "R2")
        self._select(first)
        self._load(self.videos["L1"], self.videos["R2"])
        self.assertIn("différentes", self.ctrl.sessions().statusText)
        self.assertEqual(fa.get_session(first)["pairs"][0]["right_media_id"],
                         fa.resolve_media_id(self.videos["R1"]))
        self.assertEqual(fa.get_session(second)["pairs"][0]["left_media_id"],
                         fa.resolve_media_id(self.videos["L2"]))

    def test_choisir_une_session_y_range_la_paire_libre_chargee(self):
        import fish_annotate as fa

        sid = self._create("Récif A")
        self._load(self.videos["L1"], self.videos["R1"])
        self.assertEqual(fa.get_session(sid)["pair_count"], 0)
        with patch("src.controllers.session_controller.threading.Thread", _ImmediateThread):
            self._select(sid)
            for _ in range(3):
                self.app.processEvents()
        self.assertEqual(fa.get_session(sid)["pair_count"], 1)

    def test_choisir_une_session_ne_revient_pas_a_celle_de_la_paire(self):
        owner = self._create("Récif A")
        other = self._create("Récif B")
        self._attach(owner, "L1", "R1")
        self._load(self.videos["L1"], self.videos["R1"])
        self.assertEqual(self.ctrl.sessions().selectedId, owner)
        with patch("src.controllers.session_controller.threading.Thread", _ImmediateThread):
            self._select(other)
            for _ in range(3):
                self.app.processEvents()
        self.assertEqual(self.ctrl.sessions().selectedId, other)

    def test_paire_complete_rejoint_la_session_active(self):
        import fish_annotate as fa
        import fish_db_stats as fdb

        sid = self._create("Récif A")
        self._select(sid)
        self._load(self.videos["L1"], self.videos["R1"])
        stored = fa.get_session(sid)
        self.assertEqual(stored["pair_count"], 1)
        self.assertEqual(self.ctrl.data().sessionId, sid)
        self.assertEqual(
            fdb.get_session_metadata(stored["right_media_id"])["site"], "Récif A"
        )

    # ── prise figée et déplaçable ──────────────────────────────────

    def _synced(self, left: str, right: str, frames=(7, 3)) -> None:
        """Synchro enregistrée (videos.txt + sync_frames.npy) pour cette paire."""
        (self.cam / "videos.txt").write_text(
            f"{self.videos[left]}\n{self.videos[right]}\n", encoding="utf-8"
        )
        np.save(self.cam / "sync_frames.npy", np.asarray(frames, dtype=np.int64))

    def test_reattache_laisse_la_synchro_figee(self):
        import fish_annotate as fa

        sid = self._create("Récif A")
        self._attach(sid, "L1", "R1", frame_offset=5, calibration_profile="avant")
        self._synced("L1", "R1")
        again = self._attach(sid, "L1", "R1", calibration_profile="apres")
        self.assertEqual(again["pair_count"], 1)
        self.assertEqual(again["pairs"][0]["frame_offset"], 5)
        self.assertEqual(again["pairs"][0]["calibration_profile"], "avant")
        self.assertEqual(fa.get_session(sid)["frame_offset"], 5)

    def test_offset_explicite_remplace_l_offset_fige(self):
        sid = self._create("Récif A")
        self._attach(sid, "L1", "R1", frame_offset=5)
        again = self._attach(sid, "L1", "R1", frame_offset=9)
        self.assertEqual(again["pairs"][0]["frame_offset"], 9)

    def test_synchro_d_une_autre_paire_n_est_pas_figee(self):
        from src.annodb.frame_ref import timeline_offset

        sid = self._create("Récif A")
        self._synced("L2", "R2")
        row = self._attach(sid, "L1", "R1")
        self.assertIsNone(row["pairs"][0]["frame_offset"])
        self._synced("L1", "R1")
        other = self._create("Récif B")
        row = self._attach(other, "L2", "R2")
        self.assertIsNone(row["pairs"][0]["frame_offset"])
        row = self._attach(sid, "L1", "R1")
        self.assertEqual(row["pairs"][0]["frame_offset"], timeline_offset(self.cam))

    def test_nouvelle_synchro_refige_la_prise(self):
        import fish_annotate as fa
        from src.annodb.frame_ref import timeline_offset

        sid = self._create("Récif A")
        self._synced("L1", "R1", frames=(7, 3))
        self._attach(sid, "L1", "R1")
        sync = self.ctrl.sync()
        sync._left, sync._right = self.videos["L1"], self.videos["R1"]
        sync._left_fc = sync._right_fc = 5
        sync.applyManual(1, 4)
        self.assertEqual(fa.get_session(sid)["pairs"][0]["frame_offset"], timeline_offset(self.cam))
        self.assertEqual(timeline_offset(self.cam), 1)

    def test_deplacer_une_prise_emporte_ses_observations(self):
        import fish_annotate as fa
        from src.annodb.connection import session_scope
        from src.annodb.models import CaptureSession

        source = self._create("Récif A")
        target = self._create("Récif B")
        self._attach(source, "L1", "R1", frame_offset=3)
        self._attach(source, "L2", "R2", frame_offset=4)
        fa.add_observation(
            self.videos["L1"], 1, {"x1": 1.0, "y1": 1.0, "x2": 9.0, "y2": 9.0},
            source="manual", frame_ref="absolute",
        )
        self._select(source)
        self.ctrl.sessions().movePairTo(0, target)

        moved_to = fa.get_session(target)
        self.assertEqual(moved_to["pair_count"], 1)
        self.assertEqual(moved_to["pairs"][0]["frame_offset"], 3)
        counts = {row["session_id"]: row for row in fa.list_sessions_v2()}
        self.assertEqual(counts[target]["observation_count"], 1)
        self.assertEqual(counts[source]["observation_count"], 0)
        self.assertEqual(counts[source]["pair_count"], 1)
        with session_scope() as db:
            src_row = db.get(CaptureSession, source)
            dst_row = db.get(CaptureSession, target)
            l1 = fa.resolve_media_id(self.videos["L1"])
            l2 = fa.resolve_media_id(self.videos["L2"])
            self.assertEqual(dst_row.left_media_id, l1)
            self.assertEqual(dst_row.frame_offset, 3)
            self.assertEqual(src_row.left_media_id, l2)
            self.assertEqual(src_row.frame_offset, 4)
        self.assertEqual(fa.find_session_for_media(l1)["session_id"], target)
        # L'export d'abondance lit lieu et date sur les médias.
        import fish_db_stats as fdb

        self.assertEqual(fdb.get_session_metadata(l1)["site"], "Récif B")
        self.assertTrue(fdb.get_session_metadata(l1)["session_date"].startswith("2026-09-20"))

    def test_deplacer_la_derniere_prise_vide_les_pointeurs(self):
        import fish_annotate as fa
        from src.annodb.connection import session_scope
        from src.annodb.models import CaptureSession

        source = self._create("Récif A")
        target = self._create("Récif B")
        self._attach(source, "L1", "R1")
        self._select(source)
        self.ctrl.sessions().movePairTo(0, target)
        with session_scope() as db:
            src_row = db.get(CaptureSession, source)
            self.assertIsNone(src_row.left_media_id)
            self.assertIsNone(src_row.right_media_id)
        self.assertFalse(fa.get_session(source)["has_pair"])


if __name__ == "__main__":
    unittest.main()
