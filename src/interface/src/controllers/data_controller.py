from __future__ import annotations

import os
import sys
import threading
from collections import Counter
from datetime import datetime
from pathlib import Path

from PySide6.QtCore import Property, QObject, Signal, Slot

from src.controllers.measure_controller import MeasureController
from src.models.gallery_list_model import GalleryListModel
from src.models.grazing_list_model import GrazingListModel
from src.models.registry_list_model import RegistryListModel
from src.util import paths
from src.util.log_model import LogModel

# Rang « non identifie » : sur le terrain on ne descend pas toujours jusqu'a
# l'espece. Un champ vide ne dit pas si le rang est inconnu ou pas encore
# saisi - NA le dit.
NA_LABEL = "NA"
_NA_TOKENS = frozenset({
    "na", "n/a", "n.a.", "nd", "n.d.",
    "non identifie", "non identifié",
    "indetermine", "indéterminé",
    "inconnu",
})


def _is_na(text: str) -> bool:
    return (text or "").strip().casefold() in _NA_TOKENS


def _normalize_rank_text(text: str) -> str:
    """Harmonise les variantes saisies (« na », « inconnu »…) sur « NA »."""
    text = (text or "").strip()
    return NA_LABEL if _is_na(text) else text


# Rangs du plus large au plus fin - l'ordre est ce qui rend _na_conflict lisible.
_RANK_LABELS = ("Famille", "Genre", "Espèce")


def _na_conflict(na_flags: list[bool], texts: list[str]) -> str:
    """Refuse « rang parent NA » + « rang plus fin renseigné », qui est faux.

    Un binôme porte son genre et un genre porte sa famille : déclarer
    « Genre = NA » tout en retenant « Trachinotus goodei » revient à dire
    deux choses contradictoires. Sans ce garde-fou, `ensure_taxon_from_ui`
    redéduit silencieusement le genre depuis le binôme, et la base se
    retrouve avec un `genus_id` réel ET `genus_is_na = True` - un NA que
    l'interface n'affiche jamais et qu'aucun export ne peut interpréter.

    Laisser un rang VIDE reste permis : c'est « la base le déduira », alors
    que NA est une affirmation d'annotateur.
    """
    for parent in range(len(texts) - 1):
        if not na_flags[parent]:
            continue
        for child in range(parent + 1, len(texts)):
            if not texts[child]:
                continue
            wide = _RANK_LABELS[parent]
            fine = _RANK_LABELS[child].lower()
            return (
                f"{wide} « NA » est incompatible avec {fine} "
                f"« {texts[child]} » : le taxon impose son rang parent. "
                f"Mettez {fine} à NA, ou laissez {wide.lower()} vide "
                f"pour que la base le déduise."
            )
    return ""


# Referentiels d'index de frame - voir annotations/src/annodb/frame_ref.py.
# Toutes les ecritures de ce controleur sont desormais en index ABSOLU du
# fichier video source, comme les echantillons de piste.
FRAME_REF_ABSOLUTE = "absolute"
FRAME_REF_TIMELINE_LEGACY = "timeline_legacy"


