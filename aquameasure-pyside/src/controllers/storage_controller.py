"""Page Paramètres / Stockage - « où c'est enregistré ».

Demande explicite du client : « un espace où je vois où c'est enregistré, une
vraie application claire », et l'option retenue est **voir ET choisir** -
afficher tous les chemins réels, ouvrir les dossiers, et pouvoir déplacer la
racine des données (disque externe, dossier synchronisé…).

Trois principes tenus ici :

1. **Rien n'est calculé sur le thread d'interface.** Mesurer la taille d'un
   dossier d'exports de plusieurs gigaoctets gèlerait la fenêtre : les tailles
   sont calculées dans un fil séparé, affichées « calcul… » puis remplacées,
   et mémorisées pour la session.
2. **Aucun déplacement silencieux.** Changer de racine propose une **copie**,
   explicitement confirmée, avec progression. L'ancien emplacement n'est
   **jamais** supprimé automatiquement : c'est à l'utilisateur de faire le
   ménage une fois qu'il a vérifié.
3. **Chaque emplacement se dit.** Un InfoDot explique ce que contient le
   dossier et ce qu'on perd s'il disparaît.
"""

from __future__ import annotations

import os
import shutil
import sys
import threading
from datetime import datetime
from pathlib import Path

from PySide6.QtCore import Property, QObject, Signal, Slot
from PySide6.QtWidgets import QFileDialog

from src.util import paths, storage_config
from src.util.log_model import LogModel

# Au-delà, la page passe l'état de sauvegarde en avertissement visuel.
BACKUP_WARN_DAYS = 7


def _fmt_mtime(path: Path) -> str:
    try:
        stamp = datetime.fromtimestamp(path.stat().st_mtime)
    except OSError:
        return "-"
    return stamp.strftime("%d/%m/%Y à %H:%M")


def _fmt_stamp(iso: str) -> str:
    if not iso:
        return ""
    try:
        return datetime.fromisoformat(iso).strftime("%d/%m/%Y à %H:%M")
    except ValueError:
        return iso


