"""Pont QML du registre de detecteurs : choix, installation, comparaison."""

from __future__ import annotations

import sys
import time
from pathlib import Path
from typing import Any

from PySide6.QtCore import Property, QObject, QThread, QUrl, Signal, Slot

import fish_detectors as fd
from fish_detectors.spec import SAM3_CUSTOM_ID, sam3_prompt_entry
from src.util.log_model import LogModel

_SUFFIX_BACKENDS = {
    ".pt": "ultralytics",
    ".onnx": "onnx",
    ".engine": "ultralytics",
    ".torchscript": "ultralytics",
    ".pth": "rfdetr",
}

class _DownloadWorker(QThread):
    progressed = Signal(int, int)
    finished_ok = Signal(str, str)
    failed = Signal(str, str)

    def __init__(self, detector_id: str, parent=None):
        super().__init__(parent)
        self._detector_id = detector_id
        self._cancel = False

    def cancel(self) -> None:
        self._cancel = True

    def run(self):
        try:
            path = fd.registry().download(
                self._detector_id,
                progress=lambda done, total: self.progressed.emit(done, total),
                cancelled=lambda: self._cancel,
            )
            self.finished_ok.emit(self._detector_id, str(path))
        except Exception as exc:
            self.failed.emit(self._detector_id, str(exc))


class _PipInstallWorker(QThread):
    """`pip install` dans l'interpreteur qui fait tourner l'application.

    Un moteur d'inference absent se reglait jusqu'ici en ouvrant un terminal et
    en retrouvant le bon interpreteur - celui du venv, pas celui du PATH. La
    confusion entre les deux est la premiere cause d'un « j'ai pourtant
    installe le paquet » suivi d'un modele toujours indisponible. On installe
    donc avec sys.executable, sans laisser le choix.
    """

    line = Signal(str)
    done = Signal(bool, str)

    def __init__(self, packages: list[str], parent=None):
        super().__init__(parent)
        self._packages = list(packages)

    def run(self):
        import subprocess

        command = [sys.executable, "-m", "pip", "install", *self._packages]
        self.line.emit("$ " + " ".join(command))
        try:
            process = subprocess.Popen(
                command,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                text=True,
                encoding="utf-8",
                errors="replace",
                # Pas de console qui surgit derriere la fenetre sous Windows.
                creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
            )
        except OSError as exc:
            self.done.emit(False, str(exc))
            return

        for raw in process.stdout or ():
            text = raw.rstrip()
            # pip ecrit une ligne par paquet telechargé : on ne garde que ce
            # qui apprend quelque chose, la console de l'app est partagee.
            if text and not text.startswith("  "):
                self.line.emit(text)
        code = process.wait()
        if code == 0:
            self.done.emit(True, " ".join(self._packages))
        else:
            self.done.emit(False, f"pip a repondu {code}")


class _CompareWorker(QThread):
    row_ready = Signal(dict)
    finished_all = Signal(int)

    def __init__(self, frame, conf: float, detector_ids: list[str], parent=None):
        super().__init__(parent)
        self._frame = frame
        self._conf = conf
        self._ids = detector_ids

    def run(self):
        reg = fd.registry()
        done = 0
        for detector_id in self._ids:
            status = reg.status(detector_id)
            if status is None:
                continue
            row: dict[str, Any] = {
                "id": detector_id,
                "label": status.spec.label,
                "backend": status.spec.backend,
                "count": 0,
                "elapsedMs": 0,
                "bestConf": 0.0,
                "error": "",
            }
            start = time.perf_counter()
            try:
                conf = self._conf if self._conf > 0 else status.spec.default_conf
                boxes = reg.detect(self._frame, conf=conf, detector_id=detector_id)
                row["count"] = len(boxes)
                row["bestConf"] = round(float(boxes[0]["conf"]), 3) if boxes else 0.0
            except Exception as exc:
                row["error"] = str(exc)
            row["elapsedMs"] = int((time.perf_counter() - start) * 1000)
            self.row_ready.emit(row)
            done += 1
        self.finished_all.emit(done)


