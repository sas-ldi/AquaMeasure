"""Marqueurs ponctuels (« bouchees ») poses un par un sur une piste suivie.

Le probleme vecu : l'intervalle de broutage dit « ce poisson a broute de f1200
a f1500 », mais rien ne disait combien de coups de bouche il a donnes dedans.
Le dataset d'annotation vise justement le comptage des bouchees, et il n'y
avait aucun geste pour revenir sur une piste deja suivie et marquer les
instants un par un.

Trois choix structurants, tous imposes par ce qui existe deja :

- **Ecriture** : `fish_annotate.add_behavior_point`, la fonction soeur de
  `add_grazing_interval`. Cette derniere refuse explicitement les types de
  portee ponctuelle ; la detourner aurait ecrit une bouchee comme un intervalle
  d'une frame, sans jamais verifier la portee du type.
- **Pas** `spatial_behavior_flags` : ce drapeau est porte par une observation
  enregistree, pas par une frame de piste, donc il ne sait pas dire « ce
  poisson, a cette image ».
- **La bbox affichee vient d'ici, pas de l'overlay de detection**. A l'arret,
  `FishController` publie les boites du detecteur, pas les echantillons de la
  piste : sans cette lecture, la piste selectionnee n'aurait aucune boite a
  cliquer sur la plupart des images.
"""

from __future__ import annotations

import json
import sys
from bisect import bisect_left
from pathlib import Path
from typing import Optional

from PySide6.QtCore import Property, QObject, Signal, Slot

from src.util import paths
from src.util.log_model import LogModel

# Au-dela de cet ecart entre deux echantillons voisins, on refuse de deduire
# une boite : un trou de suivi d'une seconde ne doit pas devenir une position
# inventee sous le curseur de l'annotateur.
MAX_GAP_FRAMES = 30