class DataController(QObject):
    subTabChanged = Signal()
    sessionSiteChanged = Signal()
    sessionTitleChanged = Signal()
    sessionNotesChanged = Signal()
    sessionDateChanged = Signal()
    mediaIdChanged = Signal()
    statusTextChanged = Signal()
    selectedTrackLabelChanged = Signal()
    selectedBoxIndexChanged = Signal()
    selectedAnnIdChanged = Signal()
    selectedObservationChanged = Signal()
    editFamilyChanged = Signal()
    editGenusChanged = Signal()
    editSpeciesChanged = Signal()
    editMeasurementMmChanged = Signal()
    registryRowDirtyChanged = Signal()
    draftChanged = Signal()
    dbAvailableChanged = Signal()
    busyChanged = Signal()
    videoPathChanged = Signal()
    _observationAdded = Signal(dict)
    _observationFailed = Signal(str)
    _sessionSaved = Signal(dict)
    _sessionFailed = Signal(str)
    _measurementPersisted = Signal(dict)
    _measurementPersistFailed = Signal(dict)
    _fishialPreviewReady = Signal(dict)
    _fishialPromotionReady = Signal(dict)
    _fishialProjectPromotionReady = Signal(dict)
    _fishialProjectProgress = Signal(dict)
    grazingOverlayChanged = Signal()
    galleryChanged = Signal()
    frameAiCountChanged = Signal()
    frameManualCountChanged = Signal()
    frameCountValidatedChanged = Signal()
    frameCountCurrentChanged = Signal()
    pendingStereoMeasureMmChanged = Signal()
    sessionStatsSummaryChanged = Signal()
    sessionMaxVisibleFishChanged = Signal()
    sessionRegistryStatsChanged = Signal()
    registryTotalCountChanged = Signal()
    registryChanged = Signal()
    familyOptionsChanged = Signal()
    genusOptionsChanged = Signal()
    speciesOptionsChanged = Signal()
    taxonomyReadyChanged = Signal()
    fishialMinRefsChanged = Signal()
    fishialSessionChanged = Signal()
    fishialProjectChanged = Signal()
    currentFrameIndexChanged = Signal()
    eventTypesChanged = Signal()
    eventTypeIndexChanged = Signal()
    behaviorTypesChanged = Signal()
    behaviorFlagChanged = Signal()
    # Evenements portes par la ligne selectionnee : flags ponctuels de
    # l'observation ET intervalles de sa piste. Deux sources, donc un signal
    # distinct de `behaviorFlagChanged`, qui ne connait que les flags.
    selectedEventsChanged = Signal()
    sessionIdChanged = Signal()

    def __init__(self, measure: MeasureController, fish: QObject | None = None, parent=None):
        super().__init__(parent)
        self._measure = measure
        self._fish = fish
        self._logs = LogModel(self)
        self._registry = RegistryListModel(self)
        self._grazing = GrazingListModel(self)
        self._gallery = GalleryListModel(self)
        self._site = ""
        self._title = ""
        self._notes = ""
        self._date = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        self._media_id = ""
        self._status = ""
        self._selected_track = ""
        self._selected_box_index = -1
        self._selected_ann_id = ""
        self._selected_row_data: dict = {}
        self._selected_frame_index = -1
        self._selected_label = ""
        self._selected_registry_row = -1
        self._edit_family = ""
        self._edit_genus = ""
        self._edit_species = ""
        self._edit_measurement_mm = 0.0
        self._saved_family = ""
        self._saved_genus = ""
        self._saved_species = ""
        self._saved_measurement_mm = 0.0
        self._row_dirty = False
        # Brouillon de poisson : un cadre choisi, sa taxonomie proposee et sa
        # longueur, AVANT toute ecriture. Le client decrivait le workflow comme
        # « a l'envers » parce qu'il fallait creer la ligne pour voir la
        # proposition du modele ; ici rien n'est ecrit tant que
        # « Enregistrer le poisson » n'a pas ete clique.
        self._draft_active = False
        self._draft_target: dict = {}
        self._draft_taxon_origin = ""
        self._busy = False
        self._taxon_index: dict | None = None
        self._family_options: list[str] = []
        self._genus_options: list[str] = []
        self._species_options: list[str] = []
        self._ensure_repo()
        self._ensure_fv()
        self._db_ok = self._check_db()
        self._sub_tab = 0
        self._frame_ai_count = 0
        self._frame_manual_count = 0
        self._frame_count_validated = False
        self._frame_count_current = False
        self._pending_stereo_mm = 0.0
        self._pending_stereo_target: dict | None = None
        self._measurement_generation = 0
        self._measurement_in_flight_generation = 0
        self._measurement_lock = threading.Lock()
        self._measurement_queues: dict[str, dict] = {}
        self._measurement_latest_seq: dict[str, int] = {}
        self._measurement_pending_ann_ids: set[str] = set()
        self._session_max_fish: int | None = None
        self._session_stats_summary = ""
        self._session_grazing_count = 0
        self._session_has_tracking = False
        self._session_flag_count = 0
        self._session_flagged_count = 0
        self._session_flag_summary = ""
        self._session_tracked_count = 0
        self._registry_total = 0
        self._fishial_min_refs = 5
        self._fishial_session_state = "idle"
        self._fishial_preview_state = "idle"
        self._fishial_session_result: dict = {}
        self._fishial_generation = 0
        self._fishial_project_state = "idle"
        self._fishial_project_result: dict = {}
        self._fishial_project_generation = 0
        self._current_frame_index = 0
        # False = pas encore calcule ; None = offset indeterminable.
        self._frame_offset_cached: int | None | bool = False
        self._session_id = ""
        self._event_types: list[dict] = []
        self._all_event_types: list[dict] = []
        self._event_type_index = 0
        # Marqueurs ponctuels (bouchees) de la video courante, tous types et
        # toutes pistes confondus. Le bandeau les comptait deja via `Pecks`,
        # mais les pastilles de la fiche ne les voyaient pas : l'utilisateur
        # lisait « 3 bouchee(s) » en haut et « Aucun comportement marque » plus
        # bas. On les relit ici, a la source, plutot que de faire dependre la
        # fiche de la piste que `Pecks` a sous la main a cet instant.
        self._behavior_points: list[dict] = []
        # Lecture simple : tout le travail par frame est reporte a la pause.
        self._frame_refresh_pending = False
        measure.leftVideoChanged.connect(self._on_video_changed)
        measure.frameIndexChanged.connect(self._emit_grazing_overlay)
        measure.frameIndexChanged.connect(self._on_measure_frame_changed)
        measure.playingChanged.connect(self._on_measure_playing_changed)
        self._observationAdded.connect(self._finish_observation_added)
        self._observationFailed.connect(self._finish_observation_failed)
        self._sessionSaved.connect(self._finish_session_saved)
        self._sessionFailed.connect(self._finish_session_failed)
        self._measurementPersisted.connect(self._finish_measurement_persisted)
        self._measurementPersistFailed.connect(self._finish_measurement_failed)
        self._fishialPreviewReady.connect(self._finish_fishial_preview)
        self._fishialPromotionReady.connect(self._finish_fishial_promotion)
        self._fishialProjectPromotionReady.connect(
            self._finish_fishial_project_promotion
        )
        self._fishialProjectProgress.connect(self._update_fishial_project_progress)

    def _repo(self) -> Path:
        return paths.app_root()

    def _ensure_fv(self):
        fv = self._repo() / "annotations"
        fv_src = fv / "src"
        for p in (fv_src, fv):
            if p.is_dir() and str(p) not in sys.path:
                sys.path.insert(0, str(p))

    def _ensure_repo(self):
        root = self._repo()
        if str(root) not in sys.path:
            sys.path.insert(0, str(root))

    def _check_db(self) -> bool:
        try:
            self._ensure_fv()
            import fish_annotate as fa

            if fa.is_available():
                return True
            # Premier démarrage : utiliser exactement le même chemin que les
            # lectures suivantes (préférence de stockage ou FISH_VISION_DB).
            # Le repli historique créait une seconde base près de l'exe.
            from src.annodb.connection import ensure_schema

            return ensure_schema().is_file()
        except ImportError as exc:
            self._logs.append(f"[!] DB annotations : import impossible ({exc})")
            return False
        except Exception as exc:
            self._logs.append(f"[!] DB annotations : {exc}")
            return False

    @staticmethod
    def _node_label(node: dict) -> str:
        vern = (node.get("common_name") or node.get("vernacular_name") or "").strip()
        sci = (node.get("scientific_name") or "").strip()
        if vern and sci:
            return f"{vern} ({sci})"
        return sci or vern or str(node.get("id", ""))

    def _filter_node_labels(self, nodes: list[dict], filter_text: str) -> list[str]:
        ft = filter_text.strip().lower()
        labels = [self._node_label(node) for node in nodes]
        if not ft:
            return sorted(labels, key=str.lower)
        scored: list[tuple[int, str]] = []
        for node in nodes:
            label = self._node_label(node)
            vern = (node.get("common_name") or node.get("vernacular_name") or "").strip().lower()
            sci = (node.get("scientific_name") or "").strip().lower()
            rank: int | None = None
            if vern.startswith(ft):
                rank = 0
            elif sci.startswith(ft):
                rank = 0
            elif ft in vern:
                rank = 1
            if rank is None:
                continue
            scored.append((rank, label))
        scored.sort(key=lambda row: (row[0], row[1].lower()))
        return [label for _, label in scored]

    def _snapshot_row_state(self):
        self._saved_family = self._edit_family
        self._saved_genus = self._edit_genus
        self._saved_species = self._edit_species
        self._saved_measurement_mm = self._edit_measurement_mm
        self._recompute_row_dirty()

    def _recompute_row_dirty(self):
        measurement_in_flight = self._selected_ann_id in self._measurement_pending_ann_ids
        dirty = bool(
            self._selected_ann_id
            and (
                measurement_in_flight
                or self._edit_family != self._saved_family
                or self._edit_genus != self._saved_genus
                or self._edit_species != self._saved_species
                or abs(self._edit_measurement_mm - self._saved_measurement_mm) > 0.05
            )
        )
        if self._row_dirty != dirty:
            self._row_dirty = dirty
            self.registryRowDirtyChanged.emit()

    def _family_id_for_text(self, text: str) -> str | None:
        if not self._taxon_index or not text.strip() or _is_na(text):
            return None
        import fish_annotate as fa

        parsed = fa.parse_taxon_label(text)
        tid = fa.find_taxon_id(parsed, rank="family")
        if tid:
            return tid
        ft = text.strip().lower()
        for node in self._taxon_index.get("families", []):
            if self._node_label(node).lower() == ft:
                return node["id"]
            if (node.get("scientific_name") or "").lower() == ft:
                return node["id"]
            vern = (node.get("common_name") or node.get("vernacular_name") or "").strip().lower()
            if vern and vern == ft:
                return node["id"]
        return None

    def _genus_nodes_for_family(self, family_id: str | None) -> list[dict]:
        if not self._taxon_index:
            return []
        if family_id:
            return [
                n for n in self._taxon_index["children"].get(family_id, [])
                if n.get("rank") == "genus"
            ]
        return [n for n in self._taxon_index["nodes"].values() if n.get("rank") == "genus"]

    def _species_nodes_for(self, family_id: str | None, genus_id: str | None) -> list[dict]:
        if not self._taxon_index:
            return []
        if genus_id:
            return [
                n for n in self._taxon_index["children"].get(genus_id, [])
                if n.get("rank") == "species"
            ]
        if family_id:
            out: list[dict] = []
            for gen in self._genus_nodes_for_family(family_id):
                out.extend(self._species_nodes_for(family_id, gen["id"]))
            return out
        return [n for n in self._taxon_index["nodes"].values() if n.get("rank") == "species"]

    def _rebuild_taxon_option_lists(self):
        # NA en tete de chaque liste : le rang non identifie doit se choisir
        # aussi vite qu'un taxon, sans avoir a le taper.
        families = (self._taxon_index or {}).get("families", [])
        self._family_options = [NA_LABEL] + sorted(
            (self._node_label(n) for n in families),
            key=str.lower,
        )
        family_id = self._family_id_for_text(self._edit_family)
        self._genus_options = [NA_LABEL] + sorted(
            (self._node_label(n) for n in self._genus_nodes_for_family(family_id)),
            key=str.lower,
        )
        genus_id = None
        if self._edit_genus.strip() and not _is_na(self._edit_genus) and self._taxon_index:
            import fish_annotate as fa

            genus_id = fa.find_taxon_id(
                fa.parse_taxon_label(self._edit_genus),
                rank="genus",
            )
        self._species_options = [NA_LABEL] + sorted(
            (
                self._node_label(n)
                for n in self._species_nodes_for(family_id, genus_id)
            ),
            key=str.lower,
        )
        self.familyOptionsChanged.emit()
        self.genusOptionsChanged.emit()
        self.speciesOptionsChanged.emit()

    @Slot()
    def ensureTaxonomy(self):
        if not self._db_ok:
            return
        try:
            self._ensure_repo()
            self._ensure_fv()
            import fish_annotate as fa

            fa.ensure_fishial_taxonomy_if_needed()
            self._taxon_index = fa.build_taxon_index()
            self._rebuild_taxon_option_lists()
            self.taxonomyReadyChanged.emit()
        except Exception as exc:
            self._logs.append(f"[!] Taxonomie : {exc}")

    @Property(str, constant=True)
    def naLabel(self):
        """Libelle du rang non identifie - epingle en tete des listes."""
        return NA_LABEL

    @Property(list, notify=familyOptionsChanged)
    def familyOptions(self):
        return self._family_options

    @Property(list, notify=genusOptionsChanged)
    def genusOptions(self):
        return self._genus_options

    @Property(list, notify=speciesOptionsChanged)
    def speciesOptions(self):
        return self._species_options

    @Slot(str)
    def setFamilyFilter(self, text: str):
        if self._edit_family != text:
            self._edit_family = text
            self.editFamilyChanged.emit()
        self._rebuild_taxon_option_lists()
        self._recompute_row_dirty()

    @Slot(str)
    def setGenusFilter(self, text: str):
        if self._edit_genus != text:
            self._edit_genus = text
            self.editGenusChanged.emit()
        self._rebuild_taxon_option_lists()
        self._recompute_row_dirty()

    @Slot(str)
    def setSpeciesFilter(self, text: str):
        if self._edit_species != text:
            self._edit_species = text
            self.editSpeciesChanged.emit()
        self._recompute_row_dirty()

    @Property(int, notify=subTabChanged)
    def subTab(self):
        return self._sub_tab

    # Une session de travail, puis la bibliothèque Fishial locale.
    SUB_TAB_MAX = 1

    @subTab.setter
    def subTab(self, v: int):
        v = max(0, min(int(v), self.SUB_TAB_MAX))
        if self._sub_tab != v:
            self._sub_tab = v
            self.subTabChanged.emit()

    @Property(QObject, constant=True)
    def logs(self):
        return self._logs

    @Property(QObject, constant=True)
    def registry(self):
        return self._registry

    @Property(QObject, constant=True)
    def grazing(self):
        return self._grazing

    @Property(QObject, constant=True)
    def gallery(self):
        return self._gallery

    @Property(str, notify=sessionSiteChanged)
    def sessionSite(self):
        return self._site

    @sessionSite.setter
    def sessionSite(self, v: str):
        if self._site != v:
            self._site = v
            self.sessionSiteChanged.emit()

    @Property(str, notify=sessionTitleChanged)
    def sessionTitle(self):
        return self._title

    @sessionTitle.setter
    def sessionTitle(self, v: str):
        if self._title != v:
            self._title = v
            self.sessionTitleChanged.emit()

    @Property(str, notify=sessionNotesChanged)
    def sessionNotes(self):
        return self._notes

    @sessionNotes.setter
    def sessionNotes(self, v: str):
        if self._notes != v:
            self._notes = v
            self.sessionNotesChanged.emit()

    @Property(str, notify=sessionDateChanged)
    def sessionDate(self):
        return self._date

    @sessionDate.setter
    def sessionDate(self, v: str):
        if self._date != v:
            self._date = v
            self.sessionDateChanged.emit()

    @Property(str, notify=mediaIdChanged)
    def mediaId(self):
        return self._media_id

    @Property(str, notify=statusTextChanged)
    def statusText(self):
        return self._status

    @Slot(str)
    def appendStatusText(self, msg: str):
        """Ajoute une precision SANS effacer le statut deja pose.

        La vue stereo ecrasait le statut du controleur juste apres l'avoir
        declenche : le message qui expliquait le preremplissage disparaissait
        avant d'avoir ete lu.
        """
        extra = str(msg).strip()
        if not extra:
            return
        self._set_status(f"{self._status} · {extra}" if self._status else extra)

    @Slot(str)
    def setStatusText(self, msg: str):
        self._set_status(msg)

    @Property(str, notify=selectedTrackLabelChanged)
    def selectedTrackLabel(self):
        return self._selected_track

    @selectedTrackLabel.setter
    def selectedTrackLabel(self, v: str):
        if self._selected_track != v:
            self._selected_track = v
            self.selectedTrackLabelChanged.emit()

    @Property(int, notify=selectedBoxIndexChanged)
    def selectedBoxIndex(self):
        return self._selected_box_index

    @Property(str, notify=selectedAnnIdChanged)
    def selectedAnnId(self):
        return self._selected_ann_id

    @Property(str, notify=editFamilyChanged)
    def editFamily(self):
        return self._edit_family

    @editFamily.setter
    def editFamily(self, v: str):
        if self._edit_family != v:
            self._edit_family = v
            self.editFamilyChanged.emit()

    @Property(str, notify=editGenusChanged)
    def editGenus(self):
        return self._edit_genus

    @editGenus.setter
    def editGenus(self, v: str):
        if self._edit_genus != v:
            self._edit_genus = v
            self.editGenusChanged.emit()

    @Property(str, notify=editSpeciesChanged)
    def editSpecies(self):
        return self._edit_species

    @editSpecies.setter
    def editSpecies(self, v: str):
        if self._edit_species != v:
            self._edit_species = v
            self.editSpeciesChanged.emit()

    @Property(bool, notify=dbAvailableChanged)
    def dbAvailable(self):
        return self._db_ok

    @Property(bool, notify=busyChanged)
    def busy(self):
        return self._busy

    @Property(str, notify=videoPathChanged)
    def videoPath(self):
        return self._measure.leftVideo

    @Property(int, notify=registryChanged)
    def registryCount(self):
        return self._registry.rowCount()

    @Property(int, notify=galleryChanged)
    def galleryCount(self):
        return self._gallery.rowCount()

    def _set_status(self, msg: str):
        self._status = msg
        self.statusTextChanged.emit()

    def _set_busy(self, v: bool):
        if self._busy != v:
            self._busy = v
            self.busyChanged.emit()

    def _emit_grazing_overlay(self):
        if self._measure.playing:
            self._frame_refresh_pending = True
            return
        self.grazingOverlayChanged.emit()

    def _on_measure_playing_changed(self):
        """Reprend a la pause le travail par frame saute pendant la lecture."""
        if self._measure.playing or not self._frame_refresh_pending:
            return
        self._frame_refresh_pending = False
        self._on_measure_frame_changed()
        self.grazingOverlayChanged.emit()

    def _on_video_changed(self):
        self._invalidate_pending_stereo_measure()
        self.videoPathChanged.emit()
        self._clear_selected_row()
        # Une autre paire peut venir avec une autre synchro.
        self._frame_offset_cached = False
        self.loadSessionMetadata()

    @Slot()
    def loadSessionMetadata(self):
        path = self._measure.leftVideo
        if not path or not self._db_ok:
            self._media_id = ""
            self._set_session_id("")
            self.mediaIdChanged.emit()
            return
        try:
            import fish_annotate as fa
            import fish_db_stats as fdb

            self._ensure_fv()
            mid = fa.resolve_media_id(path, create=False)
            if not mid:
                self._media_id = ""
                self._set_session_id("")
                self.mediaIdChanged.emit()
                return
            meta = fdb.get_session_metadata(mid) or {}
            self._media_id = mid
            self.sessionSite = meta.get("site", "") or ""
            self.sessionTitle = meta.get("session_title", "") or ""
            self.sessionNotes = meta.get("notes", "") or ""
            self.sessionDate = meta.get("session_date", self._date) or self._date
            # La session (paire G/D) prime sur les metadonnees du media : c'est
            # elle qui porte le lieu, la date et l'offset de synchro figes.
            found = fa.find_session_for_media(mid)
            self._set_session_id(str(found.get("session_id", "")) if found else "")
            if found:
                self.sessionSite = found.get("site", "") or self._site
                self.sessionTitle = found.get("name", "") or self._title
                self.sessionNotes = found.get("notes", "") or self._notes
                self.sessionDate = found.get("session_date", "") or self._date
            self.mediaIdChanged.emit()
        except Exception as exc:
            self._logs.append(f"[!] Session : {exc}")

    def _set_session_id(self, session_id: str) -> None:
        if self._session_id != session_id:
            self._session_id = session_id
            # Une autre session veut dire un autre offset de synchro fige.
            self._frame_offset_cached = False
            self.sessionIdChanged.emit()

    @Property(str, notify=sessionIdChanged)
    def sessionId(self):
        """Session active (journée multi-prises) - vide si aucune."""
        return self._session_id

    @Slot(int)
    def setSelectedTrackFromId(self, track_id: int):
        track_id = int(track_id)
        self.selectedTrackLabel = str(track_id) if track_id >= 0 else ""

    @Slot(int)
    def selectBoxIndex(self, box_index: int):
        index = int(box_index)
        if self._fish is not None:
            count = int(self._fish.lastBoxCount)
            if index < 0 or index >= count:
                index = -1
            if int(self._fish.selectedFishIndex) != index:
                self._fish.setSelectedFishIndex(index)
        if self._selected_box_index != index:
            self._invalidate_pending_stereo_measure()
            # Changer de cadre abandonne le brouillon : sa taxonomie proposee
            # ne vaut que pour le poisson qu'on vient de quitter.
            self._clear_draft()
            self._selected_box_index = index
            self.selectedBoxIndexChanged.emit()

    @Slot(int)
    def selectBoxExplicit(self, box_index: int):
        """Dernier clic utilisateur : la bbox remplace toute ancienne cible.

        C'est aussi le geste qui ouvre le brouillon : le panneau de droite est
        prerempli ICI, avant toute ecriture. Le client tenait a cet ordre -
        on clique le poisson, la taxonomie se propose, on corrige, on mesure,
        puis un seul bouton enregistre.
        """
        index = int(box_index)
        box = (
            self._fish.lastBoxAtIndex(index)
            if self._fish is not None and index >= 0
            else {}
        )
        same_observation = bool(
            box
            and self._selected_ann_id
            and self._selected_row_abs_frame() == self._current_abs_frame()
            and self._row_bbox_signature(self._selected_row_data)
            == self._bbox_signature(box)
        )
        if not same_observation:
            if self._row_dirty:
                # Le brouillon reecrit les trois champs de taxon : abandonner
                # sans un mot une identification en cours de saisie serait
                # exactement la perte de donnees que le verrou d'ajout evite.
                self._set_status(
                    "Identification non enregistrée sur la ligne sélectionnée - "
                    "validez-la ou annulez avant de passer au poisson suivant"
                )
                return
            self._clear_selected_row()
            if self._fish is not None:
                self._fish.clearFocusBox()
        self.selectBoxIndex(index)
        if not same_observation and index >= 0 and box:
            self._begin_draft(index, box)

    def _clear_draft(self) -> None:
        if not self._draft_active:
            return
        self._draft_active = False
        self._draft_target = {}
        self._draft_taxon_origin = ""
        self.draftChanged.emit()

    def _indexed_taxon_id(self, scientific: str, rank: str) -> str | None:
        """Cherche un noeud dans l'index deja charge, sinon en base.

        `fish_annotate.find_taxon_id` reconstruit tout l'index de taxonomie a
        chaque appel : acceptable pour une validation, pas pour un clic droit.
        """
        name = (scientific or "").strip().lower()
        if not name:
            return None
        if self._taxon_index:
            for node in self._taxon_index.get("by_name", {}).get(name, []):
                if node.get("rank") == rank:
                    return node["id"]
            return None
        import fish_annotate as fa

        return fa.find_taxon_id(scientific, rank)

    def _draft_taxon_proposal(self, box: dict) -> tuple[str, str, str, str]:
        """Taxon propose pour un cadre - lecture seule, aucune observation ecrite.

        On reste volontairement sur les chemins bon marche : le nom d'espece
        que la detection porte deja, sinon la classe du modele. La resolution
        complete de `fish_annotate.propose_taxon_from_box` synchronise le
        referentiel Fishial (et peut aller le telecharger) : inacceptable a
        chaque clic droit. Ce que l'utilisateur laisse dans les champs fait
        foi de toute facon au moment de l'enregistrement.
        """
        species = str(box.get("speciesName") or box.get("species_name") or "").strip()
        cls_name = str(box.get("clsName") or box.get("cls_name") or "").strip()
        if not self._db_ok:
            return "", "", "", ""
        try:
            import fish_annotate as fa

            self._ensure_fv()
            taxon_id = self._indexed_taxon_id(species, "species") if species else None
            origin = "espèce proposée par le modèle" if taxon_id else ""
            if not taxon_id and cls_name and cls_name not in ("fish", "poisson"):
                taxon_id = fa.propose_taxon_from_detection(cls_name)
                origin = f"classe du détecteur « {cls_name} »" if taxon_id else ""
            if taxon_id:
                hier = fa.resolve_hierarchy(taxon_id)
                return (
                    self._taxon_display(hier.get("family", "") or "", "family"),
                    self._taxon_display(hier.get("genus", "") or "", "genus"),
                    self._taxon_display(hier.get("species", "") or "", "species"),
                    origin,
                )
            if species:
                # Espece proposee mais absente du referentiel local : l'afficher
                # telle quelle vaut mieux que trois champs vides, la validation
                # la resoudra (ou la creera) au moment de l'ecriture.
                return "", "", species, "espèce proposée, hors référentiel local"
        except Exception as exc:
            self._logs.append(f"[!] Proposition de taxon : {exc}")
        return "", "", "", ""

    def _begin_draft(self, index: int, box: dict) -> None:
        """Prepare la fiche du poisson choisi, sans ecrire une seule ligne."""
        path = str(self._measure.leftVideo or "")
        abs_frame = self._current_abs_frame() if path else -1
        signature = self._bbox_signature(box)
        if (
            self._draft_active
            and self._draft_target.get("bbox") == signature
            and self._draft_target.get("frame_index") == abs_frame
        ):
            # Re-cliquer le meme poisson ne doit pas ecraser ce qui vient
            # d'etre corrige a la main dans les trois champs.
            return
        family, genus, species, origin = self._draft_taxon_proposal(box)
        self.editFamily = family
        self.editGenus = genus
        self.editSpecies = species
        mm = self._pending_measurement_for_box(path, abs_frame, box) or 0.0
        if abs(self._edit_measurement_mm - mm) > 0.001:
            self._edit_measurement_mm = mm
            self.editMeasurementMmChanged.emit()
        self._rebuild_taxon_option_lists()
        self._snapshot_row_state()
        self._draft_active = True
        self._draft_target = {
            "video": path,
            "frame_index": abs_frame,
            "bbox": signature,
            "box_index": int(index),
        }
        self._draft_taxon_origin = origin
        self.draftChanged.emit()
        if family or genus or species:
            self._set_status(
                f"Taxonomie préremplie ({origin}) - corrigez si besoin, "
                "mesurez, puis « Enregistrer le poisson »"
            )
        else:
            self._set_status(
                "Poisson sélectionné, aucune proposition du modèle - "
                "identifiez, mesurez, puis « Enregistrer le poisson »"
            )

    @Property(bool, notify=draftChanged)
    def draftActive(self):
        """Une fiche est ouverte pour un cadre encore absent de la base."""
        return self._draft_active

    @Property(str, notify=draftChanged)
    def draftTaxonOrigin(self):
        return self._draft_taxon_origin

    def _taxon_display(self, scientific: str, rank: str) -> str:
        sci = (scientific or "").strip()
        if not sci or not self._taxon_index:
            return sci
        key = sci.lower()
        for node in self._taxon_index.get("nodes", {}).values():
            if node.get("rank") != rank:
                continue
            if (node.get("scientific_name") or "").strip().lower() == key:
                return self._node_label(node)
        return sci

    def _rank_edit_text(self, data: dict, key: str, rank: str) -> str:
        """Texte du champ : le taxon, ou NA si la ligne a ete validee sans lui."""
        text = self._taxon_display(data.get(key, "") or "", rank)
        if text:
            return text
        if data.get(f"{key}_is_na") is True:
            return NA_LABEL
        return (
            NA_LABEL
            if str(data.get("identification_status", "")) == "unidentifiable"
            else ""
        )

    @Slot(int)
    def loadRegistryRow(self, row: int):
        data = self._registry.row_at(row)
        if not data:
            return
        self._select_observation_data(data, row)

    def _select_observation_data(self, data: dict, row: int = -1) -> None:
        """Unique point d'entrée d'une sélection et de son aperçu Fishial."""
        # Ouvrir une ligne deja enregistree ferme la fiche en cours : les deux
        # modes partagent les memes champs, ils ne peuvent pas coexister.
        self._clear_draft()
        next_ann_id = str(data.get("ann_id", ""))
        if self._selected_ann_id != next_ann_id:
            self._invalidate_pending_stereo_measure()
            self.selectBoxIndex(-1)
        self._selected_ann_id = next_ann_id
        self.selectedAnnIdChanged.emit()
        self._remember_selected_row(data, row)
        self.editFamily = self._rank_edit_text(data, "family", "family")
        self.editGenus = self._rank_edit_text(data, "genus", "genus")
        self.editSpecies = self._rank_edit_text(data, "species", "species")
        mm = data.get("measurement_mm")
        self._edit_measurement_mm = float(mm) if mm is not None else 0.0
        self.editMeasurementMmChanged.emit()
        self._rebuild_taxon_option_lists()
        tid = data.get("track_id")
        if tid is not None:
            self.selectedTrackLabel = str(tid)
        self._snapshot_row_state()
        self.refreshFishialSessionPreview()

    @Property(int, notify=selectedObservationChanged)
    def selectedFrameIndex(self):
        return self._selected_frame_index

    @Property(str, notify=selectedObservationChanged)
    def selectedLabel(self):
        return self._selected_label

    @Property(int, notify=selectedObservationChanged)
    def selectedRegistryRow(self):
        return self._selected_registry_row

    # Statuts d'identification tels qu'affiches : la base parle anglais, l'UI
    # parle francais et dit ce que le mot veut dire.
    _STATUS_LABELS = {
        "unreviewed": "Non relu",
        "identified": "Identifié",
        "ambiguous": "Ambigu",
        "unidentifiable": "Non identifiable",
    }

    @Property(str, notify=selectedObservationChanged)
    def selectedIdentificationStatus(self):
        """Statut de relecture de la ligne selectionnee, en clair."""
        status = str(self._selected_row_data.get("identification_status") or "")
        if not status and self._selected_row_data:
            try:
                from src.annodb.identification import is_authoritative_identification

                class _RowView:
                    pass

                view = _RowView()
                for key in ("taxon_node_id", "identification_status", "source"):
                    setattr(view, key, self._selected_row_data.get(key))
                if is_authoritative_identification(view):
                    return "Identifié (legacy)"
            except ImportError:
                pass
        return self._STATUS_LABELS.get(status, "")

    @Property(str, notify=selectedObservationChanged)
    def selectedProvenance(self):
        """Origine de la boite : modele + seuil, ou saisie manuelle.

        Cette ligne survit a la validation : c'est tout l'interet de la
        provenance immuable par ajout.
        """
        data = self._selected_row_data
        if not data:
            return ""
        parts: list[str] = []
        model = str(data.get("model_id") or "")
        if model:
            conf = data.get("confidence")
            threshold = data.get("model_conf_threshold")
            detail = f"modèle « {model} »"
            if conf:
                detail += f", confiance {float(conf):.2f}"
            if threshold:
                detail += f" (seuil {float(threshold):.2f})"
            parts.append(detail)
        elif str(data.get("source") or "") == "manual":
            parts.append("boîte tracée à la main")
        reviewer = str(data.get("reviewed_by") or "")
        if reviewer:
            parts.append(f"relu par {reviewer}")
        return " · ".join(parts)

    @staticmethod
    def _observation_label(data: dict) -> str:
        for key in ("species", "genus", "family"):
            value = (data.get(key) or "").strip()
            if value:
                return value
        if str(data.get("identification_status", "")) == "unidentifiable":
            return "Non identifie (NA)"
        tid = data.get("track_id")
        return f"Piste #{tid}" if tid else "Poisson"

    def _remember_selected_row(self, data: dict, row: int = -1) -> None:
        self._selected_row_data = dict(data)
        self.behaviorFlagChanged.emit()
        self.selectedEventsChanged.emit()
        # Affichage en index absolu, comme partout ailleurs.
        _timeline, frame, _warning = self._timeline_index_for_row(data)
        label = self._observation_label(data)
        if (
            frame == self._selected_frame_index
            and label == self._selected_label
            and row == self._selected_registry_row
        ):
            return
        self._selected_frame_index = frame
        self._selected_label = label
        self._selected_registry_row = row
        self.selectedObservationChanged.emit()

    def _clear_selected_row(self) -> None:
        self._invalidate_pending_stereo_measure()
        if self._selected_ann_id:
            self._selected_ann_id = ""
            self.selectedAnnIdChanged.emit()
            # Sans ce recalcul, le verrou « identification non enregistrée »
            # restait arme alors qu'il ne designait plus aucune ligne : plus
            # aucun ajout n'etait possible jusqu'au redemarrage.
            self._recompute_row_dirty()
        self._selected_row_data = {}
        self.behaviorFlagChanged.emit()
        self.selectedEventsChanged.emit()
        self._fishial_generation += 1
        self._fishial_session_state = "idle"
        self._fishial_preview_state = "idle"
        self._fishial_session_result = {}
        self.fishialSessionChanged.emit()
        if self._selected_frame_index == -1 and not self._selected_label:
            return
        self._selected_frame_index = -1
        self._selected_label = ""
        self._selected_registry_row = -1
        self.selectedObservationChanged.emit()

    def _bbox_pixels(self, geom: dict) -> tuple[float, float, float, float] | None:
        """Geometrie base -> bbox pixels de l'image affichee.

        Les champs declares (`units`, `ref_width`, `ref_height`) priment ; le
        reniflage de magnitude ne sert plus qu'aux lignes historiques.
        """
        if not geom:
            return None
        width = float(self._measure.frameWidth or 0)
        height = float(self._measure.frameHeight or 0)

        if "units" in geom:
            ref_w = float(geom.get("ref_width") or 0) or width
            ref_h = float(geom.get("ref_height") or 0) or height
            if "x_min" in geom:
                x1, y1 = float(geom["x_min"]), float(geom["y_min"])
                x2, y2 = float(geom["x_max"]), float(geom["y_max"])
            elif "cx" in geom:
                cx, cy = float(geom["cx"]), float(geom["cy"])
                bw, bh = float(geom.get("w", 0.0)), float(geom.get("h", 0.0))
                x1, y1, x2, y2 = cx - bw / 2, cy - bh / 2, cx + bw / 2, cy + bh / 2
            else:
                return None
            if geom.get("units") == "normalized":
                if width <= 0 or height <= 0:
                    return None
                return (x1 * width, y1 * height, x2 * width, y2 * height)
            if ref_w > 0 and width > 0 and abs(ref_w - width) > 0.5:
                # Boite tracee sur une image d'une autre taille : on remet a
                # l'echelle plutot que d'afficher un cadre decale.
                sx, sy = width / ref_w, height / (ref_h or height)
                return (x1 * sx, y1 * sy, x2 * sx, y2 * sy)
            return (x1, y1, x2, y2)

        if "x_min" in geom:
            return (
                float(geom["x_min"]),
                float(geom["y_min"]),
                float(geom["x_max"]),
                float(geom["y_max"]),
            )
        if "cx" not in geom:
            return None
        cx = float(geom.get("cx", 0.0))
        cy = float(geom.get("cy", 0.0))
        bw = float(geom.get("w", 0.0))
        bh = float(geom.get("h", 0.0))
        if geom.get("normalized", True):
            if width <= 0 or height <= 0:
                return None
            cx, cy, bw, bh = cx * width, cy * height, bw * width, bh * height
        return (cx - bw / 2, cy - bh / 2, cx + bw / 2, cy + bh / 2)

    def _timeline_offset(self) -> int | None:
        """Decalage timeline -> absolu, mis en cache.

        L'offset **fige** sur la session de ce media prime : c'est celui avec
        lequel ses lignes ont ete ecrites. A defaut seulement, on relit le
        sync_frames.npy courant - qui peut decrire une autre synchro.
        """
        if self._frame_offset_cached is not False:
            return self._frame_offset_cached
        try:
            import fish_annotate as fa

            self._ensure_fv()
            self._frame_offset_cached = fa.timeline_frame_offset(self._media_id or None)
        except Exception as exc:
            self._logs.append(f"[!] Offset de synchro : {exc}")
            self._frame_offset_cached = None
        return self._frame_offset_cached

    def _timeline_index_for_row(self, data: dict) -> tuple[int | None, int, str]:
        """(index timeline, index absolu, avertissement) d'une ligne du registre.

        Les lignes recentes sont en index absolu ; les lignes historiques sont
        converties via l'offset de sync, et signalees quand elles ne le sont
        pas - jamais rejouees en silence sur la mauvaise image.
        """
        stored = int(data.get("frame_index") or 0)
        row_ref = str(data.get("frame_ref") or FRAME_REF_TIMELINE_LEGACY)
        if row_ref == FRAME_REF_ABSOLUTE:
            return self._measure.alignedIndexFromLeftAbs(stored), stored, ""
        offset = self._timeline_offset()
        abs_frame = None if offset is None else stored + offset
        if abs_frame is None:
            return None, stored, "index historique non convertible (sync absente)"
        return (
            self._measure.alignedIndexFromLeftAbs(abs_frame),
            abs_frame,
            "index historique converti",
        )

    def _focus_observation(self, data: dict) -> None:
        label = self._observation_label(data)
        row_media_id = str(data.get("media_id") or "")
        current_media_id = self._media_id
        if row_media_id and not current_media_id and self._measure.leftVideo:
            try:
                import fish_annotate as fa

                self._ensure_fv()
                current_media_id = str(
                    fa.resolve_media_id(self._measure.leftVideo, create=False) or ""
                )
            except Exception:
                current_media_id = ""
        different_media = bool(
            row_media_id and current_media_id and row_media_id != current_media_id
        )
        if row_media_id and not current_media_id:
            current_name = Path(self._measure.leftVideo or "").name
            row_name = str(data.get("media_name") or "")
            different_media = bool(
                row_name and current_name and row_name != current_name
            )
        if different_media:
            self._set_status(
                f"{label} appartient à « {data.get('media_name') or 'une autre vidéo'} ». "
                "Chargez cette vidéo depuis la session pour l'afficher."
            )
            return
        timeline_idx, abs_frame, warning = self._timeline_index_for_row(data)
        note = f" · {warning}" if warning else ""
        if self._measure.frameCount <= 0:
            self._set_status(
                f"{label} - frame {abs_frame} · chargez les vidéos pour l'afficher{note}"
            )
            return
        if timeline_idx is None:
            self._set_status(f"{label} - frame {abs_frame} · {warning}")
            self._logs.append(f"[!] {label} : {warning}")
            return
        if self._measure.playing:
            self._measure.togglePlay()
        self._measure.frameIndex = timeline_idx
        # La timeline peut etre plus courte que la session d'origine (setter borne l'index).
        reached = self._measure.frameIndex
        reached_abs = self._measure.leftAbsFrameAt(reached)
        box = self._bbox_pixels(data.get("geometry") or {})
        if self._fish is None or box is None:
            self._set_status(f"{label} - frame {abs_frame} (bbox non enregistrée){note}")
            return
        self._fish.focusAnnotationBox(
            str(data.get("ann_id", "")), reached, box[0], box[1], box[2], box[3], label,
        )
        if reached_abs != abs_frame:
            self._set_status(
                f"{label} - frame {abs_frame} hors timeline, affichée à {reached_abs}{note}"
            )
            return
        mm = data.get("measurement_mm")
        suffix = f" · {float(mm):.1f} mm" if mm else ""
        self._set_status(f"{label} - frame {abs_frame}{suffix}{note}")

    @Slot(int)
    def focusRegistryRow(self, row: int):
        """Clic registre : selection + retour a la frame + surbrillance de la bbox."""
        data = self._registry.row_at(row)
        if not data:
            return
        self.loadRegistryRow(row)
        self._focus_observation(data)

    @Slot()
    def focusSelectedObservation(self):
        if self._selected_row_data:
            self._focus_observation(self._selected_row_data)

    def _resync_selected_registry_row(self) -> None:
        """Le rechargement du registre peut deplacer la ligne selectionnee."""
        idx = self._registry_index_by_ann_id(self._selected_ann_id)
        if idx < 0:
            if self._selected_registry_row != -1:
                self._selected_registry_row = -1
                self.selectedObservationChanged.emit()
            return
        row = self._registry.row_at(idx)
        if row:
            self._remember_selected_row(row, idx)

    def _registry_index_by_ann_id(self, ann_id: str) -> int:
        if not ann_id:
            return -1
        for i in range(self._registry.rowCount()):
            row = self._registry.row_at(i)
            if row and str(row.get("ann_id")) == ann_id:
                return i
        return -1

    @Slot(str, result=bool)
    def focusObservationById(self, ann_id: str) -> bool:
        idx = self._registry_index_by_ann_id((ann_id or "").strip())
        if idx < 0:
            return False
        self.focusRegistryRow(idx)
        return True

    def focus_observation_row(self, data: dict) -> None:
        """Observation venant de l'explorateur global (hors registre de la session)."""
        self._select_observation_data(data)
        self._focus_observation(data)

    @Slot()
    def refreshAll(self):
        self.refreshRegistry()
        self.refreshGrazing()
        self.refreshGallery()
        self._refresh_registry_total()
        self._refresh_session_stats()
        self.loadFishialSettings()
        self.loadEventTypes()
        self._emit_grazing_overlay()

    @Slot()
    def resetForNewSession(self):
        """Réinitialise uniquement l'affichage, jamais la base locale."""
        self._clear_draft()
        self._clear_selected_row()
        self.selectBoxIndex(-1)
        self.selectedTrackLabel = ""
        if self._fish is not None:
            self._fish.clearFocusBox()
        self._media_id = ""
        self._session_id = ""
        self._frame_offset_cached = False
        self._registry.set_rows([])
        self._grazing.set_rows([])
        self._refresh_registry_stats([])
        self._frame_ai_count = 0
        self._frame_manual_count = 0
        self._frame_count_validated = False
        self._set_frame_count_current(False)
        self._current_frame_index = 0
        self._session_max_fish = None
        self._session_stats_summary = ""
        self._session_grazing_count = 0
        self._session_has_tracking = False
        self._session_flag_count = 0
        self._session_flagged_count = 0
        self._session_flag_summary = ""
        self._session_tracked_count = 0
        self.mediaIdChanged.emit()
        self.sessionIdChanged.emit()
        self.registryChanged.emit()
        self.grazingOverlayChanged.emit()
        self.frameAiCountChanged.emit()
        self.frameManualCountChanged.emit()
        self.frameCountValidatedChanged.emit()
        self.currentFrameIndexChanged.emit()
        self.sessionMaxVisibleFishChanged.emit()
        self.sessionStatsSummaryChanged.emit()
        self.sessionRegistryStatsChanged.emit()
        self._set_status("Nouvelle session - chargez une première paire de vidéos")

    @Slot()
    def reloadImportedFishial(self):
        """Le processus d'import a enrichi la base : oublier aussi le cache IA."""
        import fishial_gallery

        fishial_gallery.invalidate_cache()
        self.ensureTaxonomy()
        self.refreshGallery()

    @Slot()
    def refreshGallery(self):
        if not self._db_ok:
            self._gallery.set_rows([])
            self.galleryChanged.emit()
            return
        try:
            import fish_db_stats as fdb

            self._ensure_fv()
            # Bibliothèque de tout le PC : elle agrège toutes les sessions et
            # ne change pas quand on ouvre une autre vidéo ou un autre site.
            rows = fdb.list_species_summary(site=None)
            self._gallery.set_rows(rows)
            self.galleryChanged.emit()
        except Exception as exc:
            self._logs.append(f"[!] Galerie : {exc}")
            self._gallery.set_rows([])
            self.galleryChanged.emit()

    @Slot()
    def refreshRegistry(self):
        if not self._db_ok:
            self._registry.set_rows([])
            self._refresh_registry_stats([])
            return
        try:
            import fish_annotate as fa

            self._ensure_fv()
            rows = fa.list_observations(
                capture_session_id=self._session_id or None,
                media_path=(self._measure.leftVideo or None) if not self._session_id else None,
            )
            self._registry.set_rows(rows)
            self._refresh_registry_stats(rows)
            self._resync_selected_registry_row()
            self.registryChanged.emit()
            scope = "dans la session" if self._session_id else "sur la vidéo"
            self._set_status(f"{len(rows)} observation(s) {scope}")
        except Exception as exc:
            self._logs.append(f"[!] Registre : {exc}")
            self._registry.set_rows([])
            self._refresh_registry_stats([])

    @Slot()
    def refreshGrazing(self):
        path = self._measure.leftVideo
        if not path or not self._db_ok:
            self._grazing.set_rows([])
            self.refreshBehaviorPoints()
            self.selectedEventsChanged.emit()
            self._emit_grazing_overlay()
            return
        try:
            import fish_annotate as fa

            self._ensure_fv()
            rows = fa.list_grazing_intervals(path)
            self._grazing.set_rows(rows)
            self.refreshBehaviorPoints()
            self.selectedEventsChanged.emit()
            self._emit_grazing_overlay()
        except Exception as exc:
            self._logs.append(f"[!] Broute : {exc}")
            self._grazing.set_rows([])
            self.selectedEventsChanged.emit()

    @Slot()
    def refreshBehaviorPoints(self):
        """Relit les marqueurs ponctuels de la video - la source des bouchees.

        Branche sur `Pecks.markersChanged` par `AppController` : sans cela, la
        pastille de la fiche resterait sur le compte d'avant la derniere
        bouchee posee, alors que le bandeau, lui, se remet a jour.
        """
        path = self._measure.leftVideo
        if not path or not self._db_ok:
            if self._behavior_points:
                self._behavior_points = []
                self.selectedEventsChanged.emit()
            return
        try:
            import fish_annotate as fa

            self._ensure_fv()
            rows = fa.list_behavior_points(path)
        except Exception as exc:
            self._logs.append(f"[!] Bouchées : {exc}")
            rows = []
        changed = rows != self._behavior_points
        self._behavior_points = rows
        if changed:
            self.refreshRegistry()
        self.selectedEventsChanged.emit()

    @Slot()
    def saveSession(self):
        """Enregistre la session : ligne `sessions` + metadonnees sur les DEUX medias.

        Le travail part dans un **thread** : l'enregistrement calcule le sha256
        integral des deux videos (plusieurs Go a la premiere attache) et gelait
        l'interface pendant tout ce temps. Les boutons concernes sont grises via
        `busy` et le statut dit ce qui se passe.
        """
        path = self._measure.leftVideo
        if not path:
            self._set_status("Chargez une video d'abord")
            return
        if not self._site.strip():
            self._set_status("Le lieu est obligatoire pour enregistrer la session")
            return
        if not self._date.strip():
            self._set_status("La date est obligatoire pour enregistrer la session")
            return
        if self._busy:
            self._set_status("Enregistrement déjà en cours…")
            return
        payload = {
            "left_path": path,
            "right_path": self._measure.rightVideo,
            "site": self._site,
            "title": self._title,
            "notes": self._notes,
            "date": self._date,
            "session_id": self._session_id,
            "stereo_rmse": paths.stereo_rmse_if_exists(),
        }
        self._set_busy(True)
        self._set_status("Enregistrement de la paire… (empreinte des vidéos)")

        def worker():
            try:
                self._sessionSaved.emit(self._db_save_session(payload))
            except Exception as exc:
                self._sessionFailed.emit(str(exc))

        threading.Thread(target=worker, daemon=True).start()

    def _db_save_session(self, payload: dict) -> dict:
        """Ecriture de la session - thread d'arriere-plan, aucun appel Qt ici."""
        import fish_annotate as fa
        import fish_db_stats as fdb

        self._ensure_fv()
        left_path = payload["left_path"]
        mid = fa.resolve_media_id(left_path, create=True)
        if not mid:
            raise RuntimeError("Echec media en base")

        # L'attache enregistre les DEUX medias et rend leurs identifiants :
        # les resoudre a nouveau ici recalculerait un sha256 de plusieurs Go
        # pour rien.
        session_row = self._upsert_session_row(
            fa, left_path, payload["right_path"], payload, media_id=mid,
        )

        fields = {
            "site": payload["site"],
            "session_title": payload["title"],
            "notes": payload["notes"],
            "session_date": payload["date"],
        }
        ok = fdb.update_session_metadata(mid, **fields)
        right_id = str((session_row or {}).get("right_media_id", "") or "")
        if right_id:
            fdb.update_session_metadata(right_id, **fields)
        return {
            "media_id": mid,
            "ok": bool(ok),
            "session_row": session_row or {},
            "right_media_id": right_id,
        }

    def _finish_session_saved(self, result: dict):
        self._set_busy(False)
        self._media_id = str(result.get("media_id", "") or "")
        self.mediaIdChanged.emit()
        session_row = result.get("session_row") or {}
        session_id = str(session_row.get("session_id", "") or "")
        if session_id:
            self._set_session_id(session_id)
        self._frame_offset_cached = False
        suffix = ""
        if session_row:
            offset = session_row.get("frame_offset")
            suffix = (
                " · paire enregistrée" if result.get("right_media_id")
                else " · vidéo droite absente"
            )
            if offset is not None:
                suffix += f", décalage figé à {offset:+d} img"
            calib = session_row.get("calibration_profile") or ""
            if calib:
                suffix += f" · calibration « {calib} » tracée"
        self._set_status(
            ("Session enregistrée" + suffix) if result.get("ok")
            else "Echec enregistrement"
        )
        self._logs.append(self._status)

    def _finish_session_failed(self, msg: str):
        self._set_busy(False)
        self._set_status(msg)
        self._logs.append(f"[!] Session : {msg}")

    def _upsert_session_row(
        self, fa, left_path: str, right_path: str, payload: dict, *, media_id: str,
    ) -> dict | None:
        """Cree ou met a jour la session de cette paire, et fige l'offset.

        Appele depuis le thread d'ecriture : ne touche a aucun etat Qt (le
        `session_id` retenu remonte par le signal `_sessionSaved`).
        """
        name = (payload["title"] or "").strip() or Path(left_path).stem
        fields = {
            "name": name,
            "site": payload["site"],
            "session_date": payload["date"],
            "notes": payload["notes"],
        }
        session_id = str(payload.get("session_id") or "")
        if not session_id:
            existing = fa.find_session_for_media(media_id)
            session_id = str(existing.get("session_id", "")) if existing else ""
        if session_id:
            fa.update_session(session_id, **fields)
        else:
            created = fa.create_session(**fields)
            session_id = str(created.get("session_id", ""))
        if not session_id:
            return None
        return fa.attach_media_pair(
            session_id, left_path, right_path or "",
            stereo_rmse=payload.get("stereo_rmse"),
        )

    @Slot()
    def addObservationFromSelectedBox(self):
        if self._measure.playing:
            self._set_status("Mettez la lecture en pause pour ajouter un poisson")
            return
        if self._fish is None:
            self._set_status("Controleur IA indisponible")
            return
        count = int(self._fish.lastBoxCount)
        if count <= 0:
            self.selectBoxIndex(-1)
            self._set_status("Encadrez ou detectez d'abord un poisson")
            return
        index = self._selected_box_index
        if index < 0 and count == 1:
            # Un seul choix possible : le sélectionner ici rend le bouton
            # déterministe, y compris après une détection IA sans clic préalable.
            index = 0
            self.selectBoxIndex(index)
        if index < 0 or index >= count:
            self.selectBoxIndex(-1)
            self._set_status(
                "Plusieurs cadres : choisissez le poisson dans « Cadre à ajouter »"
            )
            return
        self._add_observation_at_index(index)

    @Slot(int)
    def addObservationAtBoxIndex(self, box_index: int):
        if self._measure.playing:
            self._set_status("Mettez en pause pour annoter (clic droit)")
            return
        if not self._db_ok:
            self._ensure_fv()
            self._db_ok = self._check_db()
            if self._db_ok:
                self.dbAvailableChanged.emit()
                self.ensureTaxonomy()
        if not self._db_ok:
            self._set_status("Base d'annotations indisponible")
            return
        if box_index < 0:
            return
        self._selected_box_index = box_index
        self.selectedBoxIndexChanged.emit()
        if self._fish is not None:
            self._fish.setSelectedFishIndex(box_index)
        self._set_status("Ajout au registre en cours…")
        self._logs.append(f"Clic droit bbox #{box_index} - enregistrement…")
        self._add_observation_at_index(box_index)

    @Slot()
    def beginDraftFromSelectedBox(self):
        """Ouvre la fiche du cadre courant sans passer par le clic droit."""
        if self._measure.playing:
            self._set_status("Mettez la lecture en pause pour choisir un poisson")
            return
        if self._fish is None:
            self._set_status("Controleur IA indisponible")
            return
        count = int(self._fish.lastBoxCount)
        if count <= 0:
            self.selectBoxIndex(-1)
            self._set_status("Encadrez ou detectez d'abord un poisson")
            return
        index = self._selected_box_index
        if index < 0 and count == 1:
            index = 0
        if index < 0 or index >= count:
            self.selectBoxIndex(-1)
            self._set_status(
                "Plusieurs cadres : choisissez le poisson dans « Cadre à ajouter »"
            )
            return
        self.selectBoxExplicit(index)
        if not self._draft_active and self._selected_ann_id:
            # Ce cadre porte deja une observation : ouvrir une fiche creerait
            # un doublon. Le dire, plutot que de laisser le bouton sans effet
            # visible juste apres un enregistrement.
            self._set_status(
                "Ce poisson est déjà enregistré - corrigez son identification, "
                "ou « Désélectionner » pour passer au suivant"
            )

    @Slot(str, str, str)
    def saveFish(self, family: str, genus: str, species: str):
        """Enregistre la fiche courante, nouvelle ou deja existante."""
        if not self._db_ok:
            self._set_status("Base d'annotations indisponible")
            return
        if self._busy:
            self._set_status("Un enregistrement est déjà en cours")
            return
        if self._measure.playing:
            self._set_status("Mettez la lecture en pause pour enregistrer le poisson")
            return
        if self._draft_active:
            self.editFamily = _normalize_rank_text(family)
            self.editGenus = _normalize_rank_text(genus)
            self.editSpecies = _normalize_rank_text(species)
            self.saveDraftObservation()
        elif self._selected_ann_id:
            if not any(str(value).strip() for value in (family, genus, species)) and not self._row_dirty:
                self._set_status("Poisson déjà enregistré, identification à faire")
                self._clear_saved_measurement_handles()
                return
            self._save_selected_row(family, genus, species)
        else:
            self._set_status("Sélectionnez le poisson pour ouvrir sa fiche")

    @Slot()
    def saveDraftObservation(self):
        """Le geste unique : ecrit la ligne AVEC son taxon et sa longueur.

        L'ancien ordre creait la ligne d'abord et n'identifiait qu'ensuite,
        en deux boutons. Ici « Enregistrer le poisson » commit l'observation,
        puis pose immediatement l'identification affichee (donc relue par un
        humain, qui l'avait sous les yeux) et la longueur du brouillon.
        """
        if not self._draft_active:
            self._set_status(
                "Clic droit sur le cadre du poisson pour ouvrir sa fiche"
            )
            return
        index = int(self._draft_target.get("box_index", -1))
        count = int(self._fish.lastBoxCount) if self._fish is not None else 0
        if index < 0 or index >= count:
            self._clear_draft()
            self._set_status(
                "Le cadre de la fiche n'existe plus - resélectionnez le poisson"
            )
            return
        self._add_observation_at_index(
            index, (self._edit_family, self._edit_genus, self._edit_species),
        )

    @Slot()
    def clearDraftMeasurement(self):
        """Oublie la longueur de la fiche en cours et les points A / B.

        Ne touche jamais une ligne deja enregistree : la longueur d'une
        observation en base se corrige en remesurant, pas en l'effacant depuis
        un bouton de brouillon.
        """
        if not self._draft_active:
            self._set_status("Aucune fiche ouverte - rien à retirer")
            return
        self._invalidate_pending_stereo_measure()
        if self._edit_measurement_mm:
            self._edit_measurement_mm = 0.0
            self.editMeasurementMmChanged.emit()
        self._snapshot_row_state()
        clear_points = getattr(self._measure, "clearPoints", None)
        if callable(clear_points):
            clear_points()
        self._set_status("Longueur retirée de la fiche - « Mesurer » pour recommencer")

    def _clear_saved_measurement_handles(self):
        """Retire les points de travail après succès, sans effacer la longueur enregistrée."""
        clear_points = getattr(self._measure, "clearPoints", None)
        if callable(clear_points):
            clear_points()

    @Slot()
    def clearSelection(self):
        """Referme la fiche : ni ligne, ni cadre, ni mesure a l'ecran.

        C'est la sortie demandee apres un enregistrement : le poisson suivant
        ne doit hériter ni du taxon, ni des points A/B du precedent.
        """
        was_dirty = self._row_dirty
        self._clear_draft()
        self._clear_selected_row()
        if self._fish is not None:
            self._fish.clearFocusBox()
        self.selectBoxIndex(-1)
        self.editFamily = ""
        self.editGenus = ""
        self.editSpecies = ""
        if self._edit_measurement_mm:
            self._edit_measurement_mm = 0.0
            self.editMeasurementMmChanged.emit()
        self._rebuild_taxon_option_lists()
        self._snapshot_row_state()
        clear_points = getattr(self._measure, "clearPoints", None)
        if callable(clear_points):
            clear_points()
        self._set_status(
            "Sélection retirée - modifications non enregistrées abandonnées"
            if was_dirty
            else "Sélection retirée - clic droit sur le poisson suivant"
        )

    def _add_observation_at_index(
        self, idx: int, identification: tuple[str, str, str] | None = None,
    ):
        # Tous les appelants (bouton, clic droit et Python) passent ici : le
        # verrou est donc acquis une seule fois, avant tout lancement de worker.
        #
        # `identification` porte le taxon affiche dans le brouillon. Il voyage
        # avec le payload pour que `_finish_observation_added` sache qu'il ne
        # doit PAS le remplacer par la proposition relue sur la nouvelle ligne.
        #
        # Ajouter une ligne selectionne la NOUVELLE observation et repeuple les
        # champs de taxon depuis celle-ci (voir _finish_observation_added) : une
        # saisie non enregistree serait donc effacee sans un mot, remplacee par
        # la proposition du modele. On refuse plutot, et l'utilisateur tranche.
        if self._row_dirty:
            self._set_status(
                "Identification non enregistrée sur la ligne sélectionnée - "
                "validez-la ou annulez avant d'ajouter un autre poisson"
            )
            return
        if self._busy:
            self._set_status("Un enregistrement est deja en cours")
            return
        if not self._db_ok:
            self._set_status("Base indisponible")
            return
        if self._fish is None:
            self._set_status("Controleur IA indisponible")
            return
        path = self._measure.leftVideo
        if not path:
            self._set_status("Chargez une video")
            return
        box = self._fish.lastBoxAtIndex(idx)
        if not box:
            self._set_status("BBox introuvable - detectez d'abord")
            return
        manual = bool(self._fish.isManualBoxIndex(idx))
        # L'UI travaille en index timeline ; la base, elle, ne stocke plus que
        # l'index ABSOLU du fichier source - le meme referentiel que les pistes.
        timeline_index = self._measure.frameIndex
        abs_frame = self._measure.leftAbsFrameAt(timeline_index)
        try:
            raw = self._enrich_observation_raw(
                {
                    "x1": float(box.get("x1", 0)),
                    "y1": float(box.get("y1", 0)),
                    "x2": float(box.get("x2", 0)),
                    "y2": float(box.get("y2", 0)),
                    "track_id": box.get("trackId", -1),
                    "db_track_id": box.get("dbTrackId", ""),
                    "cls_name": box.get("clsName", "fish"),
                    "species_name": box.get("speciesName", ""),
                    "species_conf": box.get("speciesConf", 0),
                    "conf": box.get("conf", 0),
                },
                timeline_index,
                manual,
            )
        except Exception as exc:
            self._set_status(str(exc))
            self._logs.append(f"[!] {exc}")
            return
        pending_measurement = self._pending_measurement_for_box(path, abs_frame, box)
        payload = {
            "path": path,
            "frame_index": abs_frame,
            "timeline_index": timeline_index,
            "manual": manual,
            "raw": raw,
            # Numero ByteTrack + declencheur d'ecriture des pistes en attente :
            # sans eux, l'observation partait sans lien vers sa piste.
            "external_track_id": int(box.get("trackId", -1) or -1),
            "flush_tracks": self._fish.tracking_flusher(),
            # Seuil de confiance effectivement applique a la detection : lu ici,
            # sur le thread UI, ou vit la propriete.
            "model_conf_threshold": (
                None if manual else float(self._fish.confidence)
            ),
            # Une mesure n'est jointe que si elle vise exactement cette vidéo,
            # cette frame absolue et cette bbox. Une valeur globale serait une
            # corruption silencieuse dès qu'un autre poisson est sélectionné.
            "measurement_mm": pending_measurement,
            "measurement_target": (
                dict(self._pending_stereo_target)
                if pending_measurement is not None
                else None
            ),
            "identification": (
                tuple(str(text) for text in identification)
                if identification is not None
                else None
            ),
        }
        self._set_busy(True)

        def worker():
            try:
                result = self._db_add_observation(payload)
                self._observationAdded.emit(result)
            except Exception as exc:
                self._observationFailed.emit(str(exc))

        threading.Thread(target=worker, daemon=True).start()

    def _enrich_observation_raw(
        self, raw: dict, frame_index: int, manual: bool,
    ) -> dict:
        """Triangulation + galerie Fishial - thread UI uniquement (accès Qt/OpenCV).

        `frame_index` est ici l'index TIMELINE (celui que comprend le service
        de mesure). L'espace image des coordonnees est deduit de l'image
        reellement utilisee : rectifiee si la calibration a repondu, brute
        sinon - jamais suppose.
        """
        raw = dict(raw)
        # Garder le repère et les images utilisés par la mesure courante.
        # Un rechargement déclencherait une redétection au milieu de
        # l'enregistrement et pourrait détacher la longueur du brouillon.
        rect_l, rect_r, P1, P2 = self._measure.rectified_pair(frame_index)
        crop_source = rect_l
        if rect_l is not None:
            raw["image_space"] = "stereo_rectified_left"
            raw["ref_height"], raw["ref_width"] = int(rect_l.shape[0]), int(rect_l.shape[1])
        else:
            raw["image_space"] = "raw"
            raw["ref_width"] = int(self._measure.frameWidth or 0)
            raw["ref_height"] = int(self._measure.frameHeight or 0)
            raw_pair = getattr(self._measure, "raw_pair", None)
            if callable(raw_pair):
                try:
                    crop_source, _ = raw_pair(frame_index)
                except Exception as exc:
                    # La bbox reste enregistrable si un lecteur ponctuellement
                    # indisponible empêche seulement la copie Fishial.
                    self._logs.append(f"[!] Copie locale du poisson : {exc}")
        # Le crop est conservé immédiatement, même sans calibration. Fishial
        # ne dépendra donc plus de la vidéo plusieurs mois plus tard.
        if crop_source is not None:
            try:
                import cv2

                h, w = crop_source.shape[:2]
                x1 = max(0, min(w - 1, int(float(raw.get("x1", 0)))))
                y1 = max(0, min(h - 1, int(float(raw.get("y1", 0)))))
                x2 = max(x1 + 1, min(w, int(float(raw.get("x2", 0)))))
                y2 = max(y1 + 1, min(h, int(float(raw.get("y2", 0)))))
                pad_x = int((x2 - x1) * 0.08)
                pad_y = int((y2 - y1) * 0.08)
                crop = crop_source[
                    max(0, y1 - pad_y):min(h, y2 + pad_y),
                    max(0, x1 - pad_x):min(w, x2 + pad_x),
                ]
                ok, encoded = cv2.imencode(
                    ".jpg", crop, [cv2.IMWRITE_JPEG_QUALITY, 94],
                )
                if ok:
                    raw["_crop_jpeg"] = encoded.tobytes()
            except Exception as exc:
                self._logs.append(f"[!] Copie locale du poisson : {exc}")
        if rect_l is not None and rect_r is not None and P1 is not None and P2 is not None:
            try:
                self._ensure_repo()
                from stereo_utils import project_boxes_to_right_rect, triangulate_bbox_rect_mm

                right_boxes = project_boxes_to_right_rect([raw], rect_l, rect_r)
                tri = triangulate_bbox_rect_mm(
                    rect_l, rect_r, raw, P1, P2, right_boxes=right_boxes,
                )
                if tri is not None:
                    raw["position_x_mm"] = tri[0]
                    raw["position_y_mm"] = tri[1]
                    raw["position_z_mm"] = tri[2]
            except Exception:
                pass
        if not manual and not raw.get("species_name") and crop_source is not None:
            try:
                import fishial_gallery as _fg

                res = _fg.classify_with_gallery(crop_source, raw)
                sp = (res.get("species_name") or "").strip()
                tid = (res.get("taxon_node_id") or "").strip()
                if sp:
                    raw["species_name"] = sp
                if tid:
                    raw["taxon_node_id"] = tid
            except Exception:
                pass
        return raw

    def _resolve_db_track_id(self, payload: dict) -> str:
        """UUID de la piste en base pour cette bbox - thread d'ecriture.

        Le worker de tracking ecrit par lots de 1500 : au moment du clic, la
        piste n'existe generalement pas encore en base et `track_id` restait
        NULL (100 % des lignes a l'audit). On force donc l'ecriture du lot en
        attente, puis on resout le numero ByteTrack en identifiant base.
        """
        import fish_annotate as fa

        existing = str(payload["raw"].get("db_track_id") or "").strip()
        if existing:
            return existing
        external = int(payload.get("external_track_id", -1) or -1)
        flusher = payload.get("flush_tracks")
        if external < 0 or flusher is None:
            return ""
        try:
            if not flusher():
                raise RuntimeError(
                    "Piste en cours d'enregistrement - réessayez dans un instant"
                )
            resolved = fa.resolve_track_db_id(payload["path"], external) or ""
            if not resolved:
                raise RuntimeError(
                    "Piste introuvable après enregistrement - réessayez"
                )
            return resolved
        except Exception as exc:
            self._logs.append(f"[!] Lien observation/piste : {exc}")
            raise

    def _detector_provenance(self) -> dict:
        """Identite du detecteur actif - thread d'ecriture (peut lire les poids).

        Sans elle, impossible de savoir plus tard quel modele avait propose une
        boite, ni de mesurer s'il avait raison.
        """
        try:
            self._ensure_repo()
            import fish_detectors as fd

            return fd.registry().provenance()
        except Exception as exc:
            self._logs.append(f"[!] Provenance du modele : {exc}")
            return {"model_id": None, "model_sha256": None}

    def _db_add_observation(self, payload: dict) -> dict:
        """Insertion DB seule - safe depuis un worker thread (pas d'appels Qt)."""
        import fish_annotate as fa

        self._ensure_fv()
        raw = payload["raw"]
        manual = payload["manual"]
        db_track_id = self._resolve_db_track_id(payload)
        if db_track_id:
            raw["db_track_id"] = db_track_id
        provenance = {} if manual else self._detector_provenance()
        out = fa.add_observation(
            payload["path"],
            payload["frame_index"],
            raw,
            confidence=float(raw.get("conf") or 0) or None,
            track_id=db_track_id or None,
            source="manual" if manual else "model",
            image_space=raw.get("image_space"),
            ref_width=raw.get("ref_width") or None,
            ref_height=raw.get("ref_height") or None,
            frame_ref=FRAME_REF_ABSOLUTE,
            model_id=provenance.get("model_id"),
            model_sha256=provenance.get("model_sha256"),
            model_conf_threshold=payload.get("model_conf_threshold"),
            measurement_mm=payload.get("measurement_mm"),
        )
        crop_bytes = raw.pop("_crop_jpeg", None)
        if crop_bytes:
            try:
                out["crop_path"] = fa.save_annotation_crop(
                    str(out.get("ann_id", "")), crop_bytes,
                )
            except Exception as exc:
                # L'observation est déjà commitée : ne pas faire croire que
                # l'ajout a échoué (et provoquer un doublon au prochain clic).
                out["crop_warning"] = str(exc)
                self._logs.append(f"[!] Copie locale du poisson : {exc}")
        return {
            "out": out,
            "manual": manual,
            "species": raw.get("species_name") or out.get("species") or "?",
            "measurement_target": payload.get("measurement_target"),
            "identification": payload.get("identification"),
        }

    def _finish_observation_added(self, result: dict):
        self._set_busy(False)
        out = result.get("out") or {}
        manual = bool(result.get("manual"))
        try:
            # Consommer avant de focaliser la nouvelle ligne : la focalisation
            # peut réaligner la sélection sur une bbox IA proche et changer la
            # cible courante, alors que le payload porte l'identité exacte qui
            # vient d'être commitée.
            self._consume_pending_stereo_measure(result.get("measurement_target"))
            self.refreshRegistry()
            ann_id = str(out.get("ann_id", ""))
            idx = self._registry_index_by_ann_id(ann_id)
            if idx >= 0:
                selected_row = self._registry.row_at(idx) or {}
                self._select_observation_data(selected_row, idx)
                # L'action principale crée une seule observation, puis place
                # immédiatement l'utilisateur sur cette ligne et sa bbox.
                try:
                    self._focus_observation(selected_row)
                except Exception as exc:
                    # La ligne est DÉJÀ commitée. Annoncer un échec d'ajout
                    # ferait cliquer une seconde fois (donc un doublon), et
                    # ferait surtout perdre l'identification du brouillon qui
                    # reste à écrire juste en dessous.
                    self._logs.append(
                        f"[!] Focalisation de la nouvelle ligne : {exc}"
                    )
            # `identification` vaut None au clic droit, et un triplet (parfois
            # tout vide) quand le geste vient de la fiche : c'est ce qui
            # distingue les deux, pas le fait qu'un rang soit rempli.
            identification = result.get("identification")
            from_draft = identification is not None
            posed_taxon = from_draft and any(
                str(text).strip() for text in identification
            )
            if idx >= 0 and posed_taxon:
                # Chemin brouillon : le taxon qui était SOUS LES YEUX de
                # l'utilisateur au moment du clic fait foi. La sélection de la
                # nouvelle ligne vient de repeupler les champs avec la
                # proposition du modèle : la corriger ici évite d'enregistrer
                # autre chose que ce qui était affiché.
                self.editFamily = identification[0]
                self.editGenus = identification[1]
                self.editSpecies = identification[2]
                self._rebuild_taxon_option_lists()
                self._clear_draft()
                if self._save_selected_row(*identification):
                    # Le panneau doit dire ce qui vient d'être ÉCRIT, pas ce
                    # qu'il reste à faire : « Ligne enregistree - <taxon> »
                    # laissait croire à une simple insertion alors que la
                    # relecture, elle, venait d'être posée au nom de
                    # l'annotateur.
                    self._set_status(self._draft_saved_status(identification))
                self._logs.append(self._status)
                return
            self._clear_draft()
            if manual:
                self.editFamily = ""
                self.editGenus = ""
                self.editSpecies = ""
                mm = out.get("measurement_mm")
                self._edit_measurement_mm = float(mm) if mm is not None else 0.0
                self.editMeasurementMmChanged.emit()
                measure_suffix = (
                    f" · mesure {self._edit_measurement_mm:.1f} mm conservee"
                    if self._edit_measurement_mm > 0 else ""
                )
                self._set_status(
                    "Ligne ajoutée au registre"
                    f"{measure_suffix} - identifiez Famille / Genre / Espèce "
                    "(NA accepté par rang), puis « Enregistrer le poisson »"
                )
            else:
                self.editFamily = out.get("family", "") or ""
                self.editGenus = out.get("genus", "") or ""
                self.editSpecies = out.get("species", "") or out.get("suggested_species", "") or ""
                mm = out.get("measurement_mm")
                self._edit_measurement_mm = float(mm) if mm is not None else 0.0
                self.editMeasurementMmChanged.emit()
                sp = result.get("species") or "?"
                self._set_status(
                    f"Ligne ajoutée au registre - proposition du modèle : {sp}, "
                    "pas encore validée · corrigez ou choisissez NA, "
                    "puis « Enregistrer le poisson »"
                )
            self._rebuild_taxon_option_lists()
            self._snapshot_row_state()
            if from_draft:
                # Trois rangs vides : rien n'a été affirmé, la ligne reste
                # « Non relu ». Le dire ici évite que l'utilisateur croie son
                # poisson identifié parce qu'il a cliqué « Enregistrer ».
                self._set_status(
                    "Poisson enregistré, identification à faire"
                    f"{self._measurement_suffix()}"
                )
                self._clear_saved_measurement_handles()
            self._logs.append(self._status)
        except Exception as exc:
            self._finish_observation_failed(str(exc))

    def _draft_saved_status(self, identification: tuple) -> str:
        """Ce que « Enregistrer le poisson » vient d'écrire, en une phrase.

        Trois issues, trois phrases : un taxon posé, « tout NA » (quelqu'un a
        regardé et tranché : non identifiable, ce n'est pas un oubli), et les
        rangs vides traités par l'appelant.
        """
        texts = [str(item or "").strip() for item in identification]
        if texts and all(_is_na(text) or not text for text in texts):
            head = "Poisson enregistré, non identifiable (NA)"
        else:
            finest = next(
                (text for text in reversed(texts) if text and not _is_na(text)),
                "non identifié",
            )
            head = f"Poisson enregistré et identifié : {finest}"
        return f"{head}{self._measurement_suffix()}"

    def _measurement_suffix(self) -> str:
        if self._edit_measurement_mm > 0:
            return f" · {self._edit_measurement_mm:.1f} mm"
        return ""

    def _finish_observation_failed(self, msg: str):
        self._set_busy(False)
        if "FOREIGN KEY constraint failed" in msg:
            msg = (
                "Enregistrement refuse - taxon ou piste invalide en base. "
                "Reessayez apres « Actualiser » ou validez le taxon manuellement."
            )
        elif "IntegrityError" in msg:
            msg = "Enregistrement refuse - donnees incoherentes en base."
        self._set_status(msg)
        self._logs.append(f"[!] {msg}")

    @Property(bool, notify=registryRowDirtyChanged)
    def registryRowDirty(self):
        return self._row_dirty

    @Property(bool, notify=registryRowDirtyChanged)
    def measurementPersisting(self):
        return self._selected_ann_id in self._measurement_pending_ann_ids

    @Slot()
    def saveSelectedObservation(self):
        self.saveSelectedRow(self._edit_family, self._edit_genus, self._edit_species)

    @Slot(str, str, str)
    def saveSelectedRow(self, family: str, genus: str, species: str):
        self._save_selected_row(family, genus, species)

    def _save_selected_row(self, family: str, genus: str, species: str) -> bool:
        """Écrit l'identification et dit si elle est bien partie en base.

        Le booléen n'est pas cosmétique : l'appelant du brouillon annonce
        « Poisson enregistré et identifié ». Sans lui, un conflit NA refusé ici
        laissait l'annonce s'afficher par-dessus le message d'erreur, et la
        ligne restait « Non relu » sans que personne le sache.
        """
        self._edit_family = _normalize_rank_text(family)
        self._edit_genus = _normalize_rank_text(genus)
        self._edit_species = _normalize_rank_text(species)
        self.editFamilyChanged.emit()
        self.editGenusChanged.emit()
        self.editSpeciesChanged.emit()
        if not self._selected_ann_id:
            self._set_status("Selectionnez une ligne du registre")
            return False
        # NA vaut saisie : le rang est declare non identifie, pas laisse vide.
        ranks = (self._edit_family, self._edit_genus, self._edit_species)
        na_flags = [_is_na(t) for t in ranks]
        texts = ["" if na else t.strip() for na, t in zip(na_flags, ranks)]
        if not any(texts) and not any(na_flags):
            self._set_status("Renseignez Famille, Genre ou Espece - ou NA si non identifie")
            return False
        conflict = _na_conflict(na_flags, texts)
        if conflict:
            self._set_status(conflict)
            self._logs.append(f"[!] {conflict}")
            return False
        saved_id = self._selected_ann_id
        try:
            import fish_annotate as fa

            self._ensure_fv()
            self._ensure_repo()
            self.ensureTaxonomy()
            kwargs: dict = {}
            if any(texts):
                kwargs["family_text"] = texts[0]
                kwargs["genus_text"] = texts[1]
                kwargs["species_text"] = texts[2]
            else:
                # Tout en NA : on rattache au noeud « poisson generique » pour
                # que la ligne compte comme validee non identifiee.
                na_id = fa.unidentified_taxon_id()
                if not na_id:
                    self._set_status("Taxon « non identifie » absent de la base")
                    self._logs.append(f"[!] {self._status}")
                    return False
                kwargs["taxon_node_id"] = na_id
            kwargs["family_is_na"] = na_flags[0]
            kwargs["genus_is_na"] = na_flags[1]
            kwargs["species_is_na"] = na_flags[2]
            if self._edit_measurement_mm > 0:
                kwargs["measurement_mm"] = self._edit_measurement_mm
            resolved = fa.update_observation(saved_id, **kwargs)
            _ = resolved
            self.refreshRegistry()
            self.ensureTaxonomy()
            for i in range(self._registry.rowCount()):
                row = self._registry.row_at(i)
                if row and str(row.get("ann_id")) == saved_id:
                    self.loadRegistryRow(i)
                    break
            parts = [texts[2] or texts[1] or texts[0] or "non identifie"]
            if self._edit_measurement_mm > 0:
                parts.append(f"{self._edit_measurement_mm:.1f} mm")
            self._set_status(f"Ligne enregistree - {' · '.join(parts)}")
            self._logs.append(self._status)
            self._clear_saved_measurement_handles()
            return True
        except Exception as exc:
            self._set_status(str(exc))
            self._logs.append(f"[!] {exc}")
            return False

    @Slot()
    def discardRowEdits(self):
        """Rend les champs a leur etat enregistre - la sortie du verrou d'ajout."""
        if not self._selected_ann_id:
            return
        self.editFamily = self._saved_family
        self.editGenus = self._saved_genus
        self.editSpecies = self._saved_species
        self._edit_measurement_mm = self._saved_measurement_mm
        self.editMeasurementMmChanged.emit()
        self._rebuild_taxon_option_lists()
        self._recompute_row_dirty()
        if self._row_dirty:
            # Seule une ecriture de mesure encore en vol peut maintenir l'etat
            # « modifie » : le dire, plutot que d'annoncer une annulation fausse.
            self._set_status("Mesure en cours d'enregistrement - patientez")
        else:
            self._set_status("Modifications de la ligne annulées")

    @Slot()
    def deleteSelectedObservation(self):
        if not self._selected_ann_id:
            return
        try:
            import fish_annotate as fa

            fa.delete_observation(self._selected_ann_id)
            self._clear_selected_row()
            if self._fish is not None:
                self._fish.clearFocusBox()
            self.refreshRegistry()
            self._set_status("Observation supprimee")
        except Exception as exc:
            self._logs.append(f"[!] {exc}")

    @Slot()
    def promoteSelectedToGallery(self):
        """Ancien nom conservé pour les appels existants, même action par session."""
        self.promoteSelectedSessionToGallery()

    @Property(str, notify=fishialSessionChanged)
    def fishialSessionState(self):
        return self._fishial_session_state

    @Property(str, notify=fishialSessionChanged)
    def fishialSessionPreviewState(self):
        return self._fishial_preview_state

    @Property(bool, notify=fishialSessionChanged)
    def fishialSessionEligible(self):
        return bool(self._fishial_session_result.get("eligible"))

    @Property(str, notify=fishialSessionChanged)
    def fishialSessionSpecies(self):
        return str(self._fishial_session_result.get("taxon_name") or "")

    @Property(str, notify=fishialSessionChanged)
    def fishialSessionScope(self):
        return str(self._fishial_session_result.get("scope_label") or "")

    @Property(int, notify=fishialSessionChanged)
    def fishialSessionCandidates(self):
        return int(self._fishial_session_result.get("candidates") or 0)

    @Property(int, notify=fishialSessionChanged)
    def fishialSessionExploitable(self):
        return int(self._fishial_session_result.get("exploitable") or 0)

    @Property(int, notify=fishialSessionChanged)
    def fishialSessionExisting(self):
        return int(self._fishial_session_result.get("existing") or 0)

    @Property(int, notify=fishialSessionChanged)
    def fishialSessionRejected(self):
        return int(self._fishial_session_result.get("rejected") or 0)

    @Property(int, notify=fishialSessionChanged)
    def fishialSessionAdded(self):
        return int(self._fishial_session_result.get("added") or 0)

    @Property(str, notify=fishialSessionChanged)
    def fishialSessionMessage(self):
        error = str(self._fishial_session_result.get("error") or "")
        if error:
            return error
        reasons = self._fishial_session_result.get("rejection_reasons") or {}
        if reasons:
            labels = {
                "media_absent": "média absent",
                "media_inaccessible": "média inaccessible",
                "frame_inaccessible": "frame inaccessible",
                "image_illisible": "image illisible",
                "crop_invalide": "bbox/crop invalide",
                "embedding_invalide": "embedding invalide",
                "photo_espace_rectifie_incompatible": "photo déclarée rectifiée : utilisez une géométrie brute",
                "media_droit_non_pris_en_charge": "média droit non pris en charge : utilisez le média gauche",
                "espace_geometrie_incompatible": "espace de géométrie incompatible avec Fishial",
                "media_gauche_session_requise": "média gauche de la session requis",
                "calibration_annotation_absente": "calibration de l'annotation absente",
                "calibration_indisponible": "calibration indisponible : rechargez le profil",
                "calibration_incompatible": "calibration incompatible : rectifiez avec le profil enregistré",
            }
            return ", ".join(
                f"{count} {labels.get(key, key)}"
                for key, count in sorted(reasons.items())
            )
        if self._fishial_session_result.get("is_provisional"):
            return "Espèce provisoire validée : classe locale few-shot autorisée."
        return "Enrichissement few-shot local : aucun réentraînement du modèle Fishial."

    def _fishial_request_payload(self, ann_id: str, generation: int) -> dict:
        return {
            "selected_annotation_id": ann_id,
            "capture_session_id": self._session_id or None,
            "fallback_media_id": self._media_id or None,
            "generation": generation,
        }

    @Slot()
    def refreshFishialSessionPreview(self):
        ann_id = self._selected_ann_id
        self._fishial_generation += 1
        generation = self._fishial_generation
        self._fishial_session_state = "idle"
        self._fishial_preview_state = "running" if ann_id else "idle"
        self._fishial_session_result = (
            {"selected_annotation_id": ann_id} if ann_id else {}
        )
        self.fishialSessionChanged.emit()
        if not ann_id:
            return
        status = str(self._selected_row_data.get("identification_status") or "")
        species_id = str(self._selected_row_data.get("species_id") or "")
        if status and status != "identified":
            self._fishial_preview_state = "error"
            self._fishial_session_result = {
                "eligible": False,
                "error": "Observation non relue : validez humainement une espèce avant Fishial",
            }
            self.fishialSessionChanged.emit()
            return
        if not species_id:
            self._fishial_preview_state = "error"
            self._fishial_session_result = {
                "eligible": False,
                "error": "Taxon partiel : une espèce validée est requise",
            }
            self.fishialSessionChanged.emit()
            return
        payload = self._fishial_request_payload(ann_id, generation)

        def worker():
            try:
                import fishial_gallery as fg

                self._ensure_repo()
                self._ensure_fv()
                result = fg.inspect_session_references(
                    payload["selected_annotation_id"],
                    capture_session_id=payload["capture_session_id"],
                    fallback_media_id=payload["fallback_media_id"],
                )
            except Exception as exc:
                result = {"ok": False, "error": str(exc)}
            result["generation"] = generation
            result["selected_annotation_id"] = ann_id
            self._fishialPreviewReady.emit(result)

        threading.Thread(target=worker, daemon=True).start()

    def _finish_fishial_preview(self, result: dict):
        if (
            int(result.get("generation", -1)) != self._fishial_generation
            or str(result.get("selected_annotation_id") or "") != self._selected_ann_id
        ):
            return
        self._fishial_session_result = dict(result)
        if not result.get("ok"):
            self._fishial_preview_state = "error"
        elif result.get("eligible"):
            self._fishial_preview_state = "idle"
        else:
            self._fishial_preview_state = "unavailable"
        self.fishialSessionChanged.emit()

    @Slot()
    def promoteSelectedSessionToGallery(self):
        if not self._selected_ann_id:
            self._set_status("Selectionnez une observation validee")
            return
        if self._fishial_session_state == "running":
            return
        self._fishial_generation += 1
        generation = self._fishial_generation
        ann_id = self._selected_ann_id
        payload = self._fishial_request_payload(ann_id, generation)
        self._fishial_session_state = "running"
        self._fishial_preview_state = "idle"
        self._fishial_session_result = {
            **self._fishial_session_result,
            "selected_annotation_id": ann_id,
        }
        self.fishialSessionChanged.emit()
        self._set_status("Calcul des références Fishial de la session…")

        def worker():
            try:
                import fishial_gallery as fg

                self._ensure_repo()
                self._ensure_fv()
                result = fg.promote_session_references(
                    payload["selected_annotation_id"],
                    capture_session_id=payload["capture_session_id"],
                    fallback_media_id=payload["fallback_media_id"],
                )
            except Exception as exc:
                result = {"ok": False, "error": str(exc)}
            result["generation"] = generation
            result["selected_annotation_id"] = ann_id
            self._fishialPromotionReady.emit(result)

        threading.Thread(target=worker, daemon=True).start()

    def _finish_fishial_promotion(self, result: dict):
        if (
            int(result.get("generation", -1)) != self._fishial_generation
            or str(result.get("selected_annotation_id") or "") != self._selected_ann_id
        ):
            return
        # Une indisponibilité de l'embedder ne remet pas en cause le contexte
        # taxonomique déjà inspecté pour cette même génération.
        self._fishial_session_result = {
            **self._fishial_session_result,
            **result,
        }
        active = int(result.get("added") or 0) + int(result.get("existing") or 0)
        if result.get("ok") and active > 0:
            self._fishial_session_state = "success"
            added = int(result.get("added") or 0)
            existing = int(result.get("existing") or 0)
            rejected = int(result.get("rejected") or 0)
            self._set_status(
                f"Fishial session : {added} ajoutée(s), "
                f"{existing} déjà présente(s), {rejected} ignorée(s)"
            )
            self.refreshGallery()
        else:
            self._fishial_session_state = "error"
            self._set_status(str(
                result.get("error")
                or "Aucune référence Fishial active : tous les éléments ont été rejetés"
            ))
        self._logs.append(self._status)
        self.fishialSessionChanged.emit()

    @Slot(int)
    def promoteGalleryTaxonAt(self, row: int):
        taxon_id = self._gallery.taxon_id_at(row)
        if taxon_id:
            self._start_fishial_project_promotion(taxon_id)

    @Slot()
    def promoteAllNewFishialImages(self):
        self._start_fishial_project_promotion()

    @Property(int, notify=galleryChanged)
    def fishialPendingImages(self):
        return self._gallery.pending_totals()[0]

    @Property(int, notify=galleryChanged)
    def fishialPendingSpecies(self):
        return self._gallery.pending_totals()[1]

    def _start_fishial_project_promotion(self, taxon_id: str | None = None):
        if self._fishial_project_state == "running":
            return
        self._fishial_project_generation += 1
        generation = self._fishial_project_generation
        self._fishial_project_state = "running"
        self._fishial_project_result = {
            "taxon_id": taxon_id,
            "message": "Calcul des nouvelles références Fishial…",
        }
        self.fishialProjectChanged.emit()
        self._set_status(self.fishialProjectMessage)

        def worker():
            try:
                import fishial_gallery as fg

                self._ensure_repo()
                self._ensure_fv()
                if taxon_id:
                    result = fg.promote_taxon_to_gallery(taxon_id)
                else:
                    def progress(current, total, species):
                        self._fishialProjectProgress.emit({
                            "generation": generation,
                            "message": f"Ajout Fishial {current}/{total} — {species}",
                        })
                    result = fg.promote_all_new_references(progress=progress)
            except Exception as exc:
                result = {"ok": False, "error": str(exc)}
            result["generation"] = generation
            result["taxon_id"] = taxon_id
            self._fishialProjectPromotionReady.emit(result)

        threading.Thread(target=worker, daemon=True).start()

    def _update_fishial_project_progress(self, result: dict):
        if (int(result.get("generation", -1)) != self._fishial_project_generation
                or self._fishial_project_state != "running"):
            return
        self._fishial_project_result["message"] = str(result.get("message") or "")
        self._set_status(self.fishialProjectMessage)
        self.fishialProjectChanged.emit()

    def _finish_fishial_project_promotion(self, result: dict):
        if int(result.get("generation", -1)) != self._fishial_project_generation:
            return
        self._fishial_project_result = dict(result)
        summary = (
            "Fishial mis à jour : "
            f"{int(result.get('added') or 0)} ajoutée(s), "
            f"{int(result.get('existing') or 0)} déjà présente(s), "
            f"{int(result.get('rejected') or 0)} ignorée(s)."
        )
        below = int(result.get("species_below_threshold") or 0)
        if below:
            summary += f" {below} espèce(s) sous le seuil, laissée(s) en attente."
        if result.get("ok"):
            self._fishial_project_state = "success"
            message = summary
        else:
            self._fishial_project_state = "error"
            message = str(result.get("error") or "Échec Fishial projet")
            if result.get("added") or result.get("species_processed"):
                message = summary + " " + message
        self._fishial_project_result["message"] = message
        self._set_status(message)
        # Un lot peut avoir réussi en partie : ses nouveaux compteurs doivent
        # aussi se voir lorsque l'une des espèces a échoué.
        self.refreshGallery()
        self._logs.append(self._status)
        self.fishialProjectChanged.emit()

    @Property(str, notify=fishialProjectChanged)
    def fishialProjectState(self):
        return self._fishial_project_state

    @Property(int, notify=fishialProjectChanged)
    def fishialProjectAdded(self):
        return int(self._fishial_project_result.get("added") or 0)

    @Property(int, notify=fishialProjectChanged)
    def fishialProjectExisting(self):
        return int(self._fishial_project_result.get("existing") or 0)

    @Property(int, notify=fishialProjectChanged)
    def fishialProjectRejected(self):
        return int(self._fishial_project_result.get("rejected") or 0)

    @Property(str, notify=fishialProjectChanged)
    def fishialProjectMessage(self):
        return str(self._fishial_project_result.get("message")
                   or self._fishial_project_result.get("error") or "")

    # ── Événements (broute et autres types du catalogue) ────────────────

    @Slot()
    def loadEventTypes(self):
        """Catalogue des comportements annotables (table `event_types`)."""
        if not self._db_ok:
            return
        try:
            import fish_annotate as fa

            self._ensure_fv()
            selected_key = self._current_event_key() if self._event_types else ""
            all_rows = fa.list_event_types(active_only=False, with_usage=True)
            rows = [row for row in all_rows if row.get("isActive")]
            if not rows:
                return
            self._all_event_types = all_rows
            self._event_types = rows
            selected_index = next(
                (i for i, row in enumerate(rows) if row.get("key") == selected_key),
                0,
            )
            self._event_type_index = selected_index
            # Le contenu à un même index peut avoir changé après suppression
            # ou désactivation : les propriétés label/scope doivent se relire.
            self.eventTypeIndexChanged.emit()
            self.behaviorFlagChanged.emit()
            self.selectedEventsChanged.emit()
            self.behaviorTypesChanged.emit()
            self.eventTypesChanged.emit()
        except Exception as exc:
            self._logs.append(f"[!] Types d'événements : {exc}")

    @Property(list, notify=eventTypesChanged)
    def eventTypeOptions(self):
        """Pictogramme + libellé des types actifs, dans l'ordre du catalogue."""
        return [
            f"{row.get('symbol') or '●'} {row.get('label', row.get('key', ''))}"
            for row in self._event_types
        ]

    @Property(list, notify=behaviorTypesChanged)
    def behaviorTypes(self):
        """Catalogue complet pour Préférences, y compris les types désactivés."""
        return list(self._all_event_types)

    @Property(int, notify=eventTypeIndexChanged)
    def eventTypeIndex(self):
        return self._event_type_index

    @Slot(int)
    def setEventTypeIndex(self, index: int):
        index = max(0, int(index))
        if index >= len(self._event_types):
            return
        if self._event_type_index != index:
            self._event_type_index = index
            self.eventTypeIndexChanged.emit()
            self.behaviorFlagChanged.emit()

    @Property(str, notify=eventTypeIndexChanged)
    def eventTypeLabel(self):
        row = self._current_event_type()
        return row.get("label", "") if row else "Broutage"

    @Property(str, notify=eventTypeIndexChanged)
    def eventTypeScope(self):
        return self._current_event_type().get("scope", "interval")

    @Property(str, notify=eventTypeIndexChanged)
    def eventTypeKey(self):
        return self._current_event_key()

    @Property(str, notify=eventTypeIndexChanged)
    def eventTypeSymbol(self):
        return self._current_event_type().get("symbol", "●") or "●"

    @Property(str, notify=eventTypeIndexChanged)
    def eventTypeColor(self):
        return self._current_event_type().get("color", "#f59e0b") or "#f59e0b"

    @Property(bool, notify=behaviorFlagChanged)
    def currentBehaviorFlagged(self):
        key = self._current_event_key()
        return any(
            row.get("key") == key
            for row in self._selected_row_data.get("behaviors", [])
        )

    # `selectedEventsChanged` est émis exactement quand la ligne sélectionnée
    # est (re)lue : `_remember_selected_row`, `_clear_selected_row`, et le
    # rattachement à une piste. C'est donc la notification juste pour tout ce
    # qui décrit la piste du poisson, sans ajouter un signal de plus à tenir
    # à jour dans les mêmes six endroits.
    @Property(str, notify=selectedEventsChanged)
    def selectedTrackDbId(self):
        """Identité en base de la piste du poisson sélectionné, sinon vide."""
        return str(self._selected_row_data.get("track_id") or "")

    @Property(int, notify=selectedEventsChanged)
    def selectedTrackNumber(self):
        """Numéro de piste tel qu'affiché partout ailleurs (#7), -1 si aucune.

        Une piste rattachée mais sans numéro lisible vaut -1 : mieux vaut un
        jalon éteint qu'un « Piste #0 » qui n'existe nulle part.
        """
        if not self.selectedTrackDbId:
            return -1
        external = self._selected_row_data.get("external_track_id")
        try:
            return int(external)
        except (TypeError, ValueError):
            return -1

    @Property(list, notify=selectedEventsChanged)
    def selectedObservationEvents(self):
        """Tous les événements du poisson sélectionné, en une seule liste.

        Trois origines qui ne se rejoignaient nulle part dans l'interface : le
        comportement ponctuel est posé sur l'observation elle-même, le
        comportement à intervalle sur la piste à laquelle elle est rattachée,
        et la bouchée sur une image précise de cette même piste. La fiche du
        registre doit montrer les trois, sinon un poisson marqué semble ne rien
        porter - c'est exactement ce que lisait l'utilisateur : « 3 bouchées »
        dans le bandeau, « Aucun comportement marqué » deux blocs plus bas.

        Les bouchées arrivent AGRÉGÉES (« ● Bouchée × 3 ») : une pastille par
        marqueur remplirait le volet dès la dizaine, et le chercheur compte des
        bouchées, il ne les liste pas ici - c'est le bloc « Bouchées sur piste »
        qui les date une par une.
        """
        data = self._selected_row_data
        if not data:
            return []
        events: list[dict] = [
            {
                "key": str(flag.get("key") or ""),
                "frameAbs": self._selected_row_abs_frame(),
                "label": str(flag.get("label") or flag.get("key") or "?"),
                "symbol": str(flag.get("symbol") or "") or "●",
                "color": str(flag.get("color") or "") or "#f59e0b",
                "detail": "",
                "count": 1,
                "scope": "instant",
            }
            for flag in (data.get("behaviors") or [])
        ]
        track_id = str(data.get("track_id") or "")
        if not track_id:
            return events
        # Un compteur par type ponctuel, dans l'ordre de première apparition :
        # l'ordre du catalogue changerait sous les yeux de l'utilisateur au
        # moindre ajout de type.
        counted: dict[str, dict] = {}
        for row in self._behavior_points:
            if str(row.get("track_db_id") or "") != track_id:
                continue
            key = str(row.get("event_type") or "")
            entry = counted.get(key)
            if entry is None:
                counted[key] = {
                    "label": str(row.get("event_label") or key or "?"),
                    "symbol": str(row.get("event_symbol") or "") or "●",
                    "color": str(row.get("event_color") or "") or "#f59e0b",
                    "detail": "",
                    "count": 1,
                    "scope": "point",
                }
            else:
                entry["count"] += 1
        events.extend(counted.values())
        for row in self._grazing.rows():
            if str(row.get("track_db_id") or "") != track_id:
                continue
            start = row.get("frame_start_abs")
            end = row.get("frame_end_abs")
            if start is None:
                start = row.get("frame_start")
            if end is None:
                end = row.get("frame_end")
            events.append({
                "eventId": str(row.get("event_id") or ""),
                "label": str(row.get("event_label") or row.get("event_type") or "?"),
                "symbol": str(row.get("event_symbol") or "") or "●",
                "color": str(row.get("event_color") or "") or "#f59e0b",
                "detail": f"f{start}→f{end}",
                "count": 1,
                "scope": "interval",
            })
        return events

    def _current_event_type(self) -> dict:
        if 0 <= self._event_type_index < len(self._event_types):
            return self._event_types[self._event_type_index]
        return {}

    def _current_event_key(self) -> str:
        return self._current_event_type().get("key", "grazing") or "grazing"

    @Slot(str, str, result=bool)
    @Slot(str, str, str, result=bool)
    def addBehaviorType(self, label: str, scope: str, symbol: str = "●") -> bool:
        try:
            import fish_annotate as fa

            self._ensure_fv()
            created = fa.create_behavior_type(label, scope, symbol)
            self.loadEventTypes()
            for index, row in enumerate(self._event_types):
                if row.get("key") == created.get("key"):
                    self.setEventTypeIndex(index)
                    break
            self._set_status(f"Comportement « {created['label']} » ajouté")
            return True
        except Exception as exc:
            self._set_status(str(exc))
            self._logs.append(f"[!] Comportement : {exc}")
            return False

    @Slot(str, result=bool)
    def removeBehaviorType(self, type_id: str) -> bool:
        row = next(
            (item for item in self._all_event_types if item.get("id") == type_id),
            None,
        )
        if row is None or row.get("isBuiltin"):
            self._set_status("Un comportement intégré ne peut pas être retiré")
            return False
        # Il y avait ici un garde-fou « ce comportement est figé par le suivi
        # en cours » : le cycle « Début / Fin et analyser » figeait un type
        # d'événement entre son début et sa fin. Ce cycle a été retiré, le
        # suivi conservé ne fige plus aucun comportement, il ne produit qu'une
        # piste. Le garde-fou ne protégeait donc plus rien.
        try:
            import fish_annotate as fa

            deleted = fa.remove_behavior_type(type_id)
            self.loadEventTypes()
            self._set_status(
                "Comportement supprimé" if deleted
                else "Comportement utilisé : désactivé, historique conservé"
            )
            return True
        except Exception as exc:
            self._set_status(str(exc))
            self._logs.append(f"[!] Comportement : {exc}")
            return False

    @Property(list, notify=selectedEventsChanged)
    def selectedPointEventKeys(self):
        return [str(row.get("key") or "")
                for row in self._selected_row_data.get("behaviors", [])]

    @Slot(str, result=bool)
    def toggleSelectedPointEvent(self, key: str) -> bool:
        """Point isolé depuis le même catalogue que les marqueurs sur piste."""
        if self._busy or not self._selected_ann_id:
            self._set_status("Enregistrez et sélectionnez d'abord le poisson.")
            return False
        if key not in self.selectedPointEventKeys and self._selected_row_abs_frame() != self._current_abs_frame():
            self._set_status("Revenez à l'image du poisson enregistré pour y poser le point.")
            return False
        index = next((i for i, row in enumerate(self._event_types)
                      if row.get("key") == key and row.get("scope") == "instant"), -1)
        if index < 0:
            self._set_status("Choisissez un événement dans la liste.")
            return False
        self.setEventTypeIndex(index)
        return self.toggleSelectedBehavior()

    @Slot(result=bool)
    def toggleSelectedBehavior(self) -> bool:
        if not self._selected_ann_id:
            self._set_status("Enregistrez et sélectionnez d'abord un poisson")
            return False
        if self.eventTypeScope != "instant":
            self._set_status("Ce comportement utilise obligatoirement le tracking")
            return False
        try:
            import fish_annotate as fa

            enabled = fa.toggle_observation_behavior(
                self._selected_ann_id, self._current_event_key()
            )
            self.refreshRegistry()
            self.behaviorFlagChanged.emit()
            self.selectedEventsChanged.emit()
            self._emit_grazing_overlay()
            action = "posé" if enabled else "retiré"
            self._set_status(f"{self.eventTypeLabel} {action} sur l'observation")
            return enabled
        except Exception as exc:
            self._set_status(str(exc))
            self._logs.append(f"[!] Comportement ponctuel : {exc}")
            return False

    # ── Écriture d'un intervalle de comportement ────────────────────────
    # FishController appelle completeAssistedBehavior uniquement quand le
    # mode Comportement sur une durée a été choisi au début du suivi.

    @Slot(int, str, int, int, result=bool)
    def completeAssistedGrazing(
        self,
        external_track_id: int,
        track_db_id: str,
        frame_start_abs: int,
        frame_end_abs: int,
    ) -> bool:
        """Crée le TemporalEvent lié, avec le type d'événement courant."""
        return self._complete_assisted_behavior(
            external_track_id,
            track_db_id,
            frame_start_abs,
            frame_end_abs,
            self._current_event_key(),
            self.eventTypeLabel,
        )

    @Slot(int, str, int, int, str, str, result=bool)
    def completeAssistedBehavior(
        self,
        external_track_id: int,
        track_db_id: str,
        frame_start_abs: int,
        frame_end_abs: int,
        event_type: str,
        event_label: str,
    ) -> bool:
        """Enregistre le type reçu, jamais le choix courant du sélecteur."""
        return self._complete_assisted_behavior(
            external_track_id,
            track_db_id,
            frame_start_abs,
            frame_end_abs,
            event_type,
            event_label,
        )

    def _complete_assisted_behavior(
        self,
        external_track_id: int,
        track_db_id: str,
        frame_start_abs: int,
        frame_end_abs: int,
        event_type: str,
        event_label: str,
    ) -> bool:
        db_id = str(track_db_id or "").strip()
        if not db_id:
            self._logs.append("[!] Comportement : identité de piste absente")
            return False
        try:
            import fish_annotate as fa

            self._ensure_fv()
            start, end = sorted((int(frame_start_abs), int(frame_end_abs)))
            fa.add_grazing_interval(
                db_id,
                start,
                end,
                frame_ref=FRAME_REF_ABSOLUTE,
                event_type=(event_type or "grazing").strip() or "grazing",
            )
            self.selectedTrackLabel = str(int(external_track_id))
            self.refreshRegistry()
            self.refreshGrazing()
            self._refresh_session_stats()
            self._logs.append(
                f"{event_label or event_type} enregistré : piste "
                f"#{external_track_id}, f{start}→f{end}"
            )
            return True
        except Exception as exc:
            self._logs.append(f"[!] Comportement piste {external_track_id} : {exc}")
            return False

    @Slot(str, str, result=str)
    def attachObservationToTrack(self, ann_id: str, track_db_id: str) -> str:
        """Donne au poisson du registre la piste que le suivi vient de produire.

        L'intervalle s'écrit sur la piste, jamais sur l'observation : tant que
        l'observation n'en porte aucune, le poisson mesuré et sa broute restent
        deux objets étrangers, à l'écran comme à l'export. On ne réécrit
        cependant jamais un rattachement existant : rediriger en silence une
        observation vers une autre piste fausserait le comptage d'individus.

        Retourne « linked », « already », « conflict », « missing » (rien à
        rattacher) ou « error ».
        """
        ann_id = str(ann_id or "").strip()
        track_db_id = str(track_db_id or "").strip()
        if not ann_id or not track_db_id:
            return "missing"
        try:
            import fish_annotate as fa

            self._ensure_fv()
            outcome = fa.attach_observation_to_track(ann_id, track_db_id)
        except Exception as exc:
            self._logs.append(f"[!] Rattachement observation/piste : {exc}")
            return "error"
        if outcome == fa.LINK_LINKED:
            # La ligne sélectionnée doit relire sa piste : c'est elle que
            # selectedObservationEvents compare aux intervalles enregistrés.
            # refreshRegistry écrit son propre statut, d'où l'ordre.
            self.refreshRegistry()
            self._refresh_session_stats()
            self.selectedEventsChanged.emit()
            self._logs.append(
                f"Observation {ann_id} rattachée à la piste {track_db_id}"
            )
            self._set_status("Poisson du registre rattaché à la piste suivie")
        elif outcome == fa.LINK_CONFLICT:
            self._logs.append(
                f"[!] Observation {ann_id} déjà rattachée à une autre piste : "
                "rattachement refusé"
            )
            self._set_status(
                "Ce poisson appartient déjà à une autre piste : sa fiche n'a "
                "pas été modifiée"
            )
        return outcome

    @Slot(result=str)
    def detachCostSummary(self) -> str:
        """Ce que le détachement coûte, en une phrase, AVANT de le faire.

        Le refus d'écraser un rattachement prévenait l'utilisateur sans lui
        laisser d'issue. L'issue existe maintenant, mais elle ne doit pas être
        un bouton muet : la piste et ses événements survivent, ce sont leurs
        liens avec CE poisson qui sautent, et c'est exactement ce qu'il faut
        annoncer avant le clic.
        """
        if not self._selected_ann_id:
            return ""
        try:
            import fish_annotate as fa

            self._ensure_fv()
            summary = fa.track_link_summary(self._selected_ann_id)
        except Exception as exc:
            self._logs.append(f"[!] Coût du détachement : {exc}")
            return ""
        if not summary.get("track_db_id"):
            return ""
        external = summary.get("external_track_id")
        piste = f"la piste #{external}" if external is not None else "sa piste"
        points = int(summary.get("point_count") or 0)
        intervals = int(summary.get("interval_count") or 0)
        if points <= 0 and intervals <= 0:
            return (
                f"Ce poisson quittera {piste}. Aucun événement n'y est "
                "enregistré : rien d'autre ne change."
            )
        parts = []
        if points > 0:
            parts.append(f"{points} bouchée(s)")
        if intervals > 0:
            parts.append(f"{intervals} intervalle(s)")
        return (
            f"Ce poisson quittera {piste} : {' et '.join(parts)} ne seront "
            "plus rattaché(e)s à lui, à l'écran comme à l'export. Ni la piste "
            "ni ces événements ne sont supprimés."
        )

    @Slot(result=str)
    def detachSelectedObservationFromTrack(self) -> str:
        """Défait le lien poisson / piste de la ligne sélectionnée.

        Retourne « detached », « not_linked », « missing » (aucune ligne
        sélectionnée) ou « error ».
        """
        ann_id = str(self._selected_ann_id or "").strip()
        if not ann_id:
            self._set_status("Sélectionnez d'abord une ligne du registre")
            return "missing"
        try:
            import fish_annotate as fa

            self._ensure_fv()
            outcome = fa.detach_observation_from_track(ann_id)
        except Exception as exc:
            self._logs.append(f"[!] Détachement observation/piste : {exc}")
            self._set_status(str(exc))
            return "error"
        if outcome == fa.LINK_NOT_LINKED:
            self._set_status("Ce poisson n'est rattaché à aucune piste")
            return outcome
        # Même ordre qu'au rattachement : refreshRegistry écrit son propre
        # statut, on pose le nôtre après.
        self.refreshRegistry()
        self.refreshBehaviorPoints()
        self._refresh_session_stats()
        self.selectedEventsChanged.emit()
        self._logs.append(f"Observation {ann_id} détachée de sa piste")
        self._set_status(
            "Poisson détaché de sa piste - la piste et ses événements sont "
            "intacts"
        )
        return outcome

    @Slot(int)
    def deleteGrazingAt(self, row: int):
        eid = self._grazing.event_id_at(row)
        if not eid:
            return
        try:
            import fish_annotate as fa

            fa.delete_grazing_interval(eid)
            self.refreshRegistry()
            self.refreshGrazing()
        except Exception as exc:
            self._logs.append(f"[!] {exc}")

    @Slot(str, result=bool)
    def deleteSelectedDurationEvent(self, event_id: str) -> bool:
        """Retire cette durée du poisson sélectionné, en gardant piste et points."""
        if self._busy:
            return False
        for index, row in enumerate(self._grazing.rows()):
            if (str(row.get("event_id") or "") == event_id
                    and str(row.get("track_db_id") or "") == self.selectedTrackDbId):
                self.deleteGrazingAt(index)
                return not any(str(r.get("event_id") or "") == event_id
                               for r in self._grazing.rows())
        return False

    @Slot()
    def openDbFolder(self):
        """Ouvre le dossier de la base **effective** (racine configurée comprise)."""
        db = paths.annotations_db_path().parent
        if db.is_dir():
            os.startfile(str(db))

    @Slot()
    def exportGrazingDataset(self):
        if not self._media_id:
            self._set_status("Enregistrez la session d'abord")
            return
        out = paths.exports_dir() / "grazing"
        out.mkdir(parents=True, exist_ok=True)
        try:
            self._ensure_fv()
            fv = self._repo() / "annotations"
            if str(fv) not in sys.path:
                sys.path.insert(0, str(fv))
            from scripts.export_grazing_dataset import export_grazing_events, export_tracks_jsonl

            n = export_grazing_events(self._media_id, out / "grazing_events.csv")
            export_tracks_jsonl(self._media_id, out / "tracks_timeline.jsonl")
            self._set_status(f"Export broute : {n} intervalle(s)")
            self._logs.append(str(out))
        except Exception as exc:
            self._set_status(str(exc))
            self._logs.append(f"[!] {exc}")

    @Slot(str)
    def exportTimelineCsv(self, rank: str = "family"):
        if not self._media_id:
            self._set_status("Enregistrez la session d'abord")
            return
        out_dir = paths.exports_dir()
        out_dir.mkdir(parents=True, exist_ok=True)
        try:
            import fish_db_stats as fdb

            self._ensure_fv()
            result = fdb.export_session_timeline_auto_path(self._media_id, out_dir)
            self._set_status(
                f"Chronologie exportée ({result.get('row_count', 0)} lignes)"
            )
            self._logs.append(str(result.get("output_path", out_dir)))
        except Exception as exc:
            self._set_status(str(exc))

    @Slot()
    def exportAbundanceCsv(self):
        """Comptages image par image (MaxN).

        Le bouton « Abondance par image » appelait `exportTimelineCsv` : il
        livrait la chronologie détaillée, pas des comptages. Le format
        `csv_abundance` était déclaré dans le catalogue mais n'était jamais
        écrit - il l'est maintenant, descripteur Frictionless compris.
        """
        if not self._media_id:
            self._set_status("Enregistrez la session d'abord")
            return
        out_dir = paths.exports_dir()
        out_dir.mkdir(parents=True, exist_ok=True)
        try:
            import fish_db_stats as fdb

            self._ensure_fv()
            result = fdb.export_abundance_auto_path(self._media_id, out_dir)
            self._set_status(
                f"Abondance exportée ({result.get('row_count', 0)} image(s) comptée(s))"
            )
            self._logs.append(str(result.get("output_path", out_dir)))
        except Exception as exc:
            self._set_status(str(exc))

    # `exportAndRetrain` a été supprimé en phase 7 : le slot n'exportait ni ne
    # réentraînait rien - il affichait « voir Hub Pro » et rendait la main. Le
    # réentraînement réel passe par `annotations/scripts/retrain_from_db.py`,
    # qui vise l'export scellé du noyau. Aucun appelant QML.

    @Property(int, notify=frameAiCountChanged)
    def frameAiCount(self):
        return self._frame_ai_count

    @Property(int, notify=frameManualCountChanged)
    def frameManualCount(self):
        return self._frame_manual_count

    @frameManualCount.setter
    def frameManualCount(self, v: int):
        v = max(0, int(v))
        self._set_frame_count_current(True)
        if self._frame_manual_count != v:
            self._frame_manual_count = v
            self.frameManualCountChanged.emit()

    @Property(bool, notify=frameCountValidatedChanged)
    def frameCountValidated(self):
        return self._frame_count_validated

    @Property(bool, notify=frameCountCurrentChanged)
    def frameCountCurrent(self):
        return self._frame_count_current

    def _set_frame_count_current(self, value: bool) -> None:
        value = bool(value)
        if self._frame_count_current != value:
            self._frame_count_current = value
            self.frameCountCurrentChanged.emit()

    @Property(float, notify=pendingStereoMeasureMmChanged)
    def pendingStereoMeasureMm(self):
        return self._pending_stereo_mm

    @Property(int, notify=sessionMaxVisibleFishChanged)
    def sessionMaxVisibleFish(self):
        return self._session_max_fish if self._session_max_fish is not None else -1

    @Property(str, notify=sessionStatsSummaryChanged)
    def sessionStatsSummary(self):
        return self._session_stats_summary

    @Property(int, notify=registryTotalCountChanged)
    def registryTotalCount(self):
        return self._registry_total

    @Property(int, notify=sessionRegistryStatsChanged)
    def sessionGrazingCount(self):
        return self._session_grazing_count

    @Property(bool, notify=sessionRegistryStatsChanged)
    def sessionHasTracking(self):
        return self._session_has_tracking

    @Property(int, notify=sessionRegistryStatsChanged)
    def sessionTrackedCount(self):
        return self._session_tracked_count

    @Property(int, notify=sessionRegistryStatsChanged)
    def sessionFlagCount(self):
        return self._session_flag_count

    @Property(int, notify=sessionRegistryStatsChanged)
    def sessionFlaggedCount(self):
        return self._session_flagged_count

    @Property(str, notify=sessionRegistryStatsChanged)
    def sessionFlagSummary(self):
        return self._session_flag_summary

    @Property(int, notify=fishialMinRefsChanged)
    def fishialMinRefs(self):
        return self._fishial_min_refs

    @fishialMinRefs.setter
    def fishialMinRefs(self, v: int):
        v = max(1, min(50, int(v)))
        if self._fishial_min_refs != v:
            try:
                import fish_db_stats as fdb

                self._ensure_fv()
                fdb.set_app_settings(fishial_min_refs=v)
            except Exception as exc:
                self._set_status(f"Impossible d'enregistrer le seuil Fishial : {exc}")
                return
            self._fishial_min_refs = v
            self.fishialMinRefsChanged.emit()
            self.refreshGallery()

    @Property(int, notify=currentFrameIndexChanged)
    def currentFrameIndex(self):
        return self._current_frame_index

    def _on_measure_frame_changed(self):
        if self._measure.playing:
            # Lecture simple : ni invalidation de sélection ni comptage
            # d'abondance (deux requêtes SQLite) par notification de position.
            self._frame_refresh_pending = True
            return
        # Une sélection est propre à une frame. Conserver seulement son index
        # pourrait désigner silencieusement un autre poisson après navigation.
        self._invalidate_pending_stereo_measure()
        self.selectBoxIndex(-1)
        self._set_frame_count_current(False)
        self.refreshFrameAbundance()

    @Property(float, notify=editMeasurementMmChanged)
    def editMeasurementMm(self):
        return self._edit_measurement_mm

    @editMeasurementMm.setter
    def editMeasurementMm(self, v: float):
        v = max(0.0, float(v))
        if abs(self._edit_measurement_mm - v) > 0.001:
            self._edit_measurement_mm = v
            self.editMeasurementMmChanged.emit()
            self._recompute_row_dirty()

    @Slot(float)
    def setEditMeasurementMm(self, v: float):
        self.editMeasurementMm = v

    @Slot(float)
    def applyMeasurementToSelectedRow(self, mm: float):
        if not self._selected_ann_id:
            self._set_status("Selectionnez une ligne du registre")
            return
        self.editMeasurementMm = mm
        self._persist_measurement(self._selected_ann_id, mm)

    def _persist_measurement(self, ann_id: str, mm: float) -> bool:
        """Sérialise et coalesce les mesures par ``ann_id``.

        Une ligne n'a jamais deux transactions concurrentes : si 200 mm est
        demandé pendant l'écriture de 100 mm, le même worker termine 100 puis
        écrit 200. Ainsi la dernière valeur demandée est aussi la dernière
        commitée, quel que soit le temps pris par chaque transaction SQLite.
        Les lignes différentes restent indépendantes.
        """
        mm = max(0.0, float(mm))
        saved_id = str(ann_id)
        if not saved_id:
            return False
        self._measurement_generation += 1
        sequence = self._measurement_generation
        target = dict(self._pending_stereo_target or {})
        media_path = str(self._measure.leftVideo or "")
        request = {
            "sequence": sequence,
            "ann_id": saved_id,
            "measurement_mm": mm,
            "target": target,
            "media_path": media_path,
        }
        self._measurement_latest_seq[saved_id] = sequence
        was_pending = saved_id in self._measurement_pending_ann_ids
        self._measurement_pending_ann_ids.add(saved_id)
        if not was_pending and saved_id == self._selected_ann_id:
            self.registryRowDirtyChanged.emit()
        self._recompute_row_dirty()

        start_worker = False
        with self._measurement_lock:
            state = self._measurement_queues.get(saved_id)
            if state is None:
                state = {"latest": request}
                self._measurement_queues[saved_id] = state
                start_worker = True
            else:
                # Une seule requête en attente suffit : seule la plus récente
                # a une valeur scientifique encore pertinente.
                state["latest"] = request

        if start_worker:
            threading.Thread(
                target=lambda: self._run_measurement_queue(saved_id),
                daemon=True,
            ).start()
        return True

    def _run_measurement_queue(self, ann_id: str) -> None:
        """Worker unique d'une ligne. Ne touche jamais d'objet Qt."""
        while True:
            with self._measurement_lock:
                state = self._measurement_queues.get(ann_id)
                request = state.get("latest") if state else None
                if state is not None:
                    state["latest"] = None
            if request is None:
                return
            try:
                import fish_annotate as fa

                self._ensure_fv()
                fa.update_observation(
                    ann_id,
                    measurement_mm=float(request["measurement_mm"]),
                )
                result = dict(request)
                result["queue_empty"] = self._measurement_queue_is_empty(ann_id)
                self._measurementPersisted.emit(result)
            except Exception as exc:
                result = dict(request)
                result["error"] = str(exc)
                result["queue_empty"] = self._measurement_queue_is_empty(ann_id)
                self._measurementPersistFailed.emit(result)
            if result["queue_empty"]:
                return

    def _measurement_queue_is_empty(self, ann_id: str) -> bool:
        with self._measurement_lock:
            state = self._measurement_queues.get(ann_id)
            if state is None or state.get("latest") is None:
                self._measurement_queues.pop(ann_id, None)
                return True
            return False

    def _invalidate_measurement_operation(self) -> None:
        """Ne détache que l'état d'édition ; une transaction lancée va au bout."""
        self._recompute_row_dirty()

    def _reload_registry_after_measurement(self, ann_id: str) -> None:
        """Actualise une ligne ; reset complet uniquement si elle est introuvable."""
        try:
            import fish_annotate as fa

            self._ensure_fv()
            rows = fa.list_observations(
                media_path=self._measure.leftVideo or None,
                ann_id=ann_id,
                limit=1,
            )
            updated = bool(rows) and self._registry.update_row_by_ann_id(ann_id, rows[0])
            if not updated:
                rows = fa.list_observations(media_path=self._measure.leftVideo or None)
                self._registry.set_rows(rows)
            self._resync_selected_registry_row()
            self.registryChanged.emit()
        except Exception as exc:
            self._logs.append(f"[!] Rafraîchissement mesure : {exc}")

    def _finish_measurement_persisted(self, result: dict) -> None:
        sequence = int(result.get("sequence", -1))
        ann_id = str(result.get("ann_id") or "")
        latest = self._measurement_latest_seq.get(ann_id) == sequence
        if latest and result.get("queue_empty"):
            self._measurement_pending_ann_ids.discard(ann_id)
            if ann_id == self._selected_ann_id:
                self.registryRowDirtyChanged.emit()
        self._reload_registry_after_measurement(ann_id)
        mm = float(result.get("measurement_mm") or 0.0)
        message = f"Mesure {mm:.1f} mm enregistrée sur la ligne {ann_id[:8]}"
        self._logs.append(message)
        if latest and ann_id == self._selected_ann_id:
            idx = self._registry_index_by_ann_id(ann_id)
            if idx >= 0:
                self.loadRegistryRow(idx)
            self._consume_pending_stereo_measure(result.get("target") or None)
            self._set_status(message)
        else:
            self._recompute_row_dirty()

    def _finish_measurement_failed(self, result: dict) -> None:
        sequence = int(result.get("sequence", -1))
        ann_id = str(result.get("ann_id") or "")
        latest = self._measurement_latest_seq.get(ann_id) == sequence
        if latest and result.get("queue_empty"):
            self._measurement_pending_ann_ids.discard(ann_id)
            if ann_id == self._selected_ann_id:
                self.registryRowDirtyChanged.emit()
        self._reload_registry_after_measurement(ann_id)
        self._recompute_row_dirty()
        message = str(result.get("error") or "Échec de l'enregistrement de la mesure")
        contextual = f"Mesure ligne {ann_id[:8]} : {message}"
        self._logs.append(f"[!] {contextual}")
        if latest and ann_id == self._selected_ann_id:
            self._set_status(contextual)

    @staticmethod
    def _bbox_signature(box: dict) -> tuple[float, float, float, float]:
        return tuple(
            round(float(box.get(key, 0.0)), 4)
            for key in ("x1", "y1", "x2", "y2")
        )

    @staticmethod
    def _row_bbox_signature(data: dict) -> tuple[float, float, float, float] | None:
        geom = data.get("geometry") or {}
        if not all(key in geom for key in ("x_min", "y_min", "x_max", "y_max")):
            return None
        return tuple(
            round(float(geom[key]), 4)
            for key in ("x_min", "y_min", "x_max", "y_max")
        )

    def _current_abs_frame(self) -> int:
        return int(self._measure.leftAbsFrameAt(self._measure.frameIndex))

    def _selected_row_abs_frame(self) -> int | None:
        if not self._selected_row_data:
            return None
        value = self._selected_row_data.get("frame_index_abs")
        if value is not None:
            return int(value)
        if self._selected_row_data.get("frame_ref") == FRAME_REF_ABSOLUTE:
            return int(self._selected_row_data.get("frame_index", 0))
        return None

    def _current_stereo_target(self) -> dict | None:
        """Cible scientifique de la distance courante, jamais un index seul."""
        path = str(self._measure.leftVideo or "")
        if not path:
            return None
        abs_frame = self._current_abs_frame()
        # Une ligne explicitement focalisée est la cible scientifique, même
        # si une redétection proche (IoU) a aussi sélectionné une bbox IA dont
        # les coordonnées diffèrent de quelques pixels.
        if self._selected_ann_id and self._selected_row_abs_frame() == abs_frame:
            focus = getattr(self._fish, "focusBox", {}) if self._fish is not None else {}
            if focus and str(focus.get("annId") or "") == self._selected_ann_id:
                return {
                    "video": path,
                    "frame_index": abs_frame,
                    "bbox": self._row_bbox_signature(self._selected_row_data),
                    "ann_id": self._selected_ann_id,
                }
        box = None
        if self._fish is not None and self._selected_box_index >= 0:
            box = self._fish.lastBoxAtIndex(self._selected_box_index)
        if box:
            signature = self._bbox_signature(box)
            target = {
                "video": path,
                "frame_index": abs_frame,
                "bbox": signature,
            }
            if (
                self._selected_ann_id
                and self._selected_row_abs_frame() == abs_frame
                and self._row_bbox_signature(self._selected_row_data) == signature
            ):
                target["ann_id"] = self._selected_ann_id
            return target
        if self._selected_ann_id and self._selected_row_abs_frame() == abs_frame:
            return {
                "video": path,
                "frame_index": abs_frame,
                "bbox": self._row_bbox_signature(self._selected_row_data),
                "ann_id": self._selected_ann_id,
            }
        return None

    def _pending_measurement_for_box(
        self, path: str, abs_frame: int, box: dict,
    ) -> float | None:
        target = self._pending_stereo_target
        if self._pending_stereo_mm <= 0 or not target:
            return None
        if (
            target.get("video") != str(path)
            or target.get("frame_index") != int(abs_frame)
            or target.get("bbox") != self._bbox_signature(box)
        ):
            return None
        return self._pending_stereo_mm

    def _invalidate_pending_stereo_measure(self) -> None:
        if self._pending_stereo_mm <= 0 and self._pending_stereo_target is None:
            return
        self._pending_stereo_mm = 0.0
        self._pending_stereo_target = None
        self.pendingStereoMeasureMmChanged.emit()

    @Slot(int)
    def invalidateMeasurementForBoxEdit(self, box_index: int, box: dict | None = None) -> None:
        """Oublie la longueur attachée à la géométrie avant sa mutation."""
        if int(box_index) != self._selected_box_index:
            return
        self._invalidate_measurement_operation()
        self._invalidate_pending_stereo_measure()
        if self._draft_active and box is not None:
            # Le cadre corrigé reste le même poisson : un second clic droit ne
            # doit pas rouvrir une fiche et écraser la taxonomie déjà saisie.
            self._draft_target["bbox"] = self._bbox_signature(box)
            self.draftChanged.emit()
        if self._draft_active and self._edit_measurement_mm:
            # La fiche affichait encore la longueur de l'ancienne géométrie :
            # l'enregistrement ne l'écrira pas, le panneau ne doit donc plus
            # la montrer.
            self._edit_measurement_mm = 0.0
            self.editMeasurementMmChanged.emit()
            self._snapshot_row_state()
        self._set_status("Bbox modifiée - mesurez à nouveau")

    def _consume_pending_stereo_measure(self, target: dict | None) -> None:
        if target and target == self._pending_stereo_target:
            self._invalidate_pending_stereo_measure()

    @Slot()
    def onStereoMeasureChanged(self):
        mm = float(self._measure.distanceMm)
        if mm <= 0:
            self._invalidate_pending_stereo_measure()
            return
        target = self._current_stereo_target()
        if target is None:
            self._invalidate_pending_stereo_measure()
            self._set_status("Sélectionnez le cadre ou la ligne à mesurer")
            return
        self._pending_stereo_mm = mm
        self._pending_stereo_target = target
        self.pendingStereoMeasureMmChanged.emit()
        ann_id = str(target.get("ann_id") or "")
        if ann_id:
            self.editMeasurementMm = mm
            self._persist_measurement(ann_id, mm)
        elif (
            self._draft_active
            # On compare le CADRE, pas sa géométrie : redimensionner la bbox
            # avant de mesurer ne doit pas détacher la longueur de la fiche.
            and self._selected_box_index == self._draft_target.get("box_index")
            and target.get("frame_index") == self._draft_target.get("frame_index")
        ):
            # La fiche ouverte porte la longueur : « Enregistrer le poisson »
            # ecrira taxon ET mesure en une seule ecriture, sans passer par
            # une ligne d'abord vide.
            self.editMeasurementMm = mm
            self._snapshot_row_state()
            self._set_status(
                f"Longueur {mm:.1f} mm préremplie - vérifiez la taxonomie "
                "puis « Enregistrer le poisson »"
            )
        else:
            self._set_status(
                f"Mesure {mm:.1f} mm liée au cadre - ajoutez-le au registre"
            )

    def _refresh_session_stats(self):
        if not self._media_id or not self._db_ok:
            self._session_max_fish = None
            self._session_stats_summary = ""
            self._session_grazing_count = 0
            self._session_has_tracking = False
            self.sessionMaxVisibleFishChanged.emit()
            self.sessionStatsSummaryChanged.emit()
            self.sessionRegistryStatsChanged.emit()
            return
        try:
            import fish_db_stats as fdb

            self._ensure_fv()
            mx = fdb.session_max_visible_fish(self._media_id)
            self._session_max_fish = mx
            validated_frames = fdb.count_validated_frames(self._media_id)
            detail = fdb.get_session_detail(self._media_id) or {}
            grazing = detail.get("grazing_count", 0)
            species = detail.get("species_count", 0)
            parts = []
            if mx is not None:
                parts.append(f"MaxN {mx}")
            parts.append(f"{validated_frames} frame(s) validée(s)")
            if grazing:
                parts.append(f"{grazing} événement(s)")
            if species:
                parts.append(f"{species} espèce(s)")
            self._session_stats_summary = " · ".join(parts) if parts else ""
            self._session_grazing_count = int(grazing or 0)
            self._session_has_tracking = bool(detail.get("has_tracking"))
            self.sessionMaxVisibleFishChanged.emit()
            self.sessionStatsSummaryChanged.emit()
            self.sessionRegistryStatsChanged.emit()
        except Exception as exc:
            self._logs.append(f"[!] Stats session : {exc}")

    def _refresh_registry_stats(self, rows: list[dict]):
        """Flags posés et lien aux pistes, sur le périmètre du registre.

        Les deux se lisent dans les lignes déjà chargées : les recompter en
        base ferait une requête de plus pour la même vérité.
        """
        labels: Counter[str] = Counter()
        flagged = 0
        tracked = 0
        for row in rows:
            behaviors = row.get("behaviors") or []
            if behaviors:
                flagged += 1
                for flag in behaviors:
                    labels[str(flag.get("label") or flag.get("key") or "?")] += 1
            if row.get("track_id"):
                tracked += 1
        self._session_flag_count = sum(labels.values())
        self._session_flagged_count = flagged
        self._session_tracked_count = tracked
        self._session_flag_summary = " · ".join(
            f"{label} {count}" for label, count in labels.most_common()
        )
        self.sessionRegistryStatsChanged.emit()

    def _refresh_registry_total(self):
        if not self._db_ok:
            self._registry_total = 0
            self.registryTotalCountChanged.emit()
            return
        try:
            import fish_annotate as fa

            self._ensure_fv()
            self._registry_total = fa.count_observations()
            self.registryTotalCountChanged.emit()
        except Exception:
            self._registry_total = 0
            self.registryTotalCountChanged.emit()

    @Slot()
    def refreshFrameAbundance(self):
        if self._measure.playing:
            # Les compteurs de la frame courante n'ont pas de sens pendant la
            # lecture, et chaque appel fait un upsert + un select SQLite.
            self._frame_refresh_pending = True
            return
        if not self._db_ok or not self._media_id:
            self._frame_ai_count = 0
            self._frame_manual_count = 0
            self._frame_count_validated = False
            self._set_frame_count_current(False)
            self.frameAiCountChanged.emit()
            self.frameManualCountChanged.emit()
            self.frameCountValidatedChanged.emit()
            return
        # Comptage indexe sur la frame ABSOLUE du fichier source.
        frame = max(0, self._measure.leftAbsFrame)
        self._current_frame_index = frame
        self.currentFrameIndexChanged.emit()
        ai_ready = bool(
            getattr(self._fish, "frameCountReady", False)
        ) if self._fish else False
        ai = (
            int(getattr(self._fish, "fishCount", 0) or 0)
            if ai_ready else 0
        )
        try:
            import fish_db_stats as fdb

            self._ensure_fv()
            if ai_ready:
                fdb.upsert_frame_abundance_ai(
                    self._media_id, frame, ai, frame_ref=FRAME_REF_ABSOLUTE,
                )
            row = fdb.get_frame_abundance(self._media_id, frame)
            has_current_row = bool(row.get("exists"))
            self._set_frame_count_current(ai_ready or has_current_row)
            self._frame_ai_count = int(row.get("ai_count", ai) or 0) if row else ai
            if row.get("validated"):
                self._frame_manual_count = int(
                    row.get("manual_count") if row.get("manual_count") is not None else row.get("effective_count", ai)
                )
            else:
                self._frame_manual_count = int(row.get("manual_count") or self._frame_ai_count or ai)
            self._frame_count_validated = bool(row.get("validated"))
            self.frameAiCountChanged.emit()
            self.frameManualCountChanged.emit()
            self.frameCountValidatedChanged.emit()
            self._refresh_session_stats()
        except Exception as exc:
            self._logs.append(f"[!] Comptage frame : {exc}")

    @Slot(int)
    def validateFrameCount(self, manual_count: int = -1):
        if not self._media_id:
            self._set_status("Enregistrez la session d'abord")
            return
        if not self._frame_count_current:
            self._set_status(
                "Attendez le comptage de cette frame ou saisissez une valeur"
            )
            return
        count = manual_count if manual_count >= 0 else self._frame_manual_count
        frame = self._current_frame_index
        try:
            import fish_db_stats as fdb

            self._ensure_fv()
            row = fdb.validate_frame_abundance(
                self._media_id,
                frame,
                count,
                ai_count=self._frame_ai_count,
                frame_ref=FRAME_REF_ABSOLUTE,
            )
            self._frame_manual_count = int(row.get("effective_count", count))
            self._frame_count_validated = True
            self.frameManualCountChanged.emit()
            self.frameCountValidatedChanged.emit()
            self._refresh_session_stats()
            self._set_status(f"Frame {frame} : {self._frame_manual_count} spécimen(s) validé(s)")
            self._logs.append(self._status)
        except Exception as exc:
            self._set_status(str(exc))

    @Slot()
    def applyStereoMeasureToSelected(self):
        if not self._selected_ann_id:
            self._set_status("Sélectionnez une ligne du registre")
            return
        if self._pending_stereo_mm <= 0:
            self._set_status("Faites d'abord une mesure stéréo A→B")
            return
        target = self._pending_stereo_target
        if not target or target.get("ann_id") != self._selected_ann_id:
            self._set_status("La mesure ne correspond pas à la ligne sélectionnée")
            return
        self.editMeasurementMm = self._pending_stereo_mm
        self._persist_measurement(self._selected_ann_id, self._pending_stereo_mm)

    # `validateSessionAnnotations` a ete SUPPRIME (phase 1, decision superviseur).
    # Il passait en masse a `validated` toute observation dont un rang etait non
    # vide - y compris les taxons **pre-remplis par l'IA et jamais relus**. La
    # base affichait donc de la vérité terrain qui n'en etait pas, et un
    # reentrainement se serait nourri des erreurs du modele.
    # La validation se fait desormais ligne a ligne (bouton « Valider
    # l'identification », `saveSelectedRow`), qui pose `identification_status`,
    # `reviewed_by` et `reviewed_at`.

    @Slot()
    def loadFishialSettings(self):
        try:
            import fish_db_stats as fdb

            self._ensure_fv()
            settings = fdb.get_app_settings()
            self.fishialMinRefs = int(settings.get("fishial_min_refs", 5))
        except Exception:
            pass

    @Slot()
    def exportSessionCsvForCurrent(self):
        if not self._media_id:
            self._set_status("Session non enregistrée")
            return
        out_dir = paths.exports_dir()
        out_dir.mkdir(parents=True, exist_ok=True)
        try:
            import fish_db_stats as fdb

            self._ensure_fv()
            result = fdb.export_session_timeline_auto_path(self._media_id, out_dir)
            self._set_status(f"CSV exporté ({result.get('row_count', 0)} lignes)")
            self._logs.append(str(result.get("output_path", "")))
        except Exception as exc:
            self._set_status(str(exc))