class _InspectWorker(QThread):
    inspected = Signal(dict)

    def __init__(self, detector_id: str, parent=None):
        super().__init__(parent)
        self._detector_id = detector_id

    def run(self):
        start = time.perf_counter()
        try:
            result = fd.registry().inspect(self._detector_id)
        except Exception as exc:
            result = {
                "id": self._detector_id,
                "state": "error",
                "compatible": False,
                "inferencePassed": False,
                "error": str(exc),
            }
        result["elapsedMs"] = int((time.perf_counter() - start) * 1000)
        self.inspected.emit(result)


class DetectorController(QObject):
    modelsChanged = Signal()
    activeChanged = Signal()
    busyChanged = Signal()
    statusTextChanged = Signal()
    downloadChanged = Signal()
    comparisonChanged = Signal()
    inspectionChanged = Signal()
    huggingFaceChanged = Signal()
    catalogUrlChanged = Signal()
    activeModelSwitched = Signal(str)
    openManagerRequested = Signal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self._logs = LogModel(self)
        self._registry = fd.registry()
        self._models: list[dict] = []
        self._backends: list[dict] = []
        self._comparison: list[dict] = []
        self._model_inspection: dict[str, Any] = {}
        self._status = ""
        self._busy = False
        self._downloading_id = ""
        self._progress = 0.0
        self._download_worker: _DownloadWorker | None = None
        self._compare_worker: _CompareWorker | None = None
        self._inspection_worker: _InspectWorker | None = None
        self._install_worker: _PipInstallWorker | None = None
        self._activate_after_inspection = ""
        self._fish = None
        self._refresh(quiet=True)

    def set_fish_controller(self, fish) -> None:
        self._fish = fish

    @Property(QObject, constant=True)
    def logs(self):
        return self._logs

    # -- etat expose ------------------------------------------------------

    @Property(list, notify=modelsChanged)
    def models(self):
        return self._models

    @Property(list, notify=modelsChanged)
    def backends(self):
        return self._backends

    @Property(list, notify=comparisonChanged)
    def comparison(self):
        return self._comparison

    @Property("QVariantMap", notify=inspectionChanged)
    def modelInspection(self):
        return self._model_inspection

    @Property(str, notify=modelsChanged)
    def sam3Prompt(self):
        spec = self._registry.spec(SAM3_CUSTOM_ID) or self._registry.spec("sam3-fish")
        if spec is None or spec.backend != "sam3":
            return "fish"
        return str(spec.options.get("text_prompt") or "fish")

    @Property(str, notify=activeChanged)
    def activeId(self):
        return self._registry.active_id

    @Property(str, notify=activeChanged)
    def activeLabel(self):
        spec = self._registry.active_spec()
        return spec.label if spec else "Aucun modele"

    @Property(str, notify=activeChanged)
    def activeBackend(self):
        spec = self._registry.active_spec()
        return spec.backend if spec else ""

    @Property(float, notify=activeChanged)
    def activeDefaultConf(self):
        return float(self._registry.confidence_for_active())

    @Property(bool, notify=activeChanged)
    def ready(self):
        return self._registry.is_available()

    @Property(str, notify=activeChanged)
    def activeReason(self):
        return self._registry.unavailable_reason()

    @Property(int, notify=modelsChanged)
    def installedCount(self):
        return sum(1 for m in self._models if m["usable"])

    @Property(bool, notify=busyChanged)
    def busy(self):
        return self._busy

    @Property(str, notify=statusTextChanged)
    def statusText(self):
        return self._status

    @Property(str, notify=downloadChanged)
    def downloadingId(self):
        return self._downloading_id

    @Property(float, notify=downloadChanged)
    def downloadProgress(self):
        return self._progress

    @Property(str, notify=catalogUrlChanged)
    def catalogUrl(self):
        return str(self._registry.state_value("catalog_url", "") or "")

    @catalogUrl.setter
    def catalogUrl(self, value: str):
        if value != self.catalogUrl:
            self._registry.set_state_value("catalog_url", str(value))
            self.catalogUrlChanged.emit()

    # -- helpers ----------------------------------------------------------

    def _set_status(self, message: str, *, log: bool = True) -> None:
        self._status = message
        self.statusTextChanged.emit()
        if log and message:
            self._logs.append(message)

    def _set_busy(self, value: bool) -> None:
        if self._busy != value:
            self._busy = value
            self.busyChanged.emit()

    def _refresh(self, *, quiet: bool = False) -> None:
        self._models = [self._with_training_info(row) for row in self._registry.entries()]
        self._backends = self._registry.backend_report()
        self.modelsChanged.emit()
        self.activeChanged.emit()
        if not quiet:
            usable = sum(1 for m in self._models if m["usable"])
            self._set_status(f"{usable} modele(s) pret(s) sur {len(self._models)}")

    # -- provenance des modeles maison -------------------------------------

    @staticmethod
    def _with_training_info(row: dict) -> dict:
        """Ajoute rang et date d'entrainement aux modeles produits ici.

        Un re-entrainement ecrase le fichier de poids sans changer son nom :
        rien a l'ecran ne disait quel modele on utilisait vraiment. Le rang et
        le nombre d'epochs viennent du descripteur ecrit par le Hub Pro ; la
        date vient du fichier lui-meme, donc elle reste juste meme pour des
        poids poses a la main.
        """
        import json
        from datetime import datetime
        from pathlib import Path as _Path

        row = dict(row)
        row["trainingLabel"] = ""
        row["trainedAt"] = ""
        row["trainedRank"] = ""
        weights = str(row.get("weightsPath") or "")
        if not weights or not row.get("installed"):
            return row
        path = _Path(weights)
        if not path.name.startswith("fish_detect_"):
            return row  # modele tiers : sa date n'apprend rien

        try:
            stamp = datetime.fromtimestamp(path.stat().st_mtime)
        except OSError:
            return row
        row["trainedAt"] = stamp.strftime("%d/%m/%Y %H:%M")

        meta_path = path.with_suffix(".meta.json")
        parts = []
        if meta_path.is_file():
            try:
                meta = json.loads(meta_path.read_text(encoding="utf-8"))
            except (OSError, ValueError):
                meta = {}
            rank = str(meta.get("rank", "") or "")
            if rank:
                row["trainedRank"] = rank
                parts.append(f"rang {rank}")
            epochs = meta.get("epochs")
            if epochs:
                parts.append(f"{int(epochs)} epochs")
        parts.append(row["trainedAt"])
        row["trainingLabel"] = " · ".join(parts)
        return row

    @Property(str, notify=activeChanged)
    def activeTrainingLabel(self):
        """« rang family · 50 epochs · 16/06/2026 18:27 », ou vide."""
        for row in self._models:
            if row["id"] == self._registry.active_id:
                return str(row.get("trainingLabel", ""))
        return ""

    # -- actions ----------------------------------------------------------

    @Slot()
    def openManager(self):
        self.openManagerRequested.emit()

    @Slot()
    def refresh(self):
        self._registry.reload()
        self._refresh()

    @Slot(str)
    def setActive(self, detector_id: str):
        status = self._registry.status(detector_id)
        if status is None:
            self._set_status(f"Modele inconnu : {detector_id}")
            return
        if not status.usable:
            self._set_status(f"{status.spec.label} - {status.reason()}")
            return
        if self._registry.set_active(detector_id):
            self._refresh(quiet=True)
            self._set_status(f"Modele actif : {status.spec.label}")
            self.activeModelSwitched.emit(detector_id)

    @Slot(str, result="QVariantMap")
    def modelAt(self, detector_id: str):
        for row in self._models:
            if row["id"] == detector_id:
                return row
        return {}

    @Slot(str)
    def openHomepage(self, detector_id: str):
        from PySide6.QtGui import QDesktopServices

        row = self.modelAt(detector_id)
        url = row.get("homepage") if row else ""
        if url:
            QDesktopServices.openUrl(QUrl(url))

    # -- telechargement ---------------------------------------------------

    @Slot(str)
    def download(self, detector_id: str):
        if self._download_worker is not None:
            self._set_status("Un telechargement est deja en cours")
            return
        status = self._registry.status(detector_id)
        if status is None or not status.spec.downloadable:
            self._set_status("Ce modele n'a pas de source de telechargement")
            return

        self._downloading_id = detector_id
        self._progress = 0.0
        self.downloadChanged.emit()
        self._set_busy(True)
        self._set_status(f"Telechargement de {status.spec.label}…")

        worker = _DownloadWorker(detector_id, self)
        worker.progressed.connect(self._on_download_progress)
        worker.finished_ok.connect(self._on_download_ok)
        worker.failed.connect(self._on_download_failed)
        worker.finished.connect(self._on_download_finished)
        self._download_worker = worker
        worker.start()

    @Slot()
    def cancelDownload(self):
        if self._download_worker is not None:
            self._download_worker.cancel()
            self._set_status("Annulation du telechargement…")

    def _on_download_progress(self, done: int, total: int):
        self._progress = (done / total) if total else 0.0
        self.downloadChanged.emit()

    def _on_download_ok(self, detector_id: str, path: str):
        self._registry.reload()
        self._refresh(quiet=True)
        row = self.modelAt(detector_id)
        self._set_status(f"{row.get('label', detector_id)} installe")

    def _on_download_failed(self, detector_id: str, message: str):
        row = self.modelAt(detector_id)
        self._set_status(f"{row.get('label', detector_id)} - {message}")

    def _on_download_finished(self):
        self._download_worker = None
        self._downloading_id = ""
        self._progress = 0.0
        self.downloadChanged.emit()
        self._set_busy(False)

    # -- comparaison de modeles -------------------------------------------

    @Slot(float)
    def compareOnCurrentFrame(self, conf: float = 0.0):
        if self._compare_worker is not None:
            return
        if self._fish is None:
            self._set_status("Comparaison indisponible")
            return
        frame = self._fish.current_frame()
        if frame is None:
            self._set_status("Ouvrez une video et mettez en pause avant de comparer")
            return

        targets = [m["id"] for m in self._models if m["usable"]]
        if not targets:
            self._set_status("Aucun modele pret a comparer")
            return

        self._comparison = []
        self.comparisonChanged.emit()
        self._set_busy(True)
        self._set_status(f"Comparaison de {len(targets)} modele(s) sur la frame courante…")

        worker = _CompareWorker(frame, float(conf), targets, self)
        worker.row_ready.connect(self._on_compare_row)
        worker.finished_all.connect(self._on_compare_done)
        worker.finished.connect(self._on_compare_finished)
        self._compare_worker = worker
        worker.start()

    def _on_compare_row(self, row: dict):
        self._comparison = [*self._comparison, row]
        self.comparisonChanged.emit()

    def _on_compare_done(self, count: int):
        self._set_status(f"Comparaison terminee - {count} modele(s) evalues")

    def _on_compare_finished(self):
        self._compare_worker = None
        self._set_busy(False)
        # La comparaison a pu charger un autre modele : on recharge l'actif.
        self._registry.reload()

    # -- diagnostic des modeles ------------------------------------------

    def _start_inspection(self, detector_id: str, *, activate_on_success: bool = False):
        if self._inspection_worker is not None or self._busy:
            self._set_status("Une autre operation est deja en cours")
            return
        status = self._registry.status(detector_id)
        label = status.spec.label if status else detector_id
        self._model_inspection = {
            "id": detector_id,
            "label": label,
            "state": "running",
            "compatible": False,
            "inferencePassed": False,
            "error": "",
        }
        self._activate_after_inspection = detector_id if activate_on_success else ""
        self.inspectionChanged.emit()
        self._set_busy(True)
        self._set_status(f"Analyse de {label} : chargement et test d'inference…")

        worker = _InspectWorker(detector_id, self)
        worker.inspected.connect(self._on_inspection_ready)
        worker.finished.connect(self._on_inspection_finished)
        self._inspection_worker = worker
        worker.start()

    @Slot(str)
    def inspectModel(self, detector_id: str):
        self._start_inspection(detector_id)

    def _on_inspection_ready(self, result: dict):
        self._model_inspection = dict(result)
        self.inspectionChanged.emit()
        detector_id = str(result.get("id") or "")
        label = str(result.get("label") or detector_id)
        if result.get("compatible"):
            if detector_id == self._activate_after_inspection:
                changed = self._registry.set_active(detector_id)
                self._refresh(quiet=True)
                if changed:
                    self.activeModelSwitched.emit(detector_id)
            self._set_status(f"{label} - compatibilite AquaMeasure validee")
        else:
            message = str(result.get("error") or "test d'inference echoue")
            self._set_status(f"{label} - {message}")

    def _on_inspection_finished(self):
        self._inspection_worker = None
        self._activate_after_inspection = ""
        self._set_busy(False)

    # -- catalogue evolutif -----------------------------------------------

    @Slot(str)
    def updateCatalog(self, url: str):
        target = url.strip() or self.catalogUrl
        if not target:
            self._set_status("Renseignez l'URL du catalogue avant de mettre a jour")
            return
        self._set_busy(True)
        count, message = self._registry.update_catalog(target)
        self._set_busy(False)
        self._set_status(message)
        if count:
            self.catalogUrlChanged.emit()
            self._refresh(quiet=True)

    @Slot(QUrl, str)
    def addLocalModel(self, file_url: QUrl, label: str):
        """Declare un fichier de poids present sur le disque comme nouveau modele."""
        path = Path(file_url.toLocalFile() if file_url.isLocalFile() else file_url.toString())
        if not path.is_file():
            self._set_status(f"Fichier introuvable : {path}")
            return
        backend = _SUFFIX_BACKENDS.get(path.suffix.lower())
        if backend is None:
            self._set_status(f"Extension non reconnue : {path.suffix}")
            return

        detector_id = f"local-{path.stem.lower().replace(' ', '-')}"
        family = {
            "ultralytics": "yolo",
            "onnx": "yolo-onnx",
            "rfdetr": "rfdetr",
        }.get(backend, backend)
        try:
            spec = self._registry.add_user_detector({
                "id": detector_id,
                "label": label.strip() or path.stem,
                "backend": backend,
                "family": family,
                "description": "Modele ajoute manuellement depuis le disque.",
                "origin": "Local",
                "bundled_paths": [str(path)],
            })
        except (OSError, ValueError) as exc:
            self._set_status(f"Ajout impossible : {exc}")
            return
        self._refresh(quiet=True)
        self._set_status(f"{spec.label} ajoute au catalogue - verification en cours")
        self._start_inspection(spec.id)

    @Slot(str)
    def setSam3Prompt(self, prompt: str):
        """Cree une variante SAM 3 persistante, puis la teste et l'active."""
        normalized = " ".join(prompt.strip().split())
        if not normalized:
            self._set_status("Saisissez un prompt SAM 3 en anglais")
            return
        if len(normalized) > 120:
            self._set_status("Le prompt SAM 3 est limite a 120 caracteres")
            return

        base = self._registry.spec("sam3-fish")
        if base is None:
            base = next((spec for spec in self._registry.specs() if spec.backend == "sam3"), None)
        if base is None:
            self._set_status("Aucun backend SAM 3 n'est declare dans le catalogue")
            return

        try:
            spec = self._registry.add_user_detector(sam3_prompt_entry(base, normalized))
        except (OSError, ValueError) as exc:
            self._set_status(f"Prompt SAM 3 impossible a enregistrer : {exc}")
            return
        self._refresh(quiet=True)
        self._set_status(f"Prompt SAM 3 enregistre : {normalized}")
        self._start_inspection(spec.id, activate_on_success=True)

    # -- installation assistee ---------------------------------------------
    #
    # SAM 3 ne demande plus qu'une commande et une autorisation. Les deux se
    # font ici : ouvrir un terminal, retrouver le bon interpreteur puis taper
    # `hf auth login` etait le vrai obstacle, bien plus que le modele lui-meme.

    @Property(bool, notify=huggingFaceChanged)
    def huggingFaceReady(self):
        """Un jeton Hugging Face est-il disponible pour cette machine ?"""
        try:
            from fish_detectors.backends.sam3 import hf_token_present
        except Exception:  # noqa: BLE001 - backend absent : rien a promettre
            return False
        return hf_token_present()

    @Slot(str)
    def installBackend(self, backend_id: str):
        """Installe les paquets manquants d'un moteur d'inference."""
        if getattr(sys, "frozen", False):
            self._set_status("Pour ajouter un moteur, installez une version AquaMeasure qui le contient. Les fichiers de poids s'ajoutent dans Étendre.")
            return
        if self._install_worker is not None:
            self._set_status("Une installation est deja en cours")
            return
        row = next(
            (b for b in self._backends if b.get("id") == backend_id), None,
        )
        if row is None:
            self._set_status(f"Moteur inconnu : {backend_id}")
            return

        # « python>=3.10 » et « huggingface-login » decrivent l'environnement
        # et une autorisation : ni l'un ni l'autre ne s'installe avec pip.
        packages = [
            requirement for requirement in row.get("missingRequirements", [])
            if not requirement.startswith("python") and requirement != "huggingface-login"
        ]
        if not packages:
            self._set_status(f"{row.get('label', backend_id)} : rien a installer avec pip")
            return

        self._set_busy(True)
        self._set_status(f"Installation de {' '.join(packages)}…")
        worker = _PipInstallWorker(packages, self)
        worker.line.connect(lambda text: self._set_status(text))
        worker.done.connect(self._on_install_done)
        worker.finished.connect(self._on_install_finished)
        self._install_worker = worker
        worker.start()

    def _on_install_done(self, ok: bool, message: str):
        if ok:
            # Les modules fraichement installes ne sont pas visibles des
            # importlib deja resolus : sans invalidation, le moteur resterait
            # « absent » jusqu'au redemarrage.
            import importlib

            importlib.invalidate_caches()
            self._registry.reload()
            self._refresh(quiet=True)
            self._set_status(f"Installe : {message}")
        else:
            self._set_status(f"Installation interrompue : {message}")

    def _on_install_finished(self):
        self._install_worker = None
        self._set_busy(False)

    @Slot(str, result=bool)
    def saveHuggingFaceToken(self, token: str):
        """Depose le jeton la ou `huggingface_hub` le relira.

        Equivaut a `hf auth login` sans terminal. Le jeton n'est jamais
        journalise : la console n'en voit que la confirmation.
        """
        cleaned = str(token).strip()
        if not cleaned:
            self._set_status("Collez le jeton Hugging Face avant d'enregistrer")
            return False
        if not cleaned.startswith("hf_"):
            self._set_status("Un jeton Hugging Face commence par « hf_ »")
            return False

        try:
            from fish_detectors.backends.sam3 import token_path
        except Exception as exc:  # noqa: BLE001 - backend absent
            self._set_status(f"Backend SAM 3 indisponible : {exc}")
            return False

        target = token_path()
        try:
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text(cleaned, encoding="utf-8")
        except OSError as exc:
            self._set_status(f"Jeton non enregistre : {exc}")
            return False

        self._registry.reload()
        self._refresh(quiet=True)
        self.huggingFaceChanged.emit()
        self._set_status("Jeton Hugging Face enregistre")
        return True

    @Slot(str)
    def removeModel(self, detector_id: str):
        if self._registry.remove_user_detector(detector_id):
            self._refresh(quiet=True)
            self._set_status(f"{detector_id} retire du catalogue")
        else:
            self._set_status("Seuls les modeles ajoutes manuellement peuvent etre retires")
