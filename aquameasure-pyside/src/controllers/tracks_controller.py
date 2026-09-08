"""Validation des pistes de suivi - le prerequis qualite des exports.

Le tracker produit beaucoup de bruit et rien, jusqu'ici, ne permettait de le
corriger : sur la base reelle, 246 pistes ne comptent qu'une seule frame (faux
positifs probables), 23 s'etendent sur plus de 7000 frames (fusions
d'identites probables) et 61 % ont des trous. Les fonctions de correction
existaient depuis longtemps dans `fish-vision/src/annodb/tracks.py`
(`split_track`, `merge_tracks`, `set_keyframe`, `delete_samples_range`) sans
aucun bouton pour les appeler.

Ce controleur ne reecrit **aucune** logique d'edition : il lit
`tracks.track_overview` et appelle les facades existantes, en rafraichissant
les bornes (`refresh_track_bounds`) apres chaque modification.
"""

from __future__ import annotations

import sys
from pathlib import Path

from PySide6.QtCore import Property, QObject, Signal, Slot

from src.controllers.measure_controller import MeasureController
from src.models.tracks_list_model import TracksListModel
from src.util import paths
from src.util.log_model import LogModel


class TracksController(QObject):
    tracksChanged = Signal()
    selectionChanged = Signal()
    statusTextChanged = Signal()
    busyChanged = Signal()
    thresholdsChanged = Signal()

    def __init__(
        self,
        measure: MeasureController,
        data: QObject,
        fish: QObject | None = None,
        parent=None,
    ):
        super().__init__(parent)
        self._measure = measure
        self._data = data
        self._fish = fish
        self._logs = LogModel(self)
        self._tracks = TracksListModel(self)
        self._selected = ""
        self._compare = ""
        self._status = ""
        self._busy = False
        self._gap_threshold = 30
        self._long_frames = 1800
        self._stats: dict = {}

    # ── Acces au paquet fish-vision ────────────────────────────────────

    def _ensure_fv(self) -> None:
        root = paths.app_root()
        for candidate in (root / "fish-vision" / "src", root / "fish-vision", root):
            if candidate.is_dir() and str(candidate) not in sys.path:
                sys.path.insert(0, str(candidate))

    def _db_path(self) -> Path | None:
        try:
            self._ensure_fv()
            from src.annodb.connection import get_db_path

            path = get_db_path()
            return path if path.is_file() else None
        except Exception as exc:  # base absente, fish-vision non installe
            self._logs.append(f"[!] Pistes : base indisponible ({exc})")
            return None

    # ── Proprietes exposees a QML ──────────────────────────────────────

    @Property(QObject, constant=True)
    def logs(self):
        return self._logs

    @Property(QObject, constant=True)
    def tracks(self):
        return self._tracks

    @Property(int, notify=tracksChanged)
    def count(self):
        return self._tracks.rowCount()

    @Property(int, notify=tracksChanged)
    def suspiciousCount(self):
        """Pistes portant au moins un drapeau - celles a relire en priorite."""
        return int(self._stats.get("suspicious", 0))

    @Property(int, notify=tracksChanged)
    def singleFrameCount(self):
        return int(self._stats.get("single_frame", 0))

    @Property(int, notify=tracksChanged)
    def veryLongCount(self):
        return int(self._stats.get("very_long", 0))

    @Property(int, notify=tracksChanged)
    def gapCount(self):
        return int(self._stats.get("has_gaps", 0))

    @Property(int, notify=tracksChanged)
    def sampleCount(self):
        return int(self._stats.get("samples", 0))

    @Property(str, notify=selectionChanged)
    def selectedTrackId(self):
        return self._selected

    @Property(str, notify=selectionChanged)
    def compareTrackId(self):
        """Deuxieme piste retenue - la fusion en demande exactement deux."""
        return self._compare

    @Property(str, notify=selectionChanged)
    def selectedLabel(self):
        row = self._row(self._selected)
        if not row:
            return ""
        return f"Piste #{row['external_track_id']}"

    @Property(str, notify=selectionChanged)
    def compareLabel(self):
        row = self._row(self._compare)
        if not row:
            return ""
        return f"Piste #{row['external_track_id']}"

    @Property(bool, notify=selectionChanged)
    def canMerge(self):
        return bool(self._selected) and bool(self._compare) and self._selected != self._compare

    @Property(str, notify=statusTextChanged)
    def statusText(self):
        return self._status

    @Property(bool, notify=busyChanged)
    def busy(self):
        return self._busy

    @Property(int, notify=thresholdsChanged)
    def gapThreshold(self):
        return self._gap_threshold

    @gapThreshold.setter
    def gapThreshold(self, value: int):
        value = max(2, int(value))
        if value != self._gap_threshold:
            self._gap_threshold = value
            self.thresholdsChanged.emit()
            self.refresh()

    @Property(int, notify=thresholdsChanged)
    def longTrackFrames(self):
        return self._long_frames

    @longTrackFrames.setter
    def longTrackFrames(self, value: int):
        value = max(30, int(value))
        if value != self._long_frames:
            self._long_frames = value
            self.thresholdsChanged.emit()
            self.refresh()

    @Property(str, notify=tracksChanged)
    def mediaId(self):
        return str(getattr(self._data, "mediaId", "") or "")

    # ── Lecture ────────────────────────────────────────────────────────

    def _row(self, track_id: str) -> dict | None:
        index = self._tracks.index_of(track_id)
        return self._tracks.row_at(index) if index >= 0 else None

    def _set_status(self, message: str) -> None:
        if self._status != message:
            self._status = message
            self.statusTextChanged.emit()

    def _set_busy(self, value: bool) -> None:
        if self._busy != value:
            self._busy = bool(value)
            self.busyChanged.emit()

    @Slot()
    def refresh(self):
        """Relit les pistes du media courant, triees par suspicion."""
        media_id = self.mediaId
        if not media_id:
            self._tracks.set_rows([])
            self._stats = {}
            self.tracksChanged.emit()
            self._set_status(
                "Aucune session enregistree : ouvrez une paire de videos et "
                "enregistrez la session pour voir ses pistes."
            )
            return
        db_path = self._db_path()
        if db_path is None:
            self._tracks.set_rows([])
            self._stats = {}
            self.tracksChanged.emit()
            self._set_status("Base d'annotations indisponible.")
            return
        try:
            from src.annodb.connection import session_scope
            from src.annodb.tracks import track_overview

            with session_scope(db_path) as session:
                rows = track_overview(
                    session, media_id,
                    gap_threshold=self._gap_threshold,
                    long_track_frames=self._long_frames,
                )
        except Exception as exc:
            self._logs.append(f"[!] Lecture des pistes : {exc}")
            self._set_status(f"Lecture des pistes impossible : {exc}")
            return

        self._tracks.set_rows(rows)
        self._stats = {
            "suspicious": sum(
                1 for r in rows
                if r["single_frame"] or r["very_long"] or r["has_gaps"]
            ),
            "single_frame": sum(1 for r in rows if r["single_frame"]),
            "very_long": sum(1 for r in rows if r["very_long"]),
            "has_gaps": sum(1 for r in rows if r["has_gaps"]),
            "samples": sum(r["sample_count"] for r in rows),
        }
        if self._selected and self._tracks.index_of(self._selected) < 0:
            self._selected = ""
        if self._compare and self._tracks.index_of(self._compare) < 0:
            self._compare = ""
        self.tracksChanged.emit()
        self.selectionChanged.emit()
        self._set_status(
            f"{len(rows)} piste(s) · {self._stats['suspicious']} a verifier "
            f"({self._stats['single_frame']} d'une frame, "
            f"{self._stats['very_long']} tres longues, "
            f"{self._stats['has_gaps']} avec trous)"
        )

    # ── Selection ──────────────────────────────────────────────────────

    @Slot(int)
    def selectRow(self, row: int):
        data = self._tracks.row_at(row)
        if not data:
            return
        self._selected = data["track_id"]
        self.selectionChanged.emit()

    @Slot(int)
    def toggleCompareRow(self, row: int):
        """Retient (ou relache) la deuxieme piste d'une fusion."""
        data = self._tracks.row_at(row)
        if not data:
            return
        track_id = data["track_id"]
        self._compare = "" if self._compare == track_id else track_id
        self.selectionChanged.emit()

    @Slot()
    def clearSelection(self):
        self._selected = ""
        self._compare = ""
        self.selectionChanged.emit()

    # ── Edition - toujours via les facades de tracks.py ────────────────

    def _apply(self, action) -> bool:
        db_path = self._db_path()
        if db_path is None:
            self._set_status("Base d'annotations indisponible.")
            return False
        self._set_busy(True)
        try:
            from src.annodb.connection import session_scope

            with session_scope(db_path) as session:
                action(session)
        except Exception as exc:
            self._logs.append(f"[!] Edition de piste : {exc}")
            self._set_status(f"Modification impossible : {exc}")
            return False
        finally:
            self._set_busy(False)
        return True

    @Slot(bool)
    def deleteSelected(self, force: bool = False):
        """Supprime la piste et ses echantillons (facade `tracks.delete_track`)."""
        track_id = self._selected
        if not track_id:
            self._set_status("Selectionnez d'abord une piste.")
            return
        label = self.selectedLabel
        outcome: dict = {}

        def run(session):
            from src.annodb.tracks import delete_track

            outcome.update(delete_track(session, track_id, force=bool(force)))

        if not self._apply(run):
            return
        if not outcome.get("deleted"):
            self._set_status(f"{label} conservee - {outcome.get('reason', '')}")
            return
        self._logs.append(
            f"{label} supprimee : {outcome.get('samples', 0)} echantillon(s), "
            f"{outcome.get('annotations_detached', 0)} observation(s) detachee(s)"
        )
        self._selected = ""
        self.refresh()
        self._set_status(
            f"{label} supprimee ({outcome.get('samples', 0)} echantillon(s) ; "
            f"{outcome.get('annotations_detached', 0)} observation(s) conservee(s), "
            "detachee(s) de la piste)"
        )

    @Slot()
    def mergeSelected(self):
        """Recolle la piste retenue sur la piste selectionnee (`merge_tracks`)."""
        if not self.canMerge:
            self._set_status(
                "La fusion demande deux pistes : selectionnez-en une, puis "
                "cochez la seconde."
            )
            return
        source, target = self._compare, self._selected
        labels = (self.compareLabel, self.selectedLabel)
        moved = {"count": 0}

        def run(session):
            from src.annodb.tracks import merge_tracks, refresh_track_bounds

            moved["count"] = merge_tracks(session, source, target)
            refresh_track_bounds(session, target)

        if not self._apply(run):
            return
        self._compare = ""
        self.refresh()
        self._set_status(
            f"{labels[0]} fusionnee dans {labels[1]} - "
            f"{moved['count']} position(s) deplacee(s)"
        )

    @Slot()
    def splitSelectedAtCurrentFrame(self):
        """Coupe la piste a la frame affichee (`split_track`).

        La coupure porte sur l'index **absolu** du fichier video : c'est la
        convention de `track_samples`, et la timeline affichee est decalee de
        l'offset de synchronisation.
        """
        track_id = self._selected
        if not track_id:
            self._set_status("Selectionnez d'abord une piste.")
            return
        frame = int(self._measure.leftAbsFrameAt(self._measure.frameIndex))
        label = self.selectedLabel
        created = {"id": None}

        def run(session):
            from src.annodb.tracks import refresh_track_bounds, split_track

            new_track = split_track(session, track_id, frame)
            if new_track is not None:
                created["id"] = new_track.id
                created["external"] = new_track.external_track_id
                refresh_track_bounds(session, track_id)
                refresh_track_bounds(session, new_track.id)

        if not self._apply(run):
            return
        if created["id"] is None:
            self._set_status(
                f"{label} inchangee : aucune position a partir de la frame "
                f"{frame} (placez-vous plus tot dans la video)."
            )
            return
        self.refresh()
        self._set_status(
            f"{label} coupee a la frame {frame} - nouvelle piste "
            f"#{created.get('external')}"
        )

    @Slot()
    def viewSelected(self):
        """Ramene la lecture sur la piste et l'entoure (mecanisme de focus existant)."""
        track_id = self._selected
        if not track_id:
            self._set_status("Selectionnez d'abord une piste.")
            return
        row = self._row(track_id)
        if not row:
            return
        db_path = self._db_path()
        if db_path is None:
            self._set_status("Base d'annotations indisponible.")
            return
        try:
            import json

            from src.annodb.connection import session_scope
            from src.annodb.tracks import list_track_samples

            with session_scope(db_path) as session:
                samples = list_track_samples(session, track_id)
                rows = [
                    (int(s.frame_index), json.loads(s.bbox_json)) for s in samples
                ]
        except Exception as exc:
            self._logs.append(f"[!] Lecture de la piste : {exc}")
            self._set_status(f"Lecture impossible : {exc}")
            return
        if not rows:
            self._set_status(f"{self.selectedLabel} n'a aucune position enregistree.")
            return

        current_abs = int(self._measure.leftAbsFrameAt(self._measure.frameIndex))
        # La position la plus proche de l'image affichee : sur une piste de
        # milliers de frames, revenir systematiquement au debut ferait perdre
        # le contexte que l'operateur vient d'atteindre.
        frame, box = min(rows, key=lambda item: abs(item[0] - current_abs))

        if self._measure.frameCount <= 0:
            self._set_status(
                f"{self.selectedLabel} - frame {frame} · chargez les videos "
                "pour l'afficher"
            )
            return
        if self._measure.playing:
            self._measure.togglePlay()
        timeline_index = int(self._measure.alignedIndexFromLeftAbs(frame))
        self._measure.frameIndex = timeline_index
        reached = self._measure.frameIndex
        if self._fish is not None:
            self._fish.focusAnnotationBox(
                track_id, reached,
                float(box.get("x_min", 0.0)), float(box.get("y_min", 0.0)),
                float(box.get("x_max", 0.0)), float(box.get("y_max", 0.0)),
                self.selectedLabel,
            )
        reached_abs = int(self._measure.leftAbsFrameAt(reached))
        if reached_abs != frame:
            self._set_status(
                f"{self.selectedLabel} - frame {frame} hors timeline, "
                f"affichee a {reached_abs}"
            )
            return
        self._set_status(
            f"{self.selectedLabel} - frame {frame} "
            f"({row['sample_count']} position(s), couverture "
            f"{row['coverage'] * 100:.0f} %)"
        )