class PeckController(QObject):
    typesChanged = Signal()
    tracksChanged = Signal()
    selectionChanged = Signal()
    markersChanged = Signal()
    statusTextChanged = Signal()
    # L'image affichee change beaucoup plus souvent que la selection : la cible
    # cliquable et « deja marquee ? » se relisent sur ce signal-la, pour ne pas
    # forcer QML a tout reevaluer a chaque frame de lecture.
    frameChanged = Signal()
    # « Quand il y a une bouchee, ca fait un petit pop du logo sur la frame ou
    # on a vu la bouchee. » Un signal, pas une propriete relue image par
    # image : l'animation doit partir sur l'evenement, une seule fois, et la
    # lecture saute des images - c'est le franchissement qu'on detecte, pas
    # l'egalite avec l'image courante.
    # x, y (espace de l'image affichee), symbole, couleur, libelle.
    peckPopped = Signal(float, float, str, str, str)

    def __init__(self, measure: QObject, data: QObject, fish: QObject | None = None,
                 parent=None):
        super().__init__(parent)
        self._measure = measure
        self._data = data
        self._fish = fish
        self._logs = LogModel(self)

        self._types: list[dict] = []
        self._type_key = ""
        self._tracks: list[dict] = []
        self._selected = ""
        self._markers: list[dict] = []
        self._video_marker_count = 0
        self._status = ""

        # Echantillons de la piste selectionnee : (frames triees, boites).
        # Relus une seule fois par selection, puis interroges par dichotomie a
        # chaque image — la lecture SQLite par frame etait exclue.
        self._sample_frames: list[int] = []
        self._sample_boxes: list[dict] = []
        # Les memes boites, recalees sur l'image brute affichee en lecture.
        # Converties une fois par selection de piste, pas image par image.
        self._sample_boxes_raw: list[dict] = []
        self._raw_token: int | None = None
        # Derniere image absolue examinee pour le pop. La lecture saute des
        # images : on regarde l'intervalle franchi, pas l'image courante.
        self._last_pop_frame = -1

        measure.frameIndexChanged.connect(self.frameChanged)
        measure.frameIndexChanged.connect(self._on_frame_advanced)
        measure.leftVideoChanged.connect(self._on_video_changed)
        if hasattr(measure, "overlaySpaceChanged"):
            measure.overlaySpaceChanged.connect(self.frameChanged)

    # ── Acces au paquet annotations ────────────────────────────────────

    def _ensure_fv(self) -> None:
        root = paths.app_root()
        for candidate in (root / "annotations" / "src", root / "annotations", root):
            if candidate.is_dir() and str(candidate) not in sys.path:
                sys.path.insert(0, str(candidate))

    def _db_path(self) -> Optional[Path]:
        try:
            self._ensure_fv()
            from src.annodb.connection import get_db_path

            path = get_db_path()
            return path if path.is_file() else None
        except Exception as exc:
            self._logs.append(f"[!] Bouchees : base indisponible ({exc})")
            return None

    def _video_path(self) -> str:
        return str(getattr(self._measure, "leftVideo", "") or "")

    def _set_status(self, message: str) -> None:
        if self._status != message:
            self._status = message
            self.statusTextChanged.emit()

    def _on_video_changed(self) -> None:
        self._selected = ""
        self._sample_frames = []
        self._sample_boxes = []
        self._sample_boxes_raw = []
        self._raw_token = None
        self._last_pop_frame = -1
        self._markers = []
        self._tracks = []
        self._video_marker_count = 0
        self.tracksChanged.emit()
        self.markersChanged.emit()
        self.selectionChanged.emit()
        self.frameChanged.emit()

    # ── Catalogue des types ponctuels ──────────────────────────────────

    @Property(QObject, constant=True)
    def logs(self):
        return self._logs

    @Property(list, notify=typesChanged)
    def pointTypes(self):
        """Types de portee ponctuelle actifs, dans l'ordre du catalogue."""
        return list(self._types)

    @Property(bool, notify=typesChanged)
    def hasPointType(self):
        return bool(self._types)

    @Property(str, notify=typesChanged)
    def typeKey(self):
        return self._current_type().get("key", "")

    @Property(str, notify=typesChanged)
    def typeLabel(self):
        return self._current_type().get("label", "")

    @Property(str, notify=typesChanged)
    def typeSymbol(self):
        return self._current_type().get("symbol", "●") or "●"

    @Property(str, notify=typesChanged)
    def typeColor(self):
        return self._current_type().get("color", "#f59e0b") or "#f59e0b"

    @Property(str, notify=typesChanged)
    def typeShortcut(self):
        """Lettre du catalogue, vide quand le type n'en declare aucune."""
        return str(self._current_type().get("shortcut", "") or "").upper()[:1]

    def _current_type(self) -> dict:
        for row in self._types:
            if row.get("key") == self._type_key:
                return row
        return self._types[0] if self._types else {}

    @Slot(str)
    def setTypeKey(self, key: str):
        key = str(key or "").strip()
        if key == self._type_key:
            return
        if key and not any(row.get("key") == key for row in self._types):
            return
        self._type_key = key
        self.typesChanged.emit()
        self._reload_markers()

    @Slot(int)
    def setTypeIndex(self, index: int):
        index = int(index)
        if 0 <= index < len(self._types):
            self.setTypeKey(self._types[index].get("key", ""))

    @Property(int, notify=typesChanged)
    def typeIndex(self):
        for i, row in enumerate(self._types):
            if row.get("key") == self._type_key:
                return i
        return 0

    @Slot(str, str, result=bool)
    def setTypeShortcut(self, type_id: str, letter: str) -> bool:
        """Ecrit la lettre de raccourci d'un type depuis les reglages.

        Sans ce formulaire, un type cree par l'utilisateur n'avait aucune
        lettre : le clavier restait inutilisable et il fallait cliquer chaque
        marqueur a la souris.
        """
        type_id = str(type_id or "").strip()
        if not type_id:
            return False
        key = str(letter or "").strip().upper()[:1]
        try:
            import fish_annotate as fa

            self._ensure_fv()
            row = fa.set_behavior_shortcut(type_id, key)
        except Exception as exc:
            self._logs.append(f"[!] Raccourci : {exc}")
            self._set_status(str(exc))
            return False
        self.loadPointTypes()
        # Le catalogue de Data alimente les Preferences et le selecteur de la
        # page Mesure : sans cette relecture, l'ecran garderait l'ancienne
        # lettre jusqu'au prochain changement de page.
        try:
            self._data.loadEventTypes()
        except Exception:
            pass
        # Collision sur tout le catalogue, pas seulement sur les types
        # ponctuels : deux lignes affichant « Touche B » dans les réglages ne
        # disent pas laquelle répond, et le doute vaut un bug.
        try:
            catalogue = fa.list_event_types(active_only=True)
        except Exception:
            catalogue = list(self._types)
        clashes = [
            item for item in catalogue
            if key and str(item.get("shortcut") or "").upper()[:1] == key
        ]
        if key and len(clashes) > 1:
            others = ", ".join(
                str(item.get("label", "")) for item in clashes
                if item.get("id") != type_id
            )
            self._set_status(
                f"Attention : la lettre {key} est déjà prise par {others}"
            )
        else:
            self._set_status(
                f"Raccourci « {key} » posé sur {row.get('label', '')}" if key
                else f"Raccourci retiré de {row.get('label', '')}"
            )
        return True

    @Slot()
    def loadPointTypes(self):
        try:
            import fish_annotate as fa

            self._ensure_fv()
            rows = fa.list_point_event_types(active_only=True)
        except Exception as exc:
            self._logs.append(f"[!] Types ponctuels : {exc}")
            return
        self._types = rows
        if not any(row.get("key") == self._type_key for row in rows):
            self._type_key = rows[0].get("key", "") if rows else ""
        self.typesChanged.emit()

    # ── Pistes du media courant ────────────────────────────────────────

    @Property(list, notify=tracksChanged)
    def tracks(self):
        """Pistes suivies du media, avec leur plage et leur compte de points."""
        return list(self._tracks)

    @Property(int, notify=tracksChanged)
    def trackCount(self):
        return len(self._tracks)

    @Property(int, notify=markersChanged)
    def videoMarkerCount(self):
        """Total des marqueurs ponctuels de la video, tous types confondus."""
        return self._video_marker_count

    @Slot()
    def refresh(self):
        """Relit catalogue, pistes et marqueurs — a l'ouverture de la page."""
        self.loadPointTypes()
        self._reload_tracks()
        self._reload_markers()

    def _reload_tracks(self) -> None:
        media_id = str(getattr(self._data, "mediaId", "") or "")
        path = self._video_path()
        if not media_id or not path:
            self._tracks = []
            self.tracksChanged.emit()
            return
        db_path = self._db_path()
        if db_path is None:
            self._tracks = []
            self.tracksChanged.emit()
            return
        try:
            import fish_annotate as fa

            from src.annodb.connection import session_scope
            from src.annodb.tracks import track_overview

            with session_scope(db_path) as session:
                rows = track_overview(session, media_id)
            points = fa.list_behavior_points(path)
        except Exception as exc:
            self._logs.append(f"[!] Pistes a annoter : {exc}")
            self._tracks = []
            self.tracksChanged.emit()
            return

        per_track: dict[str, int] = {}
        for row in points:
            key = str(row.get("track_db_id") or "")
            per_track[key] = per_track.get(key, 0) + 1
        self._video_marker_count = len(points)

        out = []
        for row in rows:
            if int(row.get("sample_count") or 0) <= 0:
                continue
            track_id = str(row["track_id"])
            out.append({
                "trackId": track_id,
                "externalTrackId": int(row.get("external_track_id") or 0),
                "label": f"Piste #{int(row.get('external_track_id') or 0)}",
                "firstFrame": int(row.get("first_frame") or 0),
                "lastFrame": int(row.get("last_frame") or 0),
                "sampleCount": int(row.get("sample_count") or 0),
                "taxon": str(row.get("taxon") or ""),
                "markerCount": per_track.get(track_id, 0),
            })
        # L'ordre de `track_overview` est celui de la suspicion : pour annoter
        # des bouchees, l'ordre utile est le numero de piste.
        out.sort(key=lambda item: (item["externalTrackId"], item["trackId"]))
        self._tracks = out
        self.tracksChanged.emit()

    # ── Selection d'une piste ──────────────────────────────────────────

    @Property(str, notify=selectionChanged)
    def selectedTrackId(self):
        return self._selected

    @Property(str, notify=selectionChanged)
    def selectedTrackLabel(self):
        row = self._track_row(self._selected)
        return row.get("label", "") if row else ""

    @Property(int, notify=selectionChanged)
    def selectedFirstFrame(self):
        row = self._track_row(self._selected)
        return int(row.get("firstFrame", 0)) if row else 0

    @Property(int, notify=selectionChanged)
    def selectedLastFrame(self):
        row = self._track_row(self._selected)
        return int(row.get("lastFrame", 0)) if row else 0

    @Property(int, notify=selectionChanged)
    def rangeStart(self):
        """Debut de la piste en index timeline, pour la barre de marqueurs."""
        if not self._selected:
            return -1
        return int(self._measure.alignedIndexFromLeftAbs(self.selectedFirstFrame))

    @Property(int, notify=selectionChanged)
    def rangeEnd(self):
        if not self._selected:
            return -1
        return int(self._measure.alignedIndexFromLeftAbs(self.selectedLastFrame))

    @Property(bool, notify=selectionChanged)
    def armed(self):
        """Vrai quand un clic ou un raccourci poserait vraiment un marqueur."""
        return bool(self._selected) and bool(self._types)

    def _track_row(self, track_id: str) -> dict:
        for row in self._tracks:
            if row.get("trackId") == track_id:
                return row
        return {}

    @Slot(str)
    @Slot(str, bool)
    def selectTrack(self, track_id: str, seek: bool = True):
        """Cale l'application sur la piste : lecture et pas-a-pas sur sa plage.

        `seek=False` sert au ciblage automatique depuis la fiche du poisson :
        choisir une ligne du registre replace deja la video sur l'image de
        l'observation, et un second saut vers l'echantillon de piste le plus
        proche ferait bouger l'image sous les yeux de l'utilisateur sans qu'il
        ait rien demande.
        """
        track_id = str(track_id or "").strip()
        if not track_id:
            self.clearSelection()
            return
        if not self._track_row(track_id):
            self._reload_tracks()
        row = self._track_row(track_id)
        if not row:
            self._set_status("Piste inconnue sur cette vidéo")
            return
        self._selected = track_id
        # Le seek qui suit change l'image courante : sans cette remise a zero
        # le premier saut aurait declenche le pop d'un marqueur qu'on n'a pas
        # traverse, simplement parce qu'on a change de piste.
        self._last_pop_frame = -1
        self._load_samples(track_id)
        self._reload_markers()
        self._pin_trail(track_id)
        if seek:
            self.seekToFrame(self._nearest_sample_frame(self._current_abs_frame()))
        self._last_pop_frame = self._current_abs_frame()
        self.selectionChanged.emit()
        self._set_status(
            f"{row['label']} — {row['sampleCount']} position(s), "
            f"f{row['firstFrame']} → f{row['lastFrame']} · "
            f"{row['markerCount']} marqueur(s)"
        )

    @Slot(int)
    def selectTrackByExternalId(self, external_id: int):
        external_id = int(external_id)
        for row in self._tracks:
            if row.get("externalTrackId") == external_id:
                self.selectTrack(row["trackId"])
                return
        self._set_status(f"Piste #{external_id} sans positions enregistrées")

    @Slot()
    def clearSelection(self):
        self._selected = ""
        self._sample_frames = []
        self._sample_boxes = []
        self._sample_boxes_raw = []
        self._raw_token = None
        self._last_pop_frame = -1
        self._pin_trail("")
        self._markers = []
        # Le statut decrit TOUJOURS la piste selectionnee. Sans cette remise a
        # zero, passer a un poisson sans piste laissait « Piste #7 - 31
        # position(s) » sous le message qui annonce l'absence de piste.
        self._set_status("")
        self.markersChanged.emit()
        self.selectionChanged.emit()

    def _load_samples(self, track_id: str) -> None:
        self._sample_frames = []
        self._sample_boxes = []
        self._sample_boxes_raw = []
        self._raw_token = None
        db_path = self._db_path()
        if db_path is None:
            return
        try:
            from src.annodb.connection import session_scope
            from src.annodb.tracks import list_track_samples

            with session_scope(db_path) as session:
                rows = [
                    (int(s.frame_index), json.loads(s.bbox_json))
                    for s in list_track_samples(session, track_id)
                ]
        except Exception as exc:
            self._logs.append(f"[!] Positions de la piste : {exc}")
            return
        rows.sort(key=lambda item: item[0])
        self._sample_frames = [frame for frame, _ in rows]
        self._sample_boxes = [box for _, box in rows]

    # ── Boite de la piste a l'image affichee ───────────────────────────

    def _current_abs_frame(self) -> int:
        return int(self._measure.leftAbsFrameAt(self._measure.frameIndex))

    def _nearest_sample_frame(self, abs_frame: int) -> int:
        if not self._sample_frames:
            return abs_frame
        return min(self._sample_frames, key=lambda f: abs(f - abs_frame))

    def _ensure_raw_samples(self) -> None:
        """Recale les echantillons de la piste sur l'image brute, une fois.

        La conversion coute une indexation numpy pour toute la piste. La
        refaire a chaque image de lecture, soixante fois par seconde, aurait
        coute plus que le decodage video - et pour un resultat identique,
        puisque les echantillons ne bougent pas.
        """
        token = int(self._measure.rect_mapping_token())
        if self._raw_token == token:
            return
        self._raw_token = token
        mapping = self._measure.rect_mapping(True)
        if mapping.is_identity or not self._sample_boxes:
            self._sample_boxes_raw = list(self._sample_boxes)
            return
        moved = mapping.map_boxes([
            (
                float(box.get("x_min", 0.0)), float(box.get("y_min", 0.0)),
                float(box.get("x_max", 0.0)), float(box.get("y_max", 0.0)),
            )
            for box in self._sample_boxes
        ])
        self._sample_boxes_raw = [
            {"x_min": x1, "y_min": y1, "x_max": x2, "y_max": y2}
            for x1, y1, x2, y2 in moved
        ]

    def _display_boxes(self) -> list[dict]:
        """Les echantillons dans l'espace de l'image reellement affichee.

        A l'arret la vue montre l'image rectifiee : ce sont les boites telles
        qu'elles sont enregistrees, sans le moindre deplacement. C'est cette
        identite-la qui garde le pointage des bouchees exact.
        """
        if not self._measure.overlay_remap_active():
            return self._sample_boxes
        self._ensure_raw_samples()
        return self._sample_boxes_raw

    def _box_at(self, abs_frame: int, boxes: Optional[list] = None) -> Optional[dict]:
        """Boite exacte, sinon interpolee entre voisins proches, sinon None.

        Meme regle que l'export AVA : on n'invente pas de position sur un trou
        de suivi, on refuse simplement de dessiner une cible.
        """
        frames = self._sample_frames
        if boxes is None:
            boxes = self._sample_boxes
        if not frames or len(boxes) != len(frames):
            return None
        position = bisect_left(frames, abs_frame)
        if position < len(frames) and frames[position] == abs_frame:
            box = boxes[position]
            return {"box": box, "interpolated": False}
        if position == 0 or position >= len(frames):
            return None
        before_f, after_f = frames[position - 1], frames[position]
        if after_f - before_f > MAX_GAP_FRAMES:
            return None
        before, after = boxes[position - 1], boxes[position]
        ratio = (abs_frame - before_f) / float(after_f - before_f)
        box = {
            name: float(before.get(name, 0.0))
            + (float(after.get(name, 0.0)) - float(before.get(name, 0.0))) * ratio
            for name in ("x_min", "y_min", "x_max", "y_max")
        }
        return {"box": box, "interpolated": True}

    @Property("QVariantMap", notify=frameChanged)
    def trackBox(self):
        """Cible cliquable : la bbox de la piste selectionnee, a cette image.

        Elle est rendue dans l'espace de l'image affichee. A l'arret cela ne
        change rien - la vue montre l'image rectifiee, la conversion est
        l'identite, et le clic tombe exactement ou il tombait. Pendant la
        lecture, la vue montre la video brute : sans ce recalage la cible
        flottait a cote du poisson.
        """
        if not self._selected:
            return {"valid": False}
        found = self._box_at(self._current_abs_frame(), self._display_boxes())
        if found is None:
            return {"valid": False}
        box = found["box"]
        return {
            "valid": True,
            "x1": float(box.get("x_min", 0.0)),
            "y1": float(box.get("y_min", 0.0)),
            "x2": float(box.get("x_max", 0.0)),
            "y2": float(box.get("y_max", 0.0)),
            "interpolated": bool(found["interpolated"]),
        }

    # ── Le « pop » d'une bouchee sur l'image ───────────────────────────

    def _on_frame_advanced(self) -> None:
        """Fait apparaitre le marqueur des images franchies depuis la derniere.

        La lecture ne passe pas par toutes les images : le lecteur natif
        notifie sa position, et deux notifications peuvent etre distantes de
        plusieurs images. Comparer l'image courante a la liste des marqueurs
        aurait donc rate la plupart des bouchees. On regarde l'intervalle.
        """
        current = self._current_abs_frame()
        previous = self._last_pop_frame
        self._last_pop_frame = current
        if previous < 0 or previous == current or not self._markers:
            return
        if current > previous:
            crossed = [
                row for row in self._markers
                if previous < row["frameAbs"] <= current
            ]
            row = crossed[-1] if crossed else None
        else:
            crossed = [
                row for row in self._markers
                if current <= row["frameAbs"] < previous
            ]
            row = crossed[0] if crossed else None
        if row is not None:
            # Un seul pop par notification, meme si plusieurs bouchees se
            # suivent : l'animation redemarre au lieu de s'empiler.
            self._emit_pop(row)

    def _emit_pop(self, row: dict) -> None:
        found = self._box_at(int(row["frameAbs"]), self._display_boxes())
        if found is None:
            # Sans position connue du poisson sur cette image, pas de pop :
            # le poser au centre de l'ecran ferait croire a une bouchee
            # ailleurs que la ou elle a eu lieu.
            return
        box = found["box"]
        self.peckPopped.emit(
            (float(box.get("x_min", 0.0)) + float(box.get("x_max", 0.0))) * 0.5,
            (float(box.get("y_min", 0.0)) + float(box.get("y_max", 0.0))) * 0.5,
            str(row.get("symbol") or "●"),
            str(row.get("color") or "#f59e0b"),
            str(row.get("label") or ""),
        )

    def _pin_trail(self, track_id: str) -> None:
        """Dit a l'overlay quelle piste garde son trajet pendant la lecture."""
        if self._fish is None:
            return
        try:
            self._fish.pinTrackTrail(str(track_id or ""))
        except Exception as exc:  # trajet decoratif : jamais bloquant
            self._logs.append(f"[!] Trajet de piste : {exc}")

    @Slot(float, float, result=bool)
    def markFromOverlay(self, x: float, y: float) -> bool:
        """Clic sur la bbox de la piste : pose un marqueur, sinon rend la main.

        Rendre la main est essentiel — sinon un clic a cote de la boite serait
        avale et la mesure stereo deviendrait impossible tant qu'une piste est
        selectionnee.
        """
        if not self.armed:
            return False
        box = self.trackBox
        if not box.get("valid"):
            return False
        if not (box["x1"] <= float(x) <= box["x2"]
                and box["y1"] <= float(y) <= box["y2"]):
            return False
        return self.markAtCurrentFrame()

    # ── Marqueurs ──────────────────────────────────────────────────────

    @Property(list, notify=markersChanged)
    def markers(self):
        """Marqueurs de la piste selectionnee, index timeline compris."""
        return list(self._markers)

    @Property(int, notify=markersChanged)
    def markerCount(self):
        return len(self._markers)

    @Property(bool, notify=frameChanged)
    def currentFrameMarked(self):
        frame = self._current_abs_frame()
        key = self.typeKey
        return any(
            row.get("frameAbs") == frame and row.get("typeKey") == key
            for row in self._markers
        )

    @Property(str, notify=statusTextChanged)
    def statusText(self):
        return self._status

    def _reload_markers(self) -> None:
        path = self._video_path()
        if not path or not self._selected:
            self._markers = []
            self.markersChanged.emit()
            self.frameChanged.emit()
            return
        try:
            import fish_annotate as fa

            self._ensure_fv()
            rows = fa.list_behavior_points(path, track_db_id=self._selected)
            total = len(fa.list_behavior_points(path))
        except Exception as exc:
            self._logs.append(f"[!] Marqueurs : {exc}")
            self._markers = []
            self.markersChanged.emit()
            self.frameChanged.emit()
            return
        out = []
        for row in rows:
            frame_abs = row.get("frame_abs")
            if frame_abs is None:
                # Ligne historique en index timeline sans offset connu : la
                # placer au hasard sur la barre serait pire que l'omettre.
                continue
            out.append({
                "eventId": row["event_id"],
                "frameAbs": int(frame_abs),
                "frame": int(self._measure.alignedIndexFromLeftAbs(int(frame_abs))),
                "typeKey": row["event_type"],
                "label": row["event_label"],
                "symbol": row["event_symbol"],
                "color": row["event_color"],
                "author": row.get("author") or "",
            })
        out.sort(key=lambda item: item["frameAbs"])
        self._markers = out
        self._video_marker_count = total
        self.markersChanged.emit()
        self.frameChanged.emit()

    @Slot(result=bool)
    def markAtCurrentFrame(self) -> bool:
        if not self._selected:
            self._set_status("Sélectionnez d'abord une piste dans le registre")
            return False
        if not self._types:
            self._set_status(
                "Aucun comportement ponctuel : créez « Bouchée » dans les "
                "réglages (Comportements)"
            )
            return False
        frame = self._current_abs_frame()
        key = self.typeKey
        existing = next(
            (row for row in self._markers
             if row["frameAbs"] == frame and row["typeKey"] == key),
            None,
        )
        if existing is not None:
            self._set_status(
                f"{self.typeLabel} déjà marquée sur l'image {frame} — "
                "utilisez « Retirer »"
            )
            return False
        try:
            import fish_annotate as fa

            from src.annodb.frame_ref import FRAME_REF_ABSOLUTE

            self._ensure_fv()
            fa.add_behavior_point(
                self._selected, frame, event_type=key, frame_ref=FRAME_REF_ABSOLUTE,
            )
        except Exception as exc:
            self._logs.append(f"[!] {self.typeLabel} : {exc}")
            self._set_status(str(exc))
            return False
        self._after_write()
        # Le marqueur vient d'etre pose sur l'image affichee : le pop est le
        # retour visuel du geste, pas seulement un rappel a la relecture.
        posed = next(
            (row for row in self._markers
             if row["frameAbs"] == frame and row["typeKey"] == key),
            None,
        )
        if posed is not None:
            self._emit_pop(posed)
        self._set_status(
            f"{self.typeSymbol} {self.typeLabel} · {self.selectedTrackLabel} · "
            f"image {frame} — {self.markerCount} au total"
        )
        return True

    @Slot(str, result=bool)
    def removeMarker(self, event_id: str) -> bool:
        event_id = str(event_id or "").strip()
        if not event_id:
            return False
        try:
            import fish_annotate as fa

            self._ensure_fv()
            removed = fa.delete_behavior_point(event_id)
        except Exception as exc:
            self._logs.append(f"[!] Retrait de marqueur : {exc}")
            self._set_status(str(exc))
            return False
        if not removed:
            self._set_status("Marqueur déjà retiré")
            return False
        self._after_write()
        self._set_status(f"Marqueur retiré — {self.markerCount} restant(s)")
        return True

    @Slot(result=bool)
    def removeMarkerAtCurrentFrame(self) -> bool:
        frame = self._current_abs_frame()
        key = self.typeKey
        row = next(
            (item for item in self._markers
             if item["frameAbs"] == frame and item["typeKey"] == key),
            None,
        )
        if row is None:
            self._set_status(f"Aucun marqueur à retirer sur l'image {frame}")
            return False
        return self.removeMarker(row["eventId"])

    def _after_write(self) -> None:
        self._reload_markers()
        self._reload_tracks()
        self.selectionChanged.emit()
        # Le badge de comportement de l'overlay est calcule a partir d'un cache
        # relu une fois par video : sans invalidation il resterait sur l'etat
        # d'avant l'ecriture.
        if self._fish is not None:
            try:
                self._fish.invalidateGrazingCache()
            except Exception:
                pass

    # ── Raccourci clavier ──────────────────────────────────────────────

    @Slot(str, result=bool)
    @Slot(str, bool, result=bool)
    def handleShortcut(self, text: str, remove: bool = False) -> bool:
        """Lettre du catalogue : pose un marqueur, ou le retire avec `remove`.

        Le champ `shortcut` d'`event_types` n'etait branche nulle part. C'est
        pourtant lui qui rend le geste tenable : annoter des dizaines de
        bouchees a la souris, image par image, ne l'est pas.

        La touche Maj arrive en parametre plutot que devinee sur la casse du
        texte : selon la disposition clavier et l'outil qui envoie l'evenement,
        `event.text` reste minuscule meme avec Maj enfonce.
        """
        key = str(text or "")
        if len(key) != 1 or not key.strip():
            return False
        row = next(
            (item for item in self._types
             if str(item.get("shortcut") or "").upper()[:1] == key.upper()),
            None,
        )
        if row is None:
            return False
        self.setTypeKey(row.get("key", ""))
        if bool(remove):
            self.removeMarkerAtCurrentFrame()
        else:
            self.markAtCurrentFrame()
        return True

    # ── Navigation dans la plage de la piste ───────────────────────────

    @Slot(int)
    def seekToFrame(self, abs_frame: int):
        if int(self._measure.frameCount) <= 0:
            return
        if bool(self._measure.playing):
            self._measure.togglePlay()
        self._measure.frameIndex = int(
            self._measure.alignedIndexFromLeftAbs(int(abs_frame))
        )
        self._focus_overlay_box()

    @Slot(str)
    def seekToMarker(self, event_id: str):
        row = next(
            (item for item in self._markers if item["eventId"] == str(event_id)),
            None,
        )
        if row is None:
            return
        self.seekToFrame(row["frameAbs"])

    @Slot(int)
    def stepToMarker(self, direction: int):
        """Marqueur suivant (1) ou precedent (-1) de la piste selectionnee."""
        if not self._markers:
            self._set_status("Aucun marqueur sur cette piste")
            return
        current = self._current_abs_frame()
        if int(direction) >= 0:
            row = next(
                (item for item in self._markers if item["frameAbs"] > current), None,
            )
        else:
            row = next(
                (item for item in reversed(self._markers)
                 if item["frameAbs"] < current),
                None,
            )
        if row is None:
            self._set_status("Plus de marqueur dans cette direction")
            return
        self.seekToFrame(row["frameAbs"])

    @Slot()
    def replayTrack(self):
        """Repart au debut de la piste et relance la lecture de sa plage."""
        if not self._selected:
            return
        self.seekToFrame(self.selectedFirstFrame)
        if int(self._measure.frameCount) > 0 and not bool(self._measure.playing):
            self._measure.togglePlay()

    def _focus_overlay_box(self) -> None:
        """Entoure la piste sur l'image, avec le mecanisme de focus existant."""
        if self._fish is None or not self._selected:
            return
        box = self.trackBox
        if not box.get("valid"):
            return
        row = self._track_row(self._selected)
        try:
            self._fish.focusAnnotationBox(
                self._selected, int(self._measure.frameIndex),
                float(box["x1"]), float(box["y1"]),
                float(box["x2"]), float(box["y2"]),
                str(row.get("label", "")),
            )
        except Exception as exc:  # focus decoratif : jamais bloquant
            self._logs.append(f"[!] Focus piste : {exc}")
