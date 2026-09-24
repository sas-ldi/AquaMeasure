from __future__ import annotations

from PySide6.QtCore import Property, QObject, Signal, Slot

from src.controllers.annotator_controller import AnnotatorController
from src.controllers.calibration_controller import CalibrationController
from src.controllers.data_controller import DataController
from src.controllers.db_explorer_controller import DbExplorerController
from src.controllers.detector_controller import DetectorController
from src.controllers.device_controller import DeviceController
from src.controllers.fish_controller import FishController
from src.controllers.measure_controller import MeasureController
from src.controllers.peck_controller import PeckController
from src.controllers.pro_tools_controller import ProToolsController
from src.controllers.session_controller import SessionController
from src.controllers.settings_controller import SettingsController
from src.controllers.storage_controller import StorageController
from src.controllers.sync_controller import SyncController
from src.controllers.tracks_controller import TracksController
from src.util import paths

# Index de page de la barre d'onglets (ordre historique conserve : les pages
# existantes gardent leur numero, la page Sessions prend le suivant).
PAGE_HOME = 0
PAGE_DEVICE = 1
PAGE_SYNC = 2
PAGE_CALIB = 3
PAGE_MEASURE = 4
PAGE_DATA = 5
PAGE_PRO = 6
PAGE_SESSIONS = 7
# Page Parametres / Stockage : ou sont ecrites les donnees, et comment en
# changer. Ajoutee en fin de numerotation, les pages existantes ne bougent pas.
PAGE_STORAGE = 8
PAGE_MAX = PAGE_STORAGE


