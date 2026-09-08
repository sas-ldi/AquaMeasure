from __future__ import annotations

import json
import os
import re
import sys
from pathlib import Path

from PySide6.QtCore import Property, QObject, QProcess, Signal, Slot
from PySide6.QtWidgets import QFileDialog, QInputDialog

from src.util import paths
from src.util.log_model import LogModel


class ProToolsController(QObject):
    busyChanged = Signal()
    exportRankChanged = Signal()
    datasetNameChanged = Signal()
    epochsChanged = Signal()
    splitByChanged = Signal()
    datasetVersionChanged = Signal()
    avaHzChanged = Signal()
    lastExportChanged = Signal()
    sessionExportChanged = Signal()
    fishialLibraryImported = Signal()

    # Grain du split, dans l'ordre où on le choisit : le média est le minimum
    # sûr, la session et le site généralisent mieux quand ils existent.
    SPLIT_BY_VALUES = ("media", "session", "site")

    # Rang par défaut des exports de suivi. Sur la base réelle, **aucune** piste
    # ne porte de taxon (0 sur 1685) : à tout autre rang, chaque boîte sortirait
    # en `ignore` et le dataset serait vide.
    TRACKING_RANK = "fish"

    def __init__(self, parent=None):
        super().__init__(parent)
        self._logs = LogModel(self)
        self._fishial_transfer_summary = ""
        self._busy = False
        self._export_rank = "family"
        self._dataset_name = "fishinv"
        self._epochs = 50
        self._split_by = self._default_split_by()
        self._dataset_version = "1.0.0"
        self._ava_hz = 1.0
        self._process: QProcess | None = None
        self._process_generation = 0
        self._last_export_status = "idle"
        self._last_export_code = -1
        self._last_export_directory = ""
        self._last_export_error = ""
        self._last_export_kind = ""
        self._active_export_kind = ""
        self._session_export_format = "coco"
        self._pending_session_id = ""
        self._session_export_state = "idle"
        self._session_preview: dict = {}
        self._missing_media: list[dict] = []

    def _default_split_by(self) -> str:
        """Grain proposé au premier affichage : `session` dès qu'il en existe.

        `media` - l'ancien défaut - sépare les **deux caméras d'une même
        session stéréo** : le même poisson, au même instant, vu de gauche et de
        droite, partait de part et d'autre du découpage. C'est une fuite de
        données, et elle était silencieuse. La décision est prise par le noyau
        d'export (`export_core.suggest_split_by`) pour que l'interface et la
        ligne de commande ne divergent pas. Sans base lisible, on garde `media`,
        le grain le plus fin qui reste sûr.
        """
        try:
            self._ensure_fv()
            from src.annodb.connection import get_db_path, session_scope
            from src.annodb.export_core import suggest_split_by

            db_path = get_db_path()
            if not db_path.exists():
                return "media"
            with session_scope(db_path) as session:
                return suggest_split_by(session)
        except Exception:
            # Base absente, schéma antérieur, fish-vision non installé : le
            # défaut ne doit jamais empêcher la page de s'ouvrir.
            return "media"

    def _ensure_fv(self) -> None:
        fv = self._fv_root()
        if fv.is_dir() and str(fv) not in sys.path:
            sys.path.insert(0, str(fv))

    @Property(QObject, constant=True)
    def logs(self):
        return self._logs

    @Property(bool, notify=busyChanged)
    def busy(self):
        return self._busy

    @Property("QVariantMap", notify=lastExportChanged)
    def lastExport(self):
        """Dernier export lancé ici, conservé après la fin du processus."""
        return {
            "status": self._last_export_status,
            "exitCode": self._last_export_code,
            "directory": self._last_export_directory,
            "error": self._last_export_error,
            "kind": self._last_export_kind,
        }

    @Property(bool, notify=sessionExportChanged)
    def includeTracking(self):
        """Compatibilité : vrai quand le format choisi est COCO-VID."""
        return self._session_export_format == "tracking"

    @includeTracking.setter
    def includeTracking(self, value: bool):
        self.sessionExportFormat = "tracking" if value else "coco"

    @Property(str, notify=sessionExportChanged)
    def sessionExportFormat(self):
        return self._session_export_format

    @sessionExportFormat.setter
    def sessionExportFormat(self, value: str):
        value = str(value or "").strip().lower()
        if value not in ("coco", "tracking", "csv"):
            value = "coco"
        if self._session_export_format != value:
            self._session_export_format = value
            self.sessionExportChanged.emit()

    @Property(str, notify=sessionExportChanged)
    def sessionExportState(self):
        return self._session_export_state

    @Property(int, notify=sessionExportChanged)
    def missingMediaCount(self):
        return len(self._missing_media)

    @Property(list, notify=sessionExportChanged)
    def missingMedia(self):
        return list(self._missing_media)

    @Property(str, notify=sessionExportChanged)
    def missingMediaMessage(self):
        if not self._missing_media:
            return ""
        names = ", ".join(str(row.get("name") or "vidéo") for row in self._missing_media)
        return (
            f"{len(self._missing_media)} fichier(s) manquant(s) : {names}. "
            "Vous pouvez les retrouver, ou continuer avec les vidéos disponibles."
        )

    @Property(str, notify=exportRankChanged)
    def exportRank(self):
        return self._export_rank

    @exportRank.setter
    def exportRank(self, v: str):
        if self._export_rank != v:
            self._export_rank = v
            self.exportRankChanged.emit()

    @Property(list, constant=True)
    def datasetOptions(self):
        """Catalogue des jeux publics téléchargeables, lu dans le script.

        Le champ était une saisie libre alors que seuls sept noms exacts sont
        acceptés (argparse `choices`) : une faute de frappe faisait échouer le
        téléchargement, et rien dans l'application ne disait où trouver la
        liste. On la lit directement dans download_public_dataset.py - pas
        d'import (le script tire des dépendances lourdes), juste une lecture
        littérale des trois dictionnaires.
        """
        script = self._fv_root() / "scripts" / "download_public_dataset.py"
        options: list[dict] = []
        try:
            import ast

            tree = ast.parse(script.read_text(encoding="utf-8"))
            for node in tree.body:
                if not isinstance(node, ast.Assign):
                    continue
                target = node.targets[0]
                if not isinstance(target, ast.Name):
                    continue
                if target.id not in ("DATASETS", "ZENODO", "DIRECT_ZIPS"):
                    continue
                for key, value in ast.literal_eval(node.value).items():
                    label = str(value.get("label", "")) or key
                    options.append({"id": key, "label": f"{key} - {label}"})
        except (OSError, SyntaxError, ValueError) as exc:
            self._logs.append(f"[!] Catalogue datasets illisible : {exc}")
            return [{"id": self._dataset_name, "label": self._dataset_name}]
        options.append(
            {"id": "synthetic", "label": "synthetic - images générées, pour tester la chaîne"}
        )
        return options

    @Property(int, notify=datasetNameChanged)
    def datasetIndex(self):
        """Position du jeu choisi dans datasetOptions (0 si introuvable)."""
        for i, opt in enumerate(self.datasetOptions):
            if opt["id"] == self._dataset_name:
                return i
        return 0

    @Slot(int)
    def selectDatasetAt(self, index: int):
        options = self.datasetOptions
        if 0 <= index < len(options):
            self.datasetName = options[index]["id"]

    @Property(str, notify=datasetNameChanged)
    def datasetName(self):
        return self._dataset_name

    @datasetName.setter
    def datasetName(self, v: str):
        if self._dataset_name != v:
            self._dataset_name = v
            self.datasetNameChanged.emit()

    @Property(str, notify=splitByChanged)
    def splitBy(self):
        """Grain du split train/val/test : 'media', 'session' ou 'site'."""
        return self._split_by

    @splitBy.setter
    def splitBy(self, v: str):
        v = str(v or "").strip().lower()
        if v not in self.SPLIT_BY_VALUES:
            return
        if self._split_by != v:
            self._split_by = v
            self.splitByChanged.emit()

    @Property(str, notify=datasetVersionChanged)
    def datasetVersion(self):
        """Version semver du dataset produit - un export ne se corrige pas."""
        return self._dataset_version

    @datasetVersion.setter
    def datasetVersion(self, v: str):
        v = str(v or "").strip() or "1.0.0"
        # La version entre dans le nom du dossier d'export : un séparateur de
        # chemin y écrirait ailleurs. Même charte que le noyau d'export.
        if not re.match(r"^[A-Za-z0-9][A-Za-z0-9._+-]*$", v):
            self._logs.append(
                f"[!] Version « {v} » refusée (lettres, chiffres, . _ - + "
                f"uniquement) - version conservée : {self._dataset_version}"
            )
            return
        if self._dataset_version != v:
            self._dataset_version = v
            self.datasetVersionChanged.emit()

    @Property(float, notify=avaHzChanged)
    def avaHz(self):
        """Densification du CSV de comportement, en lignes par seconde.

        La valeur est inscrite au manifeste : un dataset AVA densifié à 1 Hz et
        un autre à 5 Hz ne sont pas comparables, et rien d'autre que ce champ
        ne permet de le savoir après coup.
        """
        return self._ava_hz

    @avaHz.setter
    def avaHz(self, v: float):
        # Au-delà de la cadence vidéo, densifier ne crée que des doublons ;
        # en dessous de 0,1 Hz, un intervalle court ne produit plus rien.
        v = max(0.1, min(60.0, float(v)))
        if abs(self._ava_hz - v) > 1e-9:
            self._ava_hz = v
            self.avaHzChanged.emit()

    @Property(int, notify=epochsChanged)
    def epochs(self):
        return self._epochs

    @epochs.setter
    def epochs(self, v: int):
        v = max(1, min(500, int(v)))
        if self._epochs != v:
            self._epochs = v
            self.epochsChanged.emit()

    def _fv_root(self) -> Path:
        return paths.app_root() / "fish-vision"

    def _python_exe(self) -> str:
        fv = self._fv_root()
        venv_py = fv / ".venv" / "Scripts" / "python.exe"
        if venv_py.is_file():
            return str(venv_py)
        return sys.executable

    def _set_busy(self, v: bool):
        if self._busy != v:
            self._busy = v
            self.busyChanged.emit()

    def _set_last_export(
        self, status: str, code: int = -1, directory: str = "", error: str = "",
        *, kind: str | None = None,
    ) -> None:
        resolved_kind = str(
            self._active_export_kind if kind is None else kind
        )
        if not resolved_kind:
            resolved_kind = self._last_export_kind
        state = (
            str(status), int(code), str(directory or ""), str(error or ""),
            resolved_kind,
        )
        current = (
            self._last_export_status,
            self._last_export_code,
            self._last_export_directory,
            self._last_export_error,
            self._last_export_kind,
        )
        if state == current:
            return
        (
            self._last_export_status,
            self._last_export_code,
            self._last_export_directory,
            self._last_export_error,
            self._last_export_kind,
        ) = state
        self.lastExportChanged.emit()
        if (
            resolved_kind == "session"
            and self._pending_session_id
            and status in ("running", "success", "error")
        ):
            self._session_export_state = status
            self.sessionExportChanged.emit()

    @Property(str, notify=lastExportChanged)
    def fishialTransferSummary(self):
        return self._fishial_transfer_summary

    @staticmethod
    def _export_result_from_output(output: str) -> dict:
        decoder = json.JSONDecoder()
        for index, char in enumerate(output):
            if char != "{":
                continue
            try:
                value, _end = decoder.raw_decode(output[index:])
            except json.JSONDecodeError:
                continue
            if isinstance(value, dict) and value.get("output_path"):
                return value
        return {}

    @staticmethod
    def _export_directory_from_output(output: str) -> str:
        return str(ProToolsController._export_result_from_output(output).get("output_path", ""))

    def _record_export_result(self, code: int, output: str) -> None:
        if self._active_export_kind in ("fishial_local_export", "fishial_local_import"):
            self._fishial_transfer_summary = str(self._export_result_from_output(output).get("message", ""))
        directory = self._export_directory_from_output(output)
        if int(code) != 0:
            self._set_last_export("error", int(code), directory, output.strip())
            return
        if not directory:
            self._set_last_export(
                "error", 0, "", "Le processus n'a fourni aucun dossier d'export.",
            )
            return
        if not Path(directory).is_dir():
            self._set_last_export(
                "error", 0, directory, f"Dossier d'export introuvable : {directory}",
            )
            return
        self._set_last_export("success", 0, directory)

    def _models_dir(self) -> Path:
        return self._fv_root() / "models"

    def _weights_snapshot(self) -> dict[str, float]:
        """Date de derniere ecriture des poids maison, avant un job."""
        out: dict[str, float] = {}
        d = self._models_dir()
        if not d.is_dir():
            return out
        for f in d.glob("fish_detect_*.pt"):
            try:
                out[f.name] = f.stat().st_mtime
            except OSError:
                pass
        return out

    def _sign_trained_weights(self, before: dict[str, float], rank: str) -> None:
        """Ecrit un descripteur a cote de chaque fichier de poids reecrit.

        Les scripts d'entrainement recopient best.pt vers
        models/fish_detect_<tag>.pt, en ecrasant le precedent : meme nom,
        poids differents, et rien nulle part ne disait a quel rang ni
        quand il avait ete entraine. Ce descripteur repond aux deux.
        """
        import json
        from datetime import datetime

        d = self._models_dir()
        if not d.is_dir():
            return
        for f in sorted(d.glob("fish_detect_*.pt")):
            try:
                mtime = f.stat().st_mtime
            except OSError:
                continue
            if before.get(f.name) == mtime:
                continue  # inchange par ce job
            meta = {
                "rank": rank,
                "epochs": int(self._epochs),
                "trained_at": datetime.fromtimestamp(mtime).isoformat(
                    timespec="seconds"
                ),
            }
            try:
                f.with_suffix(".meta.json").write_text(
                    json.dumps(meta, ensure_ascii=False, indent=2) + "\n",
                    encoding="utf-8",
                )
            except OSError as exc:
                self._logs.append(f"[!] Descripteur {f.name} : {exc}")
                continue
            self._logs.append(
                f"Modele {f.name} - rang {rank}, {meta['epochs']} epochs, "
                f"{meta['trained_at']}"
            )

    def _run_script(
        self, script: str, args: list[str], *, is_export: bool = False,
        trained_rank: str = "", export_kind: str = "technical",
    ) -> None:
        if self._busy:
            self._logs.append("[!] Un job Pro est deja en cours")
            return
        if is_export:
            self._active_export_kind = str(export_kind or "technical")
        fv = self._fv_root()
        if not fv.is_dir():
            self._logs.append("[!] Dossier fish-vision introuvable")
            if is_export:
                self._set_last_export("error", -1)
            return
        script_path = fv / "scripts" / script
        if not script_path.is_file():
            self._logs.append(f"[!] Script introuvable : {script}")
            if is_export:
                self._set_last_export("error", -1)
            return
        self._process_generation += 1
        generation = self._process_generation
        process = QProcess(self)
        self._process = process
        process.setWorkingDirectory(str(fv))
        process.setProcessChannelMode(QProcess.MergedChannels)
        output_chunks: list[str] = []

        def on_ready():
            data = bytes(process.readAllStandardOutput()).decode("utf-8", errors="replace")
            output_chunks.append(data)
            for line in data.splitlines():
                if line.strip():
                    self._logs.append(line)

        def finalize():
            if self._process is not process or generation != self._process_generation:
                return False
            self._set_busy(False)
            self._process = None
            self._active_export_kind = ""
            process.deleteLater()
            return True

        def on_finished(code, _status):
            if self._process is not process or generation != self._process_generation:
                return
            on_ready()
            self._logs.append(f"── Termine (code {code}) ──")
            if trained_rank and int(code) == 0:
                self._sign_trained_weights(weights_before, trained_rank)
            if is_export:
                self._record_export_result(int(code), "".join(output_chunks))
                if export_kind == "fishial_local_import" and self._last_export_status == "success":
                    self.fishialLibraryImported.emit()
            finalize()

        def on_error(_error):
            if self._process is not process or generation != self._process_generation:
                return
            on_ready()
            detail = process.errorString() or "Erreur inconnue du processus"
            self._logs.append(f"[!] Processus : {detail}")
            if is_export:
                self._set_last_export("error", -1, "", detail)
            finalize()

        weights_before = self._weights_snapshot() if trained_rank else {}
        process.readyReadStandardOutput.connect(on_ready)
        process.finished.connect(on_finished)
        process.errorOccurred.connect(on_error)
        cmd = [self._python_exe(), str(script_path), *args]
        self._logs.append(f"▶ {' '.join(cmd)}")
        if is_export:
            self._set_last_export("running", -1)
        self._set_busy(True)
        process.start(cmd[0], cmd[1:])

    @Slot()
    def downloadPublicDataset(self):
        self._run_script("download_public_dataset.py", ["--dataset", self._dataset_name])

    @Slot()
    def pickIngestCvat(self):
        path, _ = QFileDialog.getOpenFileName(
            None, "Export CVAT", "", "Archives (*.zip);;Tous (*.*)"
        )
        if not path:
            return
        project, ok = QInputDialog.getText(None, "Projet", "Nom du projet DB :")
        if not ok or not project.strip():
            return
        self._run_script(
            "db_ingest_cvat.py",
            ["--export", path, "--project", project.strip()],
        )

    def _export_root(self) -> Path:
        """Racine des exports : `<racine des données>/data/exports`.

        Le dossier versionné (`<nom>_v<version>_<date>_<hash8>`) y est créé par
        le noyau d'export - un export n'écrase jamais le précédent.
        """
        out = paths.exports_dir()
        out.mkdir(parents=True, exist_ok=True)
        return out

    def _export_args(self, fmt: str) -> list[str]:
        return [
            "--format", fmt,
            "--rank", self._export_rank,
            "--split-by", self._split_by,
            "--version", self._dataset_version,
            "--out", str(self._export_root()),
        ]

    @Slot()
    def exportDbYolo(self):
        """Export YOLO - dérivé du COCO produit par le même noyau."""
        self._run_script(
            "db_export.py", self._export_args("yolo"), is_export=True,
        )

    @Slot()
    def exportDbCoco(self):
        """Export COCO - le format pivot (ignore, ids re-traçables)."""
        self._run_script(
            "db_export.py", self._export_args("coco"), is_export=True,
        )

    def _load_session_preview(self, session_id: str) -> dict:
        self._ensure_fv()
        import fish_annotate as fa

        return fa.session_export_preview(session_id)

    def _start_session_export(self, *, allow_missing: bool) -> None:
        args = [
            "--session", self._pending_session_id,
            "--format", self._session_export_format,
            "--version", self._dataset_version,
            "--out", str(self._export_root()),
        ]
        if allow_missing:
            args.append("--allow-missing")
        self._run_script(
            "export_session.py", args, is_export=True, export_kind="session",
        )

    @Slot(str)
    def exportSession(self, session_id: str):
        """Action principale : précontrôle puis paquet COCO de la session."""
        if self._busy:
            return
        session_id = str(session_id or "").strip()
        if not session_id:
            self._set_last_export(
                "error", -1, "", "Créez ou sélectionnez d'abord une session.",
                kind="session",
            )
            return
        self._set_last_export("idle", -1, "", "", kind="session")
        self._pending_session_id = session_id
        try:
            self._session_preview = self._load_session_preview(session_id)
            self._missing_media = list(self._session_preview.get("missing_media") or [])
        except Exception as exc:
            self._missing_media = []
            self._session_export_state = "error"
            self.sessionExportChanged.emit()
            self._set_last_export("error", -1, "", str(exc), kind="session")
            return
        if self._missing_media and self._session_export_format != "csv":
            self._session_export_state = "missing"
            self.sessionExportChanged.emit()
            return
        self._start_session_export(allow_missing=False)

    @Slot()
    def continueSessionExport(self):
        """Choix explicite : produire le paquet sans les vidéos absentes."""
        if self._pending_session_id and not self._busy:
            self._start_session_export(allow_missing=True)

    @Slot()
    def repointNextMissingMedia(self):
        """Demande le nouvel emplacement du premier fichier manquant."""
        if not self._pending_session_id or not self._missing_media:
            return
        expected = self._missing_media[0]
        path, _ = QFileDialog.getOpenFileName(
            None,
            f"Retrouver {expected.get('name') or 'la vidéo'}",
            "",
            "Vidéos (*.mp4 *.avi *.mov *.mkv);;Tous (*.*)",
        )
        if not path:
            return
        try:
            self._ensure_fv()
            import fish_annotate as fa

            fa.repoint_media(str(expected.get("media_id") or ""), path)
            self._session_preview = self._load_session_preview(self._pending_session_id)
            self._missing_media = list(self._session_preview.get("missing_media") or [])
            self._session_export_state = "missing" if self._missing_media else "ready"
            self.sessionExportChanged.emit()
            if not self._missing_media:
                self._start_session_export(allow_missing=False)
        except Exception as exc:
            self._session_export_state = "missing"
            self.sessionExportChanged.emit()
            self._set_last_export("error", -1, "", str(exc), kind="session")
            # L'erreur explique pourquoi ce fichier n'est pas le bon, mais le
            # parcours doit rester sur l'étape « vidéo manquante » pour que
            # l'utilisateur puisse immédiatement en choisir une autre.
            self._session_export_state = "missing"
            self.sessionExportChanged.emit()

    @Slot()
    def openLastExportFolder(self):
        folder = Path(self._last_export_directory)
        if folder.is_dir():
            os.startfile(str(folder))
        else:
            self._logs.append(f"[!] Dossier d'export introuvable : {folder}")

    @Slot()
    def exportTracking(self):
        """Export de suivi : COCO-VID (pivot) **et** son dérivé MOTChallenge.

        Le rang est forcé à `fish` : les pistes de la base ne portent aucun
        taxon, et un rang plus fin ne produirait que des boîtes `ignore`.
        """
        args = self._export_args("mot")
        # `--rank` est déjà dans `_export_args` : on remplace sa valeur plutôt
        # que d'en ajouter une seconde, qu'argparse trancherait en silence.
        args[args.index("--rank") + 1] = self.TRACKING_RANK
        self._logs.append(
            f"▶ Export suivi (COCO-VID + MOT), rang {self.TRACKING_RANK}"
        )
        self._run_script("db_export.py", args, is_export=True)

    @Slot()
    def exportBehavior(self):
        """Export de comportement : CSV type AVA + table de labels + intervalles."""
        args = self._export_args("ava") + ["--ava-hz", f"{self._ava_hz:g}"]
        args[args.index("--rank") + 1] = self.TRACKING_RANK
        self._logs.append(
            f"▶ Export comportement (AVA + intervalles) à {self._ava_hz:g} Hz"
        )
        self._run_script("db_export.py", args, is_export=True)

    @Slot()
    def trainDetect(self):
        self._run_script("train_detect.py", [], trained_rank="family")

    @Slot()
    def retrainFromDb(self):
        # Même grain de split et même racine d'export que le bouton « Export
        # YOLO » : le réentraînement doit porter sur le dataset qu'on vient de
        # produire, pas sur un dossier voisin.
        self._run_script(
            "retrain_from_db.py",
            [
                "--rank", self._export_rank,
                "--split-by", self._split_by,
                "--out", str(self._export_root()),
                "--epochs", str(self._epochs),
            ],
            trained_rank=self._export_rank,
        )

    @Slot()
    def exportCropsEmbeddings(self):
        out = paths.exports_dir() / "crops_embeddings"
        out.mkdir(parents=True, exist_ok=True)
        self._run_script(
            "export_crops.py",
            ["--out", str(out), "--embeddings"],
        )

    @Slot(str)
    def exportSessionCrops(self, media_id: str = ""):
        """Export bounding-box crops + rich metadata for one session (or all)."""
        sub = "crops_session" if media_id else "crops_all"
        out = paths.exports_dir() / sub
        out.mkdir(parents=True, exist_ok=True)
        args = ["--out", str(out), "--embeddings"]
        if media_id:
            args += ["--media-id", media_id]
        self._run_script("export_crops.py", args)

    @Slot()
    def exportFishialLocal(self):
        if self._busy:
            return
        self._fishial_transfer_summary = ""
        self._run_script(
            "transfer_fishial_library.py", ["export", "--out", str(self._export_root())],
            is_export=True, export_kind="fishial_local_export",
        )

    @Slot()
    def importFishialLocal(self):
        if self._busy:
            return
        archive, _ = QFileDialog.getOpenFileName(
            None, "Importer un Fishial local", "", "Bibliothèque Fishial (*.zip)",
        )
        if not archive:
            return
        self._fishial_transfer_summary = ""
        self._run_script(
            "transfer_fishial_library.py", ["import", "--archive", archive],
            is_export=True, export_kind="fishial_local_import",
        )

    @Slot()
    def exportFishialLibrary(self):
        """Copie portable des crops locaux, rangés par espèce."""
        self._run_script(
            "export_fishial_library.py",
            ["--out", str(self._export_root())],
            is_export=True,
            export_kind="fishial",
        )

    @Slot(str)
    def exportGrazingForMedia(self, media_id: str):
        if not media_id:
            self._logs.append("[!] media_id requis")
            return
        out = paths.exports_dir() / "grazing"
        out.mkdir(parents=True, exist_ok=True)
        self._run_script(
            "export_grazing_dataset.py",
            ["--media-id", media_id, "--out", str(out)],
        )

    @Slot()
    def auditDataset(self):
        """Audite le **dernier export** enregistré dans `export_runs`.

        Sans argument, le script auditait le dataset public téléchargé : on
        vérifiait donc autre chose que ce qu'on venait d'exporter.
        """
        self._logs.append("▶ Audit du dernier export enregistré (export_runs)")
        self._run_script("audit_dataset.py", [])

    @Slot()
    def cancelJob(self):
        if self._process and self._process.state() != QProcess.NotRunning:
            self._process.kill()
            self._logs.append("[!] Job annule")
