"""Page Sessions - une sortie, plusieurs prises stéréo, un seul bilan.

Une session peut être créée avant la sortie puis recevoir plusieurs paires de
vidéos au fil de la journée. Chaque paire fige sa propre synchronisation.
"""

from __future__ import annotations

import json
import os
import sys
import threading
from datetime import datetime
from pathlib import Path

from PySide6.QtCore import Property, QObject, QTimer, Signal, Slot

from src.controllers.data_controller import DataController
from src.controllers.measure_controller import MeasureController
from src.models.session_rows_model import SessionRowsModel
from src.util import paths
from src.util.log_model import LogModel

# Session active mémorisée entre deux lancements (camera_parameters/).
ACTIVE_SESSION_FILE = "active_session.json"


class SessionController(QObject):
    sessionsChanged = Signal()
    statusTextChanged = Signal()
    selectedChanged = Signal()
    moveTargetsChanged = Signal()
    activeSessionChanged = Signal()
    formChanged = Signal()
    busyChanged = Signal()
    _pairAttached = Signal(dict)
    _pairFailed = Signal(str)

    def __init__(
        self,
        measure: MeasureController,
        data: DataController | None = None,
        app: QObject | None = None,
        parent=None,
    ):
        super().__init__(parent)
        self._measure = measure
        self._data = data
        self._app = app
        self._logs = LogModel(self)
        self._sessions = SessionRowsModel(self)
        self._status = ""
        self._busy = False
        self._selected_row = -1
        self._selected: dict = {}
        self._form_name = ""
        self._form_site = ""
        self._form_date = datetime.now().strftime("%Y-%m-%d")
        self._form_notes = ""
        self._db_ok = self._check_db()
        self._auto_attach_scheduled = False
        self._pairAttached.connect(self._finish_pair_attached)
        self._pairFailed.connect(self._finish_pair_failed)
        # « Cette paire appartient-elle a la session active ? » depend
        # autant de la session choisie que de la paire chargee.
        self._measure.leftVideoChanged.connect(self.activeSessionChanged)
        self._measure.rightVideoChanged.connect(self.activeSessionChanged)
        self._measure.leftVideoChanged.connect(self._schedule_auto_attach)
        self._measure.rightVideoChanged.connect(self._schedule_auto_attach)
        if self._data is not None:
            self._data.sessionFieldsSaved.connect(self.refresh)

    # ── utilitaires ────────────────────────────────────────────────────

    def _repo(self) -> Path:
        return paths.app_root()

    def _ensure_fv(self) -> None:
        fv = self._repo() / "annotations"
        if fv.is_dir() and str(fv) not in sys.path:
            sys.path.insert(0, str(fv))

    def _check_db(self) -> bool:
        try:
            self._ensure_fv()
            import fish_annotate as fa

            return fa.is_available()
        except ImportError:
            return False
        except Exception:
            return False

    def _set_status(self, msg: str) -> None:
        self._status = msg
        self.statusTextChanged.emit()

    def _set_busy(self, value: bool) -> None:
        if self._busy != value:
            self._busy = value
            self.busyChanged.emit()

    # ── propriétés exposées au QML ─────────────────────────────────────

    @Property(QObject, constant=True)
    def logs(self):
        return self._logs

    @Property(QObject, constant=True)
    def sessions(self):
        return self._sessions

    @Property(int, notify=sessionsChanged)
    def sessionCount(self):
        return self._sessions.rowCount()

    @Property(str, notify=statusTextChanged)
    def statusText(self):
        return self._status

    @Property(bool, notify=busyChanged)
    def busy(self):
        return self._busy

    @Property(bool, constant=True)
    def dbAvailable(self):
        return self._db_ok

    @Property(int, notify=selectedChanged)
    def selectedRow(self):
        return self._selected_row

    @Property(str, notify=selectedChanged)
    def selectedId(self):
        return str(self._selected.get("session_id", ""))

    @Property(str, notify=selectedChanged)
    def selectedName(self):
        return str(self._selected.get("name", ""))

    @Property(str, notify=selectedChanged)
    def selectedStatus(self):
        return str(self._selected.get("status", ""))

    @Property(str, notify=selectedChanged)
    def selectedLeftPath(self):
        return str(self._selected.get("left_path", ""))

    @Property(str, notify=selectedChanged)
    def selectedRightPath(self):
        return str(self._selected.get("right_path", ""))

    @Property(bool, notify=selectedChanged)
    def selectedLeftAvailable(self):
        return bool(self._selected.get("left_available"))

    @Property(bool, notify=selectedChanged)
    def selectedRightAvailable(self):
        return bool(self._selected.get("right_available"))

    @Property(bool, notify=selectedChanged)
    def selectedHasPair(self):
        return bool(self._selected.get("has_pair"))

    @Property(int, notify=selectedChanged)
    def selectedPairCount(self):
        return int(self._selected.get("pair_count", 0) or 0)

    @Property(int, notify=selectedChanged)
    def selectedObservationCount(self):
        return int(self._selected.get("observation_count", 0) or 0)

    @Property(int, notify=selectedChanged)
    def selectedMeasurementCount(self):
        return int(self._selected.get("measurement_count", 0) or 0)

    @Property(int, notify=selectedChanged)
    def selectedEventCount(self):
        return int(self._selected.get("event_count", 0) or 0)

    @Property(int, notify=selectedChanged)
    def selectedTrackCount(self):
        return int(self._selected.get("track_count", 0) or 0)

    @Property(int, notify=selectedChanged)
    def selectedMaxN(self):
        return int(self._selected.get("max_n", 0) or 0)

    @Property(list, notify=selectedChanged)
    def selectedSpeciesSummary(self):
        return list(self._selected.get("species_summary") or [])

    @Property(list, notify=selectedChanged)
    def selectedPairs(self):
        return list(self._selected.get("pairs") or [])

    @Property(int, notify=selectedChanged)
    def selectedFrameOffset(self):
        """Décalage de synchro figé, ou -999999 s'il n'y en a pas encore."""
        value = self._selected.get("frame_offset")
        return int(value) if value is not None else -999999

    @Property(str, notify=selectedChanged)
    def selectedCalibration(self):
        profile = str(self._selected.get("calibration_profile", ""))
        sha = str(self._selected.get("calibration_sha256", ""))
        if not profile:
            return ""
        return f"{profile} · {sha[:8]}" if sha else profile

    # ── Session active ───────────────────────────────────────────────
    #
    # La session etait auparavant *deduite* de la video chargee
    # (media -> session). On ne pouvait donc pas choisir une session et
    # s'attendre a ce que l'application suive : cliquer une ligne ne
    # remplissait qu'un panneau de detail, et le volet Mesure continuait
    # d'afficher « Pas encore de session pour cette paire de videos ».

    @Property(bool, notify=activeSessionChanged)
    def hasActiveSession(self):
        return bool(self.selectedId)

    @Property(str, notify=activeSessionChanged)
    def activeSessionName(self):
        return self.selectedName

    @Property(bool, notify=activeSessionChanged)
    def activePairLoaded(self):
        """La paire chargée appartient-elle à l'une des prises de la session ?"""
        if not self.selectedId or not self.selectedHasPair:
            return False
        loaded = self._normalize(self._measure.leftVideo)
        if not loaded:
            return False
        return any(
            loaded == self._normalize(str(pair.get("left_path", "")))
            for pair in (self._selected.get("pairs") or [])
        ) or loaded == self._normalize(self.selectedLeftPath)

    @Property(bool, notify=activeSessionChanged)
    def activeSessionHasPair(self):
        """La session active a-t-elle une paire de videos attachee ?"""
        return bool(self.selectedId) and self.selectedHasPair

    @Property(str, notify=activeSessionChanged)
    def activeSessionHint(self):
        """Ce qu'il reste a faire, en clair. Ne sert qu'a l'affichage :
        l'interface se decide sur activeSessionHasPair / activePairLoaded."""
        if not self.selectedId:
            return ""
        if not self.selectedHasPair:
            return "sans paire de vidéos"
        if not self.activePairLoaded:
            return "autre paire chargée"
        return ""

    @staticmethod
    def _normalize(path: str) -> str:
        if not path:
            return ""
        try:
            return str(Path(path).resolve())
        except OSError:
            return path

    def _owner_of_loaded_video(self) -> dict | None:
        """Session qui porte déjà la vidéo gauche chargée, ou None."""
        import fish_annotate as fa

        self._ensure_fv()
        if self._data is not None:
            media_id = self._data.mediaId
        else:
            media_id = fa.resolve_media_id(self._measure.leftVideo) or ""
        return fa.find_session_for_media(media_id) if media_id else None

    def _follow_session(self, owner: dict) -> None:
        """Rend active la session qui porte déjà les vidéos chargées."""
        owner_id = str(owner.get("session_id", ""))
        if owner_id and owner_id != self.selectedId:
            self._select_by_id(owner_id)
            self._set_status(
                f"Session « {owner.get('name', '')} » ouverte (vidéos déjà rattachées)"
            )

    def _schedule_auto_attach(self):
        """La session suit la vidéo, sinon la vidéo rejoint la session."""
        if self._auto_attach_scheduled:
            return
        self._auto_attach_scheduled = True

        def run():
            self._auto_attach_scheduled = False
            left = self._measure.leftVideo
            right = self._measure.rightVideo
            if not left or self._busy or not self._db_ok:
                return
            if right and Path(left).is_file() and Path(right).is_file():
                self.attachCurrentPair()
                return
            # Demi-paire : rien n'est attaché, la session suit la vidéo gauche.
            try:
                owner = self._owner_of_loaded_video()
            except Exception as exc:
                self._logs.append(f"[!] Sessions : {exc}")
                return
            if owner:
                self._follow_session(owner)

        # Les signaux gauche puis droite sont émis pendant un même chargement :
        # attendre le prochain tour évite d'attacher une demi-paire.
        QTimer.singleShot(0, run)

    @Property(str, constant=True)
    def databasePath(self):
        return str(paths.annotations_db_path())

    @Property(str, constant=True)
    def exportsPath(self):
        return str(paths.exports_dir())

    # ── formulaire « Nouvelle session » ────────────────────────────────

    @Property(str, notify=formChanged)
    def formName(self):
        return self._form_name

    @formName.setter
    def formName(self, value: str):
        if self._form_name != value:
            self._form_name = value
            self.formChanged.emit()

    @Property(str, notify=formChanged)
    def formSite(self):
        return self._form_site

    @formSite.setter
    def formSite(self, value: str):
        if self._form_site != value:
            self._form_site = value
            self.formChanged.emit()

    @Property(str, notify=formChanged)
    def formDate(self):
        return self._form_date

    @formDate.setter
    def formDate(self, value: str):
        if self._form_date != value:
            self._form_date = value
            self.formChanged.emit()

    @Property(str, notify=formChanged)
    def formNotes(self):
        return self._form_notes

    @formNotes.setter
    def formNotes(self, value: str):
        if self._form_notes != value:
            self._form_notes = value
            self.formChanged.emit()

    @Property(bool, notify=formChanged)
    def formValid(self):
        """Site et date sont obligatoires - règle produit, dite dans le formulaire."""
        return bool(self._form_site.strip()) and bool(self._form_date.strip())

    @Property(str, notify=formChanged)
    def formHint(self):
        if not self._form_site.strip():
            return "Indiquez le lieu : sans lui, les observations ne sont pas exploitables."
        if not self._form_date.strip():
            return "Indiquez la date de la sortie (AAAA-MM-JJ)."
        return ""

    # ── actions ────────────────────────────────────────────────────────

    @Slot()
    def refresh(self):
        if not self._db_ok:
            self._db_ok = self._check_db()
        if not self._db_ok:
            self._sessions.set_rows([])
            self.sessionsChanged.emit()
            self._set_status("Base d'annotations indisponible")
            return
        try:
            import fish_annotate as fa

            self._ensure_fv()
            rows = fa.list_sessions_v2()
            self._sessions.set_rows(rows)
            self.sessionsChanged.emit()
            self.moveTargetsChanged.emit()
            if self.selectedId:
                idx = self._sessions.index_of(self.selectedId)
                if idx >= 0:
                    self.selectSession(idx)
                else:
                    self._clear_selection()
            planned = sum(1 for r in rows if r.get("status") == "planned")
            self._set_status(
                f"{len(rows)} session(s) · {planned} à venir"
                if rows
                else "Aucune session - créez-en une avec « Nouvelle session »"
            )
        except Exception as exc:
            self._logs.append(f"[!] Sessions : {exc}")
            self._set_status(str(exc))

    def _clear_selection(self) -> None:
        self._selected = {}
        self._selected_row = -1
        self.selectedChanged.emit()

    @Slot(int)
    def selectSession(self, row: int):
        data = self._sessions.row_at(row)
        if not data:
            return
        changed = str(data.get("session_id", "")) != self.selectedId
        self._selected = dict(data)
        self._selected_row = row
        self.selectedChanged.emit()
        self.moveTargetsChanged.emit()
        self._remember_session(self.selectedId)
        if self._data is not None:
            self._data._apply_session_row(data)
        # Choisir une session la rend active dans toute l'application.
        # Le chargement de la paire reste sur « Ouvrir » : rouvrir des
        # fichiers de plusieurs Go a chaque clic dans la liste serait
        # insupportable.
        self.activeSessionChanged.emit()
        if changed:
            # Une paire libre déjà chargée rejoint la session choisie ; une
            # paire rangée ailleurs ne ramène pas la sélection vers elle.
            QTimer.singleShot(0, self, lambda: self._join_loaded(follow=False))

    def _select_by_id(self, session_id: str) -> bool:
        idx = self._sessions.index_of(session_id)
        if idx < 0:
            self.refresh()
            idx = self._sessions.index_of(session_id)
        if idx >= 0:
            self.selectSession(idx)
        return idx >= 0

    def _remember_session(self, session_id: str) -> None:
        """Mémorise la session active, ou l'oublie si `session_id` est vide."""
        path = paths.cam_param(ACTIVE_SESSION_FILE)
        try:
            if session_id:
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_text(json.dumps({"session_id": session_id}), encoding="utf-8")
            else:
                path.unlink(missing_ok=True)
        except OSError as exc:
            self._logs.append(f"[!] Session mémorisée : {exc}")

    @Slot()
    def restoreLastSession(self):
        """Rouvre la session active du lancement précédent si elle existe encore."""
        try:
            saved = json.loads(
                paths.cam_param(ACTIVE_SESSION_FILE).read_text(encoding="utf-8")
            )
            session_id = str(saved.get("session_id", ""))
        except (OSError, ValueError, AttributeError):
            return
        self.refresh()
        idx = self._sessions.index_of(session_id) if session_id else -1
        if idx >= 0:
            self.selectSession(idx)

    @Slot()
    def createSession(self):
        """Crée une session planifiée. Site et date sont exigés à l'écriture."""
        if not self._db_ok:
            self._set_status("Base d'annotations indisponible")
            return
        try:
            import fish_annotate as fa

            self._ensure_fv()
            created = fa.create_session(
                name=self._form_name,
                site=self._form_site,
                session_date=self._form_date,
                notes=self._form_notes,
            )
            self._form_name = ""
            self._form_notes = ""
            self.formChanged.emit()
            self.refresh()
            idx = self._sessions.index_of(str(created.get("session_id", "")))
            if idx >= 0:
                self.selectSession(idx)
            # « Nouvelle session » signifie un nouveau contexte visible. Les
            # anciennes données restent en base mais ne restent pas affichées.
            self._measure.resetForNewSession()
            if self._data is not None:
                self._data.resetForNewSession()
                self._data._apply_session_row(created)
            self._remember_session(str(created.get("session_id", "")))
            self._set_status(
                f"Session « {created.get('name', '')} » créée - chargez sa première vidéo"
            )
            self._logs.append(self._status)
        except ValueError as exc:
            self._set_status(str(exc))
        except Exception as exc:
            self._set_status(str(exc))
            self._logs.append(f"[!] Création session : {exc}")

    @Slot()
    def attachCurrentPair(self):
        """Range la paire chargée : sa session devient active, sinon elle
        rejoint la session active (voir `fa.join_loaded_pair`).

        Le travail part dans un **thread** : l'attache calcule l'empreinte
        sha256 intégrale des deux vidéos (plusieurs Go la première fois) et
        gelait l'interface. Les boutons concernés sont grisés via `busy`.
        """
        self._join_loaded(follow=True)

    def _join_loaded(self, follow: bool) -> None:
        left = self._measure.leftVideo
        right = self._measure.rightVideo
        if not left or not right:
            if follow:
                self._set_status(
                    "Chargez d'abord les deux vidéos (page Mesure ou menu Fichier)"
                )
            return
        if not self._db_ok or not (Path(left).is_file() and Path(right).is_file()):
            return
        if self._busy:
            self._set_status("Attache déjà en cours…")
            return
        payload = {
            "session_id": self.selectedId,
            "left": left,
            "right": right,
            "stereo_rmse": paths.stereo_rmse_if_exists(),
        }
        self._set_busy(True)

        def worker():
            try:
                import fish_annotate as fa

                self._ensure_fv()
                result = fa.join_loaded_pair(
                    payload["session_id"], payload["left"], payload["right"],
                    stereo_rmse=payload["stereo_rmse"],
                )
                self._pairAttached.emit(dict(result, follow=follow))
            except Exception as exc:
                self._pairFailed.emit(str(exc))

        threading.Thread(target=worker, daemon=True).start()

    def _finish_pair_attached(self, result: dict):
        self._set_busy(False)
        action = result.get("action")
        row = result.get("session") or {}
        if action == "conflict":
            self._set_status("Ces deux vidéos sont dans deux prises différentes")
            return
        if action == "open":
            if result.get("follow", True):
                self._follow_session(row)
            return
        if not row:
            return
        self.refresh()
        self._select_by_id(str(row.get("session_id", "")))
        if self._data is not None:
            # L'offset figé change la lecture des lignes historiques :
            # le contrôleur de données doit relire sa session courante.
            self._data.loadSessionMetadata()
            # Les médias reçoivent le lieu et la date de la session.
            self._data.saveSessionFields()
        self.activeSessionChanged.emit()
        offset = row.get("frame_offset")
        detail = f" · décalage figé à {offset:+d} img" if offset is not None else ""
        calib = row.get("calibration_profile") or ""
        if calib:
            detail += f" · calibration « {calib} » tracée"
        if action == "replaced":
            self._set_status(f"Vidéo remplacée dans la prise{detail}")
        else:
            count = int(row.get("pair_count", 0) or 0)
            self._set_status(f"Vidéo ajoutée - {count} prise(s) dans la session{detail}")
        self._logs.append(self._status)

    @Slot(str, str)
    def refreezePairOffset(self, left: str, right: str):
        """Nouvelle synchro appliquée : la prise de cette paire la reprend."""
        if not left or not right or not self._db_ok:
            return
        try:
            import fish_annotate as fa

            self._ensure_fv()
            if fa.refreeze_pair_offset(left, right):
                self.refresh()
                if self._data is not None:
                    # L'offset figé vient de changer : le relire au besoin.
                    self._data._frame_offset_cached = False
        except Exception as exc:
            self._logs.append(f"[!] Synchro de la prise : {exc}")

    def _finish_pair_failed(self, msg: str):
        self._set_busy(False)
        self._set_status(msg)
        self._logs.append(f"[!] Attache paire : {msg}")

    @Slot()
    def openSelectedSession(self):
        """Rouvre la session : charge la paire, le registre, puis va à Mesure."""
        if not self._selected:
            self._set_status("Choisissez une session dans la liste")
            return
        left = self.selectedLeftPath
        right = self.selectedRightPath
        name = self.selectedName
        if not left:
            self._set_status(
                f"« {name} » n'a pas encore de vidéos : chargez-les dans Mesure"
            )
            return
        if not self.selectedLeftAvailable:
            self._set_status(
                f"Vidéo introuvable sur ce disque : {left} - rebranchez l'archive "
                "(l'application la retrouvera par son empreinte)"
            )
            return
        if not right:
            self._set_status(
                "Cette prise n'a pas de vidéo droite : rattachez sa paire "
                "stéréo avant de l'ouvrir dans Mesure"
            )
            return
        if right and not self.selectedRightAvailable:
            self._set_status(
                f"Vidéo droite introuvable : {right} - retrouvez-la depuis "
                "l'onglet Fin de session avant de rouvrir cette prise stéréo"
            )
            return
        try:
            self._measure.refresh(left, right)
            if self._data is not None:
                self._data.loadSessionMetadata()
                self._data.refreshRegistry()
                self._data.refreshGrazing()
                self._data.refreshFrameAbundance()
                self._data.loadEventTypes()
            if self._app is not None:
                self._app.currentPage = 4
            self._set_status(f"Session « {name} » ouverte - page Mesure")
            self._logs.append(self._status)
        except Exception as exc:
            self._set_status(str(exc))
            self._logs.append(f"[!] Ouverture session : {exc}")

    @Slot(int)
    def openPairAt(self, index: int):
        """Charge une prise précise d'une session qui en contient plusieurs."""
        pairs = list(self._selected.get("pairs") or [])
        if not 0 <= int(index) < len(pairs):
            return
        pair = pairs[int(index)]
        left = str(pair.get("left_path") or "")
        right = str(pair.get("right_path") or "")
        if not pair.get("left_available"):
            self._set_status(
                f"Vidéo introuvable : {left or (pair.get('left') or {}).get('name', '')}"
            )
            return
        if not right:
            self._set_status(
                f"Prise {int(index) + 1} incomplète : vidéo droite absente"
            )
            return
        if right and not pair.get("right_available"):
            self._set_status(f"Vidéo droite introuvable : {right}")
            return
        try:
            self._measure.refresh(left, right)
            if self._data is not None:
                self._data.loadSessionMetadata()
                self._data.refreshRegistry()
                self._data.refreshGrazing()
                self._data.refreshFrameAbundance()
            if self._app is not None:
                self._app.currentPage = 4
            self._set_status(
                f"Session « {self.selectedName} » · prise {int(index) + 1} ouverte"
            )
        except Exception as exc:
            self._set_status(str(exc))
            self._logs.append(f"[!] Ouverture prise : {exc}")

    @Slot(int)
    def openSessionAt(self, row: int):
        self.selectSession(row)
        self.openSelectedSession()

    @Slot()
    def deleteSelectedSession(self):
        """Supprime une session planifiée créée par erreur (jamais ses données)."""
        if not self.selectedId:
            return
        try:
            import fish_annotate as fa

            self._ensure_fv()
            if fa.delete_session(self.selectedId):
                self._clear_selection()
                self._remember_session("")
                if self._data is not None:
                    self._data._set_session_id("")
                self.activeSessionChanged.emit()
                self.refresh()
                self._set_status("Session supprimée")
            else:
                self._set_status("Session introuvable")
        except ValueError as exc:
            self._set_status(str(exc))
        except Exception as exc:
            self._set_status(str(exc))
            self._logs.append(f"[!] Suppression session : {exc}")

    @Property(list, notify=moveTargetsChanged)
    def moveTargets(self):
        """Sessions pouvant recevoir une prise de la session choisie."""
        rows = (self._sessions.row_at(i) or {} for i in range(self._sessions.rowCount()))
        return [
            {"session_id": row.get("session_id", ""), "name": row.get("name", "")}
            for row in rows
            if row.get("session_id") != self.selectedId
        ]

    @Slot(int, str)
    def movePairTo(self, pair_index: int, session_id: str):
        """Déplace une prise de la session choisie vers `session_id`."""
        pairs = list(self._selected.get("pairs") or [])
        if not 0 <= int(pair_index) < len(pairs) or not session_id:
            return
        try:
            import fish_annotate as fa

            self._ensure_fv()
            moved = pairs[int(pair_index)]
            target = fa.move_media_pair(str(moved["pair_id"]), session_id)
            if not target:
                self._set_status("Session introuvable")
                return
            self.refresh()
            loaded = self._data.mediaId if self._data is not None else ""
            if loaded and loaded in (moved.get("left_media_id"), moved.get("right_media_id")):
                # La session suit la vidéo chargée.
                self._select_by_id(session_id)
            self._set_status(
                f"Prise {int(pair_index) + 1} déplacée vers « {target.get('name', '')} »"
            )
            self._logs.append(self._status)
        except Exception as exc:
            self._set_status(str(exc))
            self._logs.append(f"[!] Déplacement prise : {exc}")

    @Slot(str)
    def setSelectedStatus(self, status: str):
        """Passe la session à « terminée » / « exportée » / « en cours »."""
        if not self.selectedId:
            return
        try:
            import fish_annotate as fa

            self._ensure_fv()
            fa.update_session(self.selectedId, status=status)
            self.refresh()
            self._set_status(f"Statut mis à jour : {status}")
        except Exception as exc:
            self._set_status(str(exc))

    @Slot()
    def openSelectedFolder(self):
        """Ouvre l'explorateur sur le dossier de la vidéo gauche."""
        target = self.selectedLeftPath
        folder = Path(target).parent if target else Path(self.databasePath).parent
        if folder.is_dir():
            os.startfile(str(folder))
        else:
            self._set_status(f"Dossier introuvable : {folder}")

    @Slot()
    def openDatabaseFolder(self):
        folder = Path(self.databasePath).parent
        if folder.is_dir():
            os.startfile(str(folder))