class AppController(QObject):
    currentPageChanged = Signal()
    projectStateChanged = Signal()
    openCharucoSettingsRequested = Signal()
    openCalibScanSettingsRequested = Signal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self._current_page = 0
        # Avant tout le reste : remonter une calibration restee dans l'ancien
        # camera_parameters/profiles/. Les controleurs qui suivent lisent
        # camera_parameters/ et doivent voir le resultat.
        self._calib_migration_note = paths.migrate_legacy_calib_profile()
        self._settings = SettingsController(self)
        self._sync = SyncController(self)
        self._calib = CalibrationController(self._settings, self)
        self._measure = MeasureController(self)
        self._device = DeviceController(self)
        self._fish = FishController(self._measure, self)
        self._detectors = DetectorController(self)
        self._detectors.set_fish_controller(self._fish)
        self._detectors.activeModelSwitched.connect(self._fish.onDetectorChanged)
        self._data = DataController(self._measure, self._fish, self)
        self._fish.set_data_controller(self._data)
        self._db_explorer = DbExplorerController(self._measure, self._data, self)
        # L'identite d'annotateur est lue avant tout : `author` ne doit plus
        # jamais valoir 'operator' en dur sur une ecriture.
        self._annotator = AnnotatorController(self)
        self._sessions = SessionController(self._measure, self._data, self, self)
        self._pro = ProToolsController(self)
        self._pro.fishialLibraryImported.connect(self._data.reloadImportedFishial)
        self._storage = StorageController(self)
        # Validation de pistes : le prerequis qualite des exports de suivi.
        self._tracks = TracksController(self._measure, self._data, self._fish, self)
        # Marqueurs ponctuels sur piste (bouchees) : un controleur a part, la
        # ou l'intervalle de broutage passe par Fish/Data. Les deux ecrivent
        # dans temporal_events mais ne partagent aucun etat.
        self._pecks = PeckController(self._measure, self._data, self._fish, self)
        self._data.mediaIdChanged.connect(self._pecks.refresh)
        # Une bouchee posee doit remonter dans les pastilles de la fiche, pas
        # seulement dans le bandeau : sans ce fil, l'utilisateur lisait
        # « 3 bouchees » en haut et « Aucun comportement marque » plus bas.
        self._pecks.markersChanged.connect(self._data.refreshBehaviorPoints)
        self._fish.trackSelected.connect(self._data.setSelectedTrackFromId)
        self._data.grazingOverlayChanged.connect(self._fish.refreshOverlay)
        self._measure.distanceMmChanged.connect(self._data.onStereoMeasureChanged)
        self._measure.frameIndexChanged.connect(self._data.refreshFrameAbundance)
        self._measure.leftVideoChanged.connect(self._data.loadSessionMetadata)
        self._fish.fishCountChanged.connect(self._data.refreshFrameAbundance)

        if self._calib_migration_note:
            self._calib.logs.append(self._calib_migration_note)
            self._measure.logs.append(self._calib_migration_note)

        self._sync.syncOffsetChanged.connect(self.refreshProjectState)
        self._sync.syncApplied.connect(self._sessions.refreezePairOffset)
        self._calib.calibrationComplete.connect(self.refreshProjectState)
        self._calib.calibrationCancelled.connect(self.refreshProjectState)
        self.refreshProjectState()
        # La session active survit au redémarrage.
        self._sessions.restoreLastSession()

    @Property(int, notify=currentPageChanged)
    def currentPage(self):
        return self._current_page

    @currentPage.setter
    def currentPage(self, page: int):
        page = max(0, min(page, PAGE_MAX))
        if self._current_page == page:
            return
        self._current_page = page
        self.currentPageChanged.emit()
        if page == PAGE_MEASURE:
            if self._measure.frameCount <= 0:
                self._measure.refresh(self._calib.leftVideo, self._calib.rightVideo)
            else:
                # Une paire deja chargee ne rechargeait pas la calibration :
                # apres une deuxieme calibration (ou un import ZIP), l'onglet
                # Mesure continuait d'utiliser l'ancienne, sans le dire.
                # loadCalibration() ne fait que vider les caches et relire les
                # .npy - c'est bien moins cher que de rouvrir les videos.
                self._measure.loadCalibration()
            self._data.refreshRegistry()
            self._data.refreshGrazing()
            self._data.refreshFrameAbundance()
            self._data.loadSessionMetadata()
            self._data.loadEventTypes()
            self._data.ensureTaxonomy()
            self._pecks.refresh()
            self._fish.refreshOverlay()
        elif page == PAGE_DATA:
            self._data.refreshAll()
            self._db_explorer.refresh()
            self._tracks.refresh()
            self._sessions.refresh()
        elif page == PAGE_SESSIONS:
            self._sessions.refresh()
        elif page == PAGE_STORAGE:
            # Les tailles sont calculees en fil separe : la page s'ouvre
            # immediatement et se remplit ensuite.
            self._storage.refresh()

    def dbExplorer(self) -> DbExplorerController:
        return self._db_explorer

    def sessions(self) -> SessionController:
        return self._sessions

    def annotator(self) -> AnnotatorController:
        return self._annotator

    @Property(bool, notify=projectStateChanged)
    def syncOk(self):
        return paths.sync_exists()

    @Property(bool, notify=projectStateChanged)
    def calibOk(self):
        return paths.calibration_exists()

    @Property(float, notify=projectStateChanged)
    def stereoRmse(self):
        return paths.stereo_rmse_if_exists()

    @Property(str, notify=projectStateChanged)
    def continueHint(self):
        if not self._device.connected:
            return "Connectez la machine de prise de vues"
        if not self.syncOk:
            return "Synchronisez les videos gauche / droite"
        if not self.calibOk:
            return "Lancez la calibration stereo"
        return "Passez a la mesure"

    def sync(self) -> SyncController:
        return self._sync

    def calibration(self) -> CalibrationController:
        return self._calib

    def measure(self) -> MeasureController:
        return self._measure

    def device(self) -> DeviceController:
        return self._device

    def settings(self) -> SettingsController:
        return self._settings

    def fish(self) -> FishController:
        return self._fish

    def detectors(self) -> DetectorController:
        return self._detectors

    def data(self) -> DataController:
        return self._data

    def proTools(self) -> ProToolsController:
        return self._pro

    def storage(self) -> StorageController:
        return self._storage

    def tracks(self) -> TracksController:
        return self._tracks

    def pecks(self) -> PeckController:
        return self._pecks

    @Slot()
    def goToContinue(self):
        if not self._device.connected:
            self.currentPage = PAGE_DEVICE
            return
        if not self.syncOk:
            self.currentPage = PAGE_SYNC
            return
        if not self.calibOk:
            self.currentPage = PAGE_CALIB
            return
        self.currentPage = PAGE_MEASURE

    @Slot()
    def refreshProjectState(self):
        self.projectStateChanged.emit()

    @Slot()
    def goToCalibrationFromSync(self):
        self._sync.saveCurrentVideos()
        self.prepareCalibrationPage()
        self.currentPage = PAGE_CALIB

    @Slot()
    def prepareCalibrationPage(self):
        left = self._sync.leftVideo
        right = self._sync.rightVideo
        if left and right:
            self._sync.saveCurrentVideos()
            self._calib.importFromSync(left, right)
        else:
            self._calib.reloadSavedVideos()
        self._calib.reloadSavedCalibration()

    @Slot()
    def openCharucoSettings(self):
        self.openCharucoSettingsRequested.emit()

    @Slot()
    def openCalibScanSettings(self):
        self.openCalibScanSettingsRequested.emit()