class StorageController(QObject):
    pathsChanged = Signal()
    sizesChanged = Signal()
    backupChanged = Signal()
    busyChanged = Signal()
    statusTextChanged = Signal()
    progressChanged = Signal()
    # La page demande la confirmation ; le dialogue vit dans le QML (idiome
    # `openDialogRequested` déjà utilisé par AnnotatorController / Detectors).
    rootProposalRequested = Signal(str)
    rootApplied = Signal(str)
    restartNeededChanged = Signal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self._logs = LogModel(self)
        self._status = ""
        self._busy = False
        self._progress = ""
        self._pending_root = ""
        # Vrai dès qu'on a changé de racine : le moteur SQLAlchemy, les
        # identifiants de média et les registres déjà chargés pointent encore
        # sur l'ancien emplacement. Le dire plutôt que d'afficher une page
        # cohérente sur des données qui ne le sont pas.
        self._restart_needed = False
        # Cache des tailles pour la session : {clé emplacement: texte}
        self._sizes: dict[str, str] = {}
        self._computing = False
        self._exports_count = -1
        self._last_export = ""
        self._calib_profiles = -1

    # ── utilitaires ───────────────────────────────────────────────────

    def _set_status(self, msg: str) -> None:
        self._status = msg
        self.statusTextChanged.emit()

    def _set_busy(self, value: bool) -> None:
        if self._busy != value:
            self._busy = value
            self.busyChanged.emit()

    def _set_progress(self, text: str) -> None:
        self._progress = text
        self.progressChanged.emit()

    def _ensure_fv(self) -> None:
        fv = paths.app_root() / "fish-vision"
        if fv.is_dir() and str(fv) not in sys.path:
            sys.path.insert(0, str(fv))

    # ── propriétés : racine et configuration ──────────────────────────

    @Property(QObject, constant=True)
    def logs(self):
        return self._logs

    @Property(str, notify=statusTextChanged)
    def statusText(self):
        return self._status

    @Property(bool, notify=busyChanged)
    def busy(self):
        return self._busy

    @Property(str, notify=progressChanged)
    def progressText(self):
        return self._progress

    @Property(str, notify=pathsChanged)
    def dataRoot(self):
        return str(storage_config.data_root())

    @Property(bool, notify=pathsChanged)
    def isDefaultRoot(self):
        return storage_config.is_default_root()

    @Property(str, notify=pathsChanged)
    def configPath(self):
        return str(storage_config.config_path())

    @Property(bool, notify=pathsChanged)
    def configExists(self):
        return storage_config.config_path().is_file()

    @Property(str, notify=pathsChanged)
    def pendingRoot(self):
        return self._pending_root

    @Property(bool, notify=restartNeededChanged)
    def restartNeeded(self):
        """La racine a changé pendant cette exécution : il faut redémarrer.

        Changer `storage.json` suffit aux chemins, mais pas aux objets déjà
        vivants : le moteur de base est ouvert sur l'ancien fichier et les
        pages ont chargé leur registre depuis lui.
        """
        return self._restart_needed

    def _set_restart_needed(self, value: bool) -> None:
        if self._restart_needed != value:
            self._restart_needed = value
            self.restartNeededChanged.emit()

    # ── propriétés : les quatre emplacements ──────────────────────────

    @Property(str, notify=pathsChanged)
    def dbPath(self):
        return str(paths.annotations_db_path())

    @Property(bool, notify=pathsChanged)
    def dbExists(self):
        return paths.annotations_db_path().is_file()

    @Property(str, notify=pathsChanged)
    def dbModified(self):
        db = paths.annotations_db_path()
        return _fmt_mtime(db) if db.is_file() else "-"

    @Property(str, notify=sizesChanged)
    def dbSize(self):
        return self._sizes.get("database", "")

    @Property(str, notify=pathsChanged)
    def calibrationsPath(self):
        return str(paths.camera_params_dir())

    @Property(str, notify=sizesChanged)
    def calibrationsSize(self):
        return self._sizes.get("calibrations", "")

    @Property(str, notify=pathsChanged)
    def calibrationProfile(self):
        """Resume de l'unique calibration utilisee (date, origine, RMSE)."""
        return paths.calibration_summary() or "aucune"

    @Property(int, notify=sizesChanged)
    def calibrationProfileCount(self):
        return self._calib_profiles

    @Property(str, notify=pathsChanged)
    def exportsPath(self):
        return str(paths.exports_dir())

    @Property(str, notify=sizesChanged)
    def exportsSize(self):
        return self._sizes.get("exports", "")

    @Property(int, notify=sizesChanged)
    def exportCount(self):
        return self._exports_count

    @Property(str, notify=sizesChanged)
    def lastExport(self):
        return self._last_export

    @Property(str, notify=pathsChanged)
    def mediaPath(self):
        return str(paths.media_dir())

    @Property(str, notify=sizesChanged)
    def mediaSize(self):
        return self._sizes.get("media", "")

    # ── propriétés : sauvegarde ───────────────────────────────────────

    @Property(str, notify=backupChanged)
    def lastBackupAt(self):
        return _fmt_stamp(storage_config.last_backup()["at"])

    @Property(str, notify=backupChanged)
    def lastBackupPath(self):
        return storage_config.last_backup()["path"]

    @Property(bool, notify=backupChanged)
    def backupOverdue(self):
        """Vrai si aucune sauvegarde n'a jamais eu lieu, ou depuis > 7 jours."""
        age = storage_config.backup_age_days()
        return age is None or age > BACKUP_WARN_DAYS

    @Property(str, notify=backupChanged)
    def backupSummary(self):
        info = storage_config.last_backup()
        if not info["at"]:
            return "Aucune sauvegarde enregistrée - la base n'a jamais été copiée ailleurs."
        age = storage_config.backup_age_days()
        when = _fmt_stamp(info["at"])
        if age is not None and age > BACKUP_WARN_DAYS:
            return f"Dernière sauvegarde le {when} - il y a plus de {int(age)} jours."
        return f"Dernière sauvegarde le {when}."

    # ── actions ───────────────────────────────────────────────────────

    @Slot()
    def refresh(self):
        """Relit les chemins, puis relance le calcul des tailles en fil séparé."""
        storage_config.invalidate_cache()
        self.pathsChanged.emit()
        self.backupChanged.emit()
        self._start_size_scan()

    @Slot()
    def recompute(self):
        """Force un nouveau calcul des tailles (le cache de session est vidé)."""
        self._sizes.clear()
        self.sizesChanged.emit()
        self._start_size_scan()

    def _start_size_scan(self) -> None:
        if self._computing:
            return
        self._computing = True
        for key in ("database", "calibrations", "exports", "media"):
            self._sizes.setdefault(key, "calcul…")
        self.sizesChanged.emit()
        threading.Thread(target=self._scan_sizes, daemon=True).start()

    def _scan_sizes(self) -> None:
        """Fil séparé : `os.walk` sur des dizaines de Go gèlerait la fenêtre."""
        try:
            sizes = {
                "database": storage_config.human_size(
                    storage_config.directory_size(paths.annotations_db_path())
                ),
                "calibrations": storage_config.human_size(
                    storage_config.directory_size(paths.camera_params_dir())
                ),
                "exports": storage_config.human_size(
                    storage_config.directory_size(paths.exports_dir())
                ),
                "media": storage_config.human_size(
                    storage_config.directory_size(paths.media_dir())
                ),
            }
            profiles = self._count_calib_profiles()
            exports_count, last_export = self._read_export_runs()
        except Exception as exc:  # noqa: BLE001 - un affichage ne casse rien
            self._logs.append(f"[!] Calcul des tailles : {exc}")
            # Sans cette remise à zéro, la page resterait bloquée sur
            # « calcul… » et laisserait croire qu'un travail est en cours.
            sizes = dict.fromkeys(
                ("database", "calibrations", "exports", "media"), "-"
            )
            profiles, exports_count, last_export = -1, -1, ""
        finally:
            self._computing = False
        self._sizes.update(sizes)
        self._calib_profiles = profiles
        self._exports_count = exports_count
        self._last_export = last_export
        self.sizesChanged.emit()

    def _count_calib_profiles(self) -> int:
        """Jeux de calibration presents : l'actif, plus les anciens profils.

        Les dossiers `profiles/` ne servent plus (une seule calibration, a la
        racine) mais on continue de les compter tant qu'ils occupent de la
        place sur le disque : cette page sert justement a le voir.
        """
        base = paths.camera_params_dir()
        if not base.is_dir():
            return 0
        count = 1 if paths.calib_profile_complete() else 0
        return count + len(paths.legacy_profile_dirs())

    def _read_export_runs(self) -> tuple[int, str]:
        """Nombre d'exports tracés et description du dernier (date + format)."""
        try:
            self._ensure_fv()
            from sqlalchemy import func, select

            from src.annodb.connection import session_scope
            from src.annodb.models import ExportRun

            with session_scope() as session:
                total = session.scalar(select(func.count(ExportRun.id))) or 0
                last = session.scalars(
                    select(ExportRun).order_by(ExportRun.created_at.desc()).limit(1)
                ).first()
                if last is None:
                    return int(total), ""
                when = last.created_at
                when_text = (
                    when.strftime("%d/%m/%Y à %H:%M")
                    if hasattr(when, "strftime") else str(when)
                )
                return int(total), f"{when_text} · {last.format}"
        except Exception as exc:  # noqa: BLE001 - base absente = page lisible
            self._logs.append(f"[!] Lecture des exports : {exc}")
            return -1, ""

    @Slot(str)
    def openPath(self, path: str):
        """Ouvre un dossier dans l'explorateur (le parent si c'est un fichier)."""
        if not path:
            return
        target = Path(path)
        if target.is_file():
            target = target.parent
        if not target.is_dir():
            self._set_status(f"Dossier inexistant : {target}")
            return
        try:
            os.startfile(str(target))  # noqa: S606 - explorateur du système
        except OSError as exc:
            self._set_status(str(exc))

    @Slot()
    def pickDataRoot(self):
        """Choix du dossier, validation, puis **proposition** de copie.

        Rien n'est appliqué ici : la racine ne change qu'après confirmation
        explicite dans le dialogue (`applyDataRoot`).
        """
        folder = QFileDialog.getExistingDirectory(
            None, "Choisir la racine des données AquaMeasure", str(storage_config.data_root())
        )
        if not folder:
            return
        try:
            validated = storage_config.validate_root(Path(folder))
        except storage_config.StorageRootError as exc:
            self._set_status(str(exc))
            self._logs.append(f"[!] {exc}")
            return
        self._pending_root = str(validated)
        self.pathsChanged.emit()
        self.rootProposalRequested.emit(str(validated))

    @Slot(bool)
    def applyDataRoot(self, copy_existing: bool):
        """Applique la racine proposée, en copiant d'abord si demandé.

        La copie tourne dans un fil : recopier une base et des exports peut
        durer plusieurs minutes. **L'ancien emplacement reste intact.**
        """
        root = self._pending_root
        if not root:
            self._set_status("Aucun dossier sélectionné.")
            return
        if self._busy:
            self._set_status("Un traitement est déjà en cours.")
            return
        self._set_busy(True)
        self._set_progress("Préparation…")
        threading.Thread(
            target=self._apply_root_worker, args=(Path(root), bool(copy_existing)),
            daemon=True,
        ).start()

    def _apply_root_worker(self, root: Path, copy_existing: bool) -> None:
        errors: list[str] = []
        try:
            if copy_existing:
                def on_progress(name: str, index: int, total: int) -> None:
                    self._set_progress(f"Copie {index}/{total} - {name}")

                report = storage_config.copy_data_tree(root, progress=on_progress)
                errors = list(report["errors"])
                self._logs.append(
                    f"Copie vers {root} : {report['copied']}/{report['file_count']} "
                    f"fichier(s), {storage_config.human_size(report['copied_bytes'])}"
                )
                for line in errors[:10]:
                    self._logs.append(f"[!] {line}")
            storage_config.set_data_root(root)
            self._set_progress("")
            if not copy_existing:
                self._set_status(
                    f"Racine des données : {root}. Rien n'a été copié - "
                    "l'application repart d'une base vide à cet emplacement."
                )
            elif errors:
                self._set_status(
                    f"Racine des données : {root}. Copie terminée avec "
                    f"{len(errors)} erreur(s) - l'ancien emplacement n'a pas "
                    "été touché, vérifiez avant de le supprimer."
                )
            else:
                self._set_status(
                    f"Racine des données : {root}. L'ancien emplacement est "
                    "conservé tel quel, à vous de le ranger."
                )
            self._logs.append(f"Racine des données : {root}")
            self._set_restart_needed(True)
            self.rootApplied.emit(str(root))
        except Exception as exc:  # noqa: BLE001 - remonté à l'écran
            self._set_status(str(exc))
            self._logs.append(f"[!] Changement de racine : {exc}")
        finally:
            self._pending_root = ""
            self._set_busy(False)
            self._set_progress("")
            self._sizes.clear()
            self.pathsChanged.emit()
            self.refresh()

    @Slot()
    def resetDataRoot(self):
        """Revient à l'emplacement par défaut (racine du dépôt). Ne copie rien."""
        if self._busy:
            return
        try:
            storage_config.set_data_root(None)
            self._set_status(
                "Racine des données remise à l'emplacement par défaut. "
                "Les fichiers de l'ancienne racine sont conservés."
            )
            self._set_restart_needed(True)
            self.rootApplied.emit(str(storage_config.data_root()))
            self.refresh()
        except OSError as exc:
            self._set_status(str(exc))

    @Slot()
    def backupNow(self):
        """Copie la base dans un dossier choisi et trace la sauvegarde.

        **Unique implémentation de la sauvegarde** depuis la phase 7 : le
        bouton « Sauvegarder la base (.db) » de l'onglet Exports appelle ce
        même slot. Il en existait une seconde dans `DbExplorerController`, qui
        copiait bien le fichier mais ne rafraîchissait pas l'alerte
        « dernière sauvegarde » de cette page - deux boutons, deux
        comportements, un seul visible par l'utilisateur.

        Le journal WAL est rabattu avant la copie : la base tourne en
        `journal_mode=WAL`, et copier le seul `.db` sans checkpoint sauvegarde
        un état antérieur aux dernières annotations. Un échec de checkpoint
        n'annule pas la sauvegarde - il est signalé, la copie a lieu quand même.
        """
        db = paths.annotations_db_path()
        if not db.is_file():
            self._set_status(f"Base introuvable : {db}")
            return
        folder = QFileDialog.getExistingDirectory(
            None, "Dossier de sauvegarde de la base", str(storage_config.data_root())
        )
        if not folder:
            return
        self._checkpoint_wal(db)
        try:
            out = Path(folder) / f"fish_annotations_{datetime.now():%Y%m%d_%H%M%S}.db"
            shutil.copy2(db, out)
            storage_config.record_backup(out)
            self._set_status(f"Sauvegarde : {out}")
            self._logs.append(str(out))
            self.backupChanged.emit()
        except OSError as exc:
            self._set_status(str(exc))
            self._logs.append(f"[!] Sauvegarde : {exc}")

    def _checkpoint_wal(self, db: Path) -> None:
        """Rabat le journal WAL dans le `.db` - jamais bloquant pour la copie."""
        try:
            self._ensure_fv()
            from src.annodb.export_core import wal_checkpoint

            if not wal_checkpoint(db):
                self._logs.append(
                    "[!] Journal WAL non rabattu avant la sauvegarde : la copie "
                    "peut ignorer les toutes dernières écritures."
                )
        except Exception as exc:  # noqa: BLE001 - la sauvegarde prime
            self._logs.append(f"[!] Checkpoint WAL impossible : {exc}")
