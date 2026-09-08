from __future__ import annotations

import json
import sys
from datetime import datetime
from pathlib import Path

from PySide6.QtCore import Property, QObject, Signal, Slot
from PySide6.QtWidgets import QFileDialog

from src.controllers.data_controller import DataController
from src.controllers.measure_controller import MeasureController
from src.models.observations_list_model import ObservationsListModel
from src.models.sessions_list_model import SessionsListModel
from src.models.species_summary_model import SpeciesSummaryModel
from src.util import paths
from src.util.log_model import LogModel


class DbExplorerController(QObject):
    filterDateFromChanged = Signal()
    filterDateToChanged = Signal()
    filterSiteChanged = Signal()
    filterSpeciesIndexChanged = Signal()
    detailJsonChanged = Signal()
    selectionTypeChanged = Signal()
    editableChanged = Signal()
    statusTextChanged = Signal()
    busyChanged = Signal()
    siteOptionsChanged = Signal()
    speciesOptionsChanged = Signal()
    viewTabChanged = Signal()
    sessionCountChanged = Signal()
    speciesCountChanged = Signal()
    observationCountChanged = Signal()

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
        self._sessions = SessionsListModel(self)
        self._species = SpeciesSummaryModel(self)
        self._observations = ObservationsListModel(self)
        self._date_from = ""
        self._date_to = ""
        self._filter_site = ""
        self._filter_species_index = 0
        self._site_options: list[str] = ["Tous"]
        self._species_options: list[dict] = [{"label": "Toutes", "id": ""}]
        self._detail_json = "{}"
        self._selection_type = ""
        self._editable = False
        self._status = ""
        self._busy = False
        self._view_tab = 0
        self._selected_media_id = ""
        self._selected_taxon_id = ""
        self._selected_ann_id = ""
        self._selected_row = -1
        self._db_ok = self._check_db()

    def _repo(self) -> Path:
        return paths.app_root()

    def _ensure_fv(self):
        fv = self._repo() / "fish-vision"
        if fv.is_dir() and str(fv) not in sys.path:
            sys.path.insert(0, str(fv))

    def _check_db(self) -> bool:
        try:
            import fish_annotate as fa

            return fa.is_available()
        except ImportError:
            return False

    def _set_status(self, msg: str):
        self._status = msg
        self.statusTextChanged.emit()

    def _set_busy(self, v: bool):
        if self._busy != v:
            self._busy = v
            self.busyChanged.emit()

    def _set_detail(self, data: dict, selection_type: str, editable: bool):
        self._selection_type = selection_type
        self._editable = editable
        self._detail_json = json.dumps(data, indent=2, ensure_ascii=False)
        self.selectionTypeChanged.emit()
        self.editableChanged.emit()
        self.detailJsonChanged.emit()

    def _filter_species_id(self) -> str | None:
        idx = self._filter_species_index
        if idx <= 0 or idx >= len(self._species_options):
            return None
        return self._species_options[idx].get("id") or None

    def _filter_site_value(self) -> str | None:
        site = self._filter_site.strip()
        return site if site and site != "Tous" else None

    @Property(QObject, constant=True)
    def logs(self):
        return self._logs

    @Property(QObject, constant=True)
    def sessions(self):
        return self._sessions

    @Property(QObject, constant=True)
    def species(self):
        return self._species

    @Property(QObject, constant=True)
    def observations(self):
        return self._observations

    @Property(str, notify=filterDateFromChanged)
    def filterDateFrom(self):
        return self._date_from

    @filterDateFrom.setter
    def filterDateFrom(self, v: str):
        if self._date_from != v:
            self._date_from = v
            self.filterDateFromChanged.emit()

    @Property(str, notify=filterDateToChanged)
    def filterDateTo(self):
        return self._date_to

    @filterDateTo.setter
    def filterDateTo(self, v: str):
        if self._date_to != v:
            self._date_to = v
            self.filterDateToChanged.emit()

    @Property(str, notify=filterSiteChanged)
    def filterSite(self):
        return self._filter_site

    @filterSite.setter
    def filterSite(self, v: str):
        if self._filter_site != v:
            self._filter_site = v
            self.filterSiteChanged.emit()

    @Property(int, notify=filterSpeciesIndexChanged)
    def filterSpeciesIndex(self):
        return self._filter_species_index

    @filterSpeciesIndex.setter
    def filterSpeciesIndex(self, v: int):
        v = max(0, int(v))
        if self._filter_species_index != v:
            self._filter_species_index = v
            self.filterSpeciesIndexChanged.emit()

    @Property(str, notify=detailJsonChanged)
    def detailJson(self):
        return self._detail_json

    @detailJson.setter
    def detailJson(self, v: str):
        if self._detail_json != v:
            self._detail_json = v
            self.detailJsonChanged.emit()

    @Property(str, notify=selectionTypeChanged)
    def selectionType(self):
        return self._selection_type

    @Property(bool, notify=editableChanged)
    def editable(self):
        return self._editable

    @Property(str, notify=statusTextChanged)
    def statusText(self):
        return self._status

    @Property(bool, notify=busyChanged)
    def busy(self):
        return self._busy

    @Property(int, notify=viewTabChanged)
    def viewTab(self):
        return self._view_tab

    @viewTab.setter
    def viewTab(self, v: int):
        v = max(0, min(int(v), 2))
        if self._view_tab != v:
            self._view_tab = v
            self.viewTabChanged.emit()

    @Property(int, notify=sessionCountChanged)
    def sessionCount(self):
        return self._sessions.rowCount()

    @Property(int, notify=speciesCountChanged)
    def speciesCount(self):
        return self._species.rowCount()

    @Property(int, notify=observationCountChanged)
    def observationCount(self):
        return self._observations.rowCount()

    @Property("QVariantList", notify=siteOptionsChanged)
    def siteOptions(self):
        return self._site_options

    @Property("QVariantList", notify=speciesOptionsChanged)
    def speciesOptions(self):
        return [o["label"] for o in self._species_options]

    @Property(str, constant=True)
    def selectedMediaId(self):
        return self._selected_media_id

    @Slot()
    def refresh(self):
        if not self._db_ok:
            self._sessions.set_rows([])
            self._species.set_rows([])
            self._observations.set_rows([])
            self.sessionCountChanged.emit()
            self.speciesCountChanged.emit()
            self.observationCountChanged.emit()
            self._set_status("Base indisponible")
            return
        try:
            import fish_db_stats as fdb

            self._ensure_fv()
            sites = fdb.list_sites()
            self._site_options = ["Tous", *sites]
            self.siteOptionsChanged.emit()

            site_val = self._filter_site_value()
            species_rows = fdb.list_species_summary(site=site_val)
            self._species_options = [{"label": "Toutes", "id": ""}]
            for sp in species_rows:
                label = sp.get("scientific_name", "?")
                if sp.get("common_name"):
                    label += f" ({sp['common_name']})"
                self._species_options.append({
                    "label": label,
                    "id": sp.get("taxon_node_id", ""),
                })
            if self._filter_species_index >= len(self._species_options):
                self._filter_species_index = 0
                self.filterSpeciesIndexChanged.emit()
            self.speciesOptionsChanged.emit()

            d_from = self._date_from.strip() or None
            d_to = self._date_to.strip() or None
            sp_id = self._filter_species_id()

            sessions = fdb.list_sessions(
                date_from=d_from,
                date_to=d_to,
                site=site_val,
                species_id=sp_id,
            )
            self._sessions.set_rows(sessions)
            self.sessionCountChanged.emit()

            self._species.set_rows(species_rows)

            obs = fdb.list_annotations(
                site=site_val,
                species_id=sp_id,
                limit=2000,
            )
            if d_from or d_to:
                allowed = {s["media_id"] for s in sessions if s.get("media_id")}
                obs = [o for o in obs if o.get("media_id") in allowed]
            self._observations.set_rows(obs)
            self.speciesCountChanged.emit()
            self.observationCountChanged.emit()

            self._set_detail({}, "", False)
            self._selected_media_id = ""
            self._selected_taxon_id = ""
            self._selected_ann_id = ""
            self._set_status(
                f"{len(sessions)} session(s) · {len(species_rows)} espèce(s) · {len(obs)} observation(s)"
            )
        except Exception as exc:
            self._logs.append(f"[!] Explorateur : {exc}")
            self._set_status(str(exc))

    @Slot(int)
    def selectSession(self, row: int):
        data = self._sessions.row_at(row)
        if not data:
            return
        self._selected_row = row
        self._selected_media_id = str(data.get("media_id", "") or "")
        self._selected_taxon_id = ""
        self._selected_ann_id = ""
        try:
            import fish_db_stats as fdb

            self._ensure_fv()
            payload = fdb.dump_session_json(self._selected_media_id)
            self._set_detail(payload, "session", True)
        except Exception as exc:
            self._set_detail(data, "session", True)
            self._logs.append(f"[!] {exc}")

    @Slot(int)
    def selectSpecies(self, row: int):
        data = self._species.row_at(row)
        if not data:
            return
        self._selected_row = row
        self._selected_taxon_id = str(data.get("taxon_node_id", "") or "")
        self._selected_media_id = ""
        self._selected_ann_id = ""
        self._set_detail(data, "species", True)

    @Slot(int)
    def selectObservation(self, row: int):
        data = self._observations.row_at(row)
        if not data:
            return
        self._selected_row = row
        self._selected_ann_id = str(data.get("ann_id", "") or "")
        self._selected_media_id = str(data.get("media_id", "") or "")
        self._selected_taxon_id = ""
        self._set_detail(data, "observation", False)

    @Slot(str)
    def applyMetadataEdits(self, edited_json: str):
        if not self._editable or not edited_json.strip():
            return
        try:
            data = json.loads(edited_json)
        except json.JSONDecodeError as exc:
            self._set_status(f"JSON invalide : {exc}")
            return
        try:
            import fish_db_stats as fdb

            self._ensure_fv()
            if self._selection_type == "session":
                meta = data.get("metadata") if isinstance(data.get("metadata"), dict) else data
                mid = meta.get("media_id") or self._selected_media_id or data.get("media_id")
                if not mid:
                    self._set_status("media_id manquant")
                    return
                ok = fdb.update_session_metadata(
                    mid,
                    site=meta.get("site", ""),
                    session_title=meta.get("session_title", ""),
                    notes=meta.get("notes", ""),
                    session_date=meta.get("session_date", ""),
                )
                self._set_status("Métadonnées session mises à jour" if ok else "Échec mise à jour")
                self.selectSession(self._selected_row if self._selected_row >= 0 else 0)
            elif self._selection_type == "species":
                tax_id = data.get("taxon_node_id") or self._selected_taxon_id
                name = data.get("scientific_name", "")
                if not tax_id or not name:
                    self._set_status("taxon_node_id ou scientific_name manquant")
                    return
                ok = fdb.update_taxon_scientific_name(tax_id, name)
                self._set_status("Taxon mis à jour" if ok else "Échec mise à jour taxon")
                self.refresh()
            else:
                self._set_status("Sélection non éditable")
            self._logs.append(self._status)
        except Exception as exc:
            self._set_status(str(exc))
            self._logs.append(f"[!] {exc}")

    def _session_video_path(self, media_id: str) -> str:
        import fish_db_stats as fdb

        self._ensure_fv()
        detail = fdb.get_session_detail(media_id)
        if not detail:
            return ""
        return fdb.resolve_video_path(detail.get("rel_path", ""), self._repo())

    def _stereo_pair_warning(self) -> str:
        """La base ne mémorise qu'une vidéo par session (rel_path).

        La vidéo droite retombe donc sur la dernière paire synchronisée : si
        elle n'appartient pas à la même prise, la mesure stéréo serait fausse.
        Mieux vaut le dire que de laisser mesurer une paire incohérente.
        """
        if getattr(self._measure, "videosFromSync", False):
            return ""
        return (
            " - ⚠ vidéo droite issue de la dernière paire synchronisée, "
            "vérifiez la paire avant toute mesure stéréo"
        )

    @Slot()
    def openSelectedInMeasure(self):
        if self._selection_type == "observation":
            self._open_observation_in_measure()
            return
        if not self._selected_media_id:
            self._set_status("Sélectionnez une session")
            return
        try:
            path = self._session_video_path(self._selected_media_id)
            if not path:
                self._set_status("Vidéo de la session introuvable")
                return
            self._measure.refresh(path, "")
            if self._app is not None:
                self._app.currentPage = 4
            if self._data is not None:
                self._data.loadSessionMetadata()
                self._data.refreshRegistry()
                self._data.refreshGrazing()
                self._data.refreshFrameAbundance()
            self._set_status(
                f"Vidéo chargée : {Path(path).name} - page Mesure"
                + self._stereo_pair_warning()
            )
            self._logs.append(self._status)
        except Exception as exc:
            self._set_status(str(exc))

    def _open_observation_in_measure(self):
        """Observation de la base -> page Mesure, sur sa frame, bbox en surbrillance."""
        data = self._observations.row_at(self._selected_row) or {}
        ann_id = str(data.get("ann_id", "") or "")
        media_id = str(data.get("media_id", "") or "")
        if not ann_id or not media_id:
            self._set_status("Sélectionnez une observation")
            return
        try:
            path = self._session_video_path(media_id)
            if not path:
                self._set_status("Vidéo de l'observation introuvable")
                return
            if self._measure.leftVideo != path:
                self._measure.refresh(path, "")
            if self._app is not None:
                self._app.currentPage = 4
            if self._data is None:
                return
            self._data.loadSessionMetadata()
            self._data.refreshRegistry()
            self._data.refreshGrazing()
            if not self._data.focusObservationById(ann_id):
                self._data.focus_observation_row(data)
            # Index absolu du fichier source : la meme frame que celle que la
            # page Mesure va afficher (les lignes historiques sont converties).
            shown_frame = data.get("frame_index_abs")
            if shown_frame is None:
                shown_frame = data.get("frame_index", 0)
            self._set_status(
                f"{data.get('species_label', 'Poisson')} - frame {shown_frame} "
                f"· {Path(path).name}" + self._stereo_pair_warning()
            )
            self._logs.append(self._status)
        except Exception as exc:
            self._set_status(str(exc))

    @Slot()
    def promoteSelectedSpecies(self):
        tax_id = self._selected_taxon_id
        if not tax_id:
            self._set_status("Sélectionnez une espèce")
            return
        try:
            import fishial_gallery as fg

            self._ensure_fv()
            result = fg.promote_taxon_to_gallery(tax_id)
            self._set_status(result.get("message", "Espèce promue"))
            self._logs.append(self._status)
            self.refresh()
        except Exception as exc:
            self._set_status(str(exc))
            self._logs.append(f"[!] {exc}")

    @Slot()
    def exportSelectedSessionCsv(self):
        media_id = self._selected_media_id
        if not media_id:
            self._set_status("Sélectionnez une session")
            return
        out_dir = paths.exports_dir()
        out_dir.mkdir(parents=True, exist_ok=True)
        try:
            import fish_db_stats as fdb

            self._ensure_fv()
            result = fdb.export_session_timeline_auto_path(media_id, out_dir)
            self._set_status(f"CSV exporté ({result.get('row_count', 0)} lignes)")
            self._logs.append(str(result.get("output_path", "")))
        except Exception as exc:
            self._set_status(str(exc))

    def _pick_folder(self, title: str) -> str:
        folder = QFileDialog.getExistingDirectory(None, title, str(paths.exports_dir()))
        return folder or ""

    @Slot()
    def exportSessionJson(self):
        media_id = self._selected_media_id or ""
        if not media_id:
            try:
                import fish_annotate as fa

                path = self._measure.leftVideo
                if path:
                    media_id = fa.resolve_media_id(path, create=False) or ""
            except ImportError:
                pass
        if not media_id:
            self._set_status("Sélectionnez une session ou chargez une vidéo")
            return
        folder = self._pick_folder("Dossier export JSON session")
        if not folder:
            return
        try:
            import fish_db_stats as fdb

            self._ensure_fv()
            payload = fdb.dump_session_json(media_id)
            out = Path(folder) / f"session_{media_id[:8]}_{datetime.now():%Y%m%d_%H%M%S}.json"
            out.write_text(json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8")
            self._set_status(f"Export JSON : {out.name}")
            self._logs.append(str(out))
        except Exception as exc:
            self._set_status(str(exc))

    @Slot()
    def exportFilteredJson(self):
        folder = self._pick_folder("Dossier export JSON filtré")
        if not folder:
            return
        try:
            import fish_db_stats as fdb

            self._ensure_fv()
            payload = fdb.dump_filtered_export(
                site=self._filter_site_value(),
                species_id=self._filter_species_id(),
                date_from=self._date_from.strip() or None,
                date_to=self._date_to.strip() or None,
            )
            out = Path(folder) / f"export_filtre_{datetime.now():%Y%m%d_%H%M%S}.json"
            out.write_text(json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8")
            self._set_status(f"Export filtré : {out.name}")
            self._logs.append(str(out))
        except Exception as exc:
            self._set_status(str(exc))

    # La sauvegarde de la base vivait ici **et** dans `StorageController`
    # (page Paramètres), avec deux implémentations parallèles : seule la
    # seconde rafraîchissait l'alerte « dernière sauvegarde » affichée à
    # l'utilisateur. Phase 7 : une seule implémentation subsiste,
    # `Storage.backupNow()`, appelée par les deux boutons (onglet Exports et
    # page Paramètres). Le point commun était déjà `storage_config.record_backup`.
