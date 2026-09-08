"""Registre des detecteurs : catalogue + backends + modele actif.

Point d'entree unique de la detection pour toute l'application. Le modele
actif est charge paresseusement et garde en memoire jusqu'a ce qu'on en
change.
"""

from __future__ import annotations

import hashlib
import threading
from pathlib import Path
from typing import Any, Callable

import numpy as np

from fish_detectors import catalog, downloader, store
from fish_detectors.backends import DetectorBackend, get_backend, load_all, plugin_errors
from fish_detectors.spec import BBOX_KEYS, DetectorSpec, normalize_boxes

# Ordre de repli quand aucun modele n'a ete choisi explicitement.
#
# Fishial est le socle produit : son detecteur officiel localise les poissons,
# puis ``fishial_classify`` identifie chaque crop. Viennent ensuite le
# detecteur communautaire, puis le modele generique de secours - aucun des
# deux ne doit devenir le choix implicite d'une nouvelle installation.
_PREFERRED = ("fishial-detector-v26", "cfd-yolov12x", "aquameasure-public")


class DetectorStatus:
    """Etat d'installation d'un modele, tel qu'affiche dans l'UI."""

    def __init__(self, spec: DetectorSpec, weights: Path | None, missing: list[str], known_backend: bool):
        self.spec = spec
        self.weights = weights
        self.missing_requirements = missing
        self.known_backend = known_backend

    @property
    def weights_present(self) -> bool:
        backend = get_backend(self.spec.backend)
        if backend is not None and not backend.needs_local_weights:
            return True
        return self.weights is not None

    @property
    def backend_ready(self) -> bool:
        return self.known_backend and not self.missing_requirements

    @property
    def usable(self) -> bool:
        return self.weights_present and self.backend_ready

    def reason(self) -> str:
        if not self.known_backend:
            return f"Backend « {self.spec.backend} » inconnu - plugin manquant"
        if self.missing_requirements:
            # Ordre volontaire : on annonce le premier obstacle a lever, pas la
            # liste entiere. Un interpreteur trop ancien passe avant les
            # paquets, qui passent avant l'acces Hugging Face - sans quoi on
            # laisserait croire qu'installer un paquet suffira.
            python_req = next(
                (r for r in self.missing_requirements if r.startswith("python>=")),
                "",
            )
            if python_req:
                return f"Python {python_req[len('python>='):]}+ requis"
            packages = [
                r for r in self.missing_requirements
                if not r.startswith("python") and r != "huggingface-login"
            ]
            if packages:
                return "Paquet requis : pip install " + " ".join(packages)
            if "huggingface-login" in self.missing_requirements:
                return (
                    "Acces Hugging Face requis : demander facebook/sam3 "
                    "puis hf auth login"
                )
        if not self.weights_present:
            if self.spec.downloadable:
                return "Poids non telecharges"
            return "Poids introuvables sur le disque"
        backend = get_backend(self.spec.backend)
        if backend is not None and not backend.needs_local_weights:
            return "Pret (poids Hugging Face au 1er chargement - hf auth login)"
        return "Pret"

    def to_dict(self) -> dict[str, Any]:
        spec = self.spec
        return {
            "id": spec.id,
            "label": spec.label,
            "backend": spec.backend,
            "backendLabel": (get_backend(spec.backend).label if self.known_backend else spec.backend),
            "family": spec.family,
            "description": spec.description,
            "origin": spec.origin,
            "homepage": spec.homepage,
            "license": spec.license,
            "classesLabel": spec.classes_label,
            "source": spec.source,
            "sizeBytes": spec.size_bytes,
            "sizeLabel": _human_size(spec.size_bytes),
            "imgsz": spec.imgsz,
            "defaultConf": spec.default_conf,
            "downloadable": spec.downloadable,
            "installed": self.weights_present,
            "backendReady": self.backend_ready,
            "usable": self.usable,
            "missingRequirements": list(self.missing_requirements),
            "weightsPath": str(self.weights) if self.weights else "",
            "reason": self.reason(),
        }


def _human_size(size: int) -> str:
    if not size:
        return ""
    mo = size / (1024 * 1024)
    return f"{mo:.0f} Mo" if mo >= 10 else f"{mo:.1f} Mo"


class DetectorRegistry:
    def __init__(self) -> None:
        self._lock = threading.RLock()
        self._specs: dict[str, DetectorSpec] = {}
        self._instance: DetectorBackend | None = None
        self._instance_id: str = ""
        self._active_id: str = ""
        self._state: dict[str, Any] = {}
        # sha256 des poids reellement charges, par (chemin, taille, mtime).
        self._weights_sha_cache: dict[tuple, str] = {}
        load_all(store.repo_root())
        self.reload()

    # -- catalogue --------------------------------------------------------

    def reload(self) -> None:
        with self._lock:
            self._specs = {s.id: s for s in catalog.load_specs()}
            self._state = store.read_json(store.state_path()) or {}
            wanted = str(self._state.get("active") or "")
            if wanted in self._specs:
                self._active_id = wanted
            else:
                self._active_id = self._auto_select()

    def _auto_select(self) -> str:
        for detector_id in _PREFERRED:
            status = self.status(detector_id)
            if status and status.usable:
                return detector_id
        for spec in self._specs.values():
            status = self.status(spec.id)
            if status and status.usable:
                return spec.id
        return next(iter(self._specs), "")

    def specs(self) -> list[DetectorSpec]:
        return list(self._specs.values())

    def spec(self, detector_id: str) -> DetectorSpec | None:
        return self._specs.get(detector_id)

    def status(self, detector_id: str) -> DetectorStatus | None:
        spec = self._specs.get(detector_id)
        if spec is None:
            return None
        backend = get_backend(spec.backend)
        # Les poids peuvent venir d'un dossier partage entre plusieurs copies
        # du depot (voir store.model_search_dirs) : un modele deja telecharge
        # ailleurs doit compter comme installe, pas comme absent.
        weights = None
        app_root = store.repo_root()
        for models_dir in store.model_search_dirs():
            weights = spec.resolve_weights(models_dir, app_root)
            if weights is not None:
                break
        missing = backend.missing_requirements() if backend else []
        return DetectorStatus(spec, weights, missing, backend is not None)

    def entries(self) -> list[dict[str, Any]]:
        """Vue serialisable pour QML, modeles utilisables en tete."""
        rows = [s.to_dict() for s in (self.status(i) for i in self._specs) if s]
        rows.sort(key=lambda r: (not r["usable"], not r["installed"], r["label"].lower()))
        return rows

    def backend_report(self) -> list[dict[str, Any]]:
        from fish_detectors.backends import all_backends

        return [
            {
                "id": backend.id,
                "label": backend.label,
                "installed": backend.is_installed(),
                "missingRequirements": backend.missing_requirements(),
            }
            for backend in all_backends().values()
        ]

    def plugin_errors(self) -> list[str]:
        return plugin_errors()

    # -- modele actif -----------------------------------------------------

    @property
    def active_id(self) -> str:
        return self._active_id

    def active_spec(self) -> DetectorSpec | None:
        return self._specs.get(self._active_id)

    def set_active(self, detector_id: str) -> bool:
        with self._lock:
            if detector_id not in self._specs or detector_id == self._active_id:
                return False
            self._active_id = detector_id
            self._release()
            self._save_state()
            return True

    def confidence_for_active(self) -> float:
        spec = self.active_spec()
        return spec.default_conf if spec else 0.25

    def provenance(self, detector_id: str | None = None) -> dict[str, Any]:
        """Identite du modele a graver dans `spatial_annotations` (phase 1).

        Le sha256 du catalogue decrit le fichier attendu au telechargement ; il
        est vide pour les poids livres avec le depot. Dans ce cas on condense le
        fichier reellement charge - une seule fois par (chemin, taille, mtime),
        sans quoi chaque annotation relirait les poids.

        A appeler hors du thread UI : le premier calcul lit le fichier entier.
        """
        spec = self._specs.get(detector_id or self._active_id)
        if spec is None:
            return {"model_id": None, "model_sha256": None}
        sha = (spec.sha256 or "").strip() or self._weights_sha256(spec.id)
        return {"model_id": spec.id, "model_sha256": sha or None}

    def _weights_sha256(self, detector_id: str) -> str:
        weights = self.weights_path(detector_id)
        if weights is None or not weights.is_file():
            return ""
        try:
            stat = weights.stat()
        except OSError:
            return ""
        key = (str(weights), stat.st_size, round(stat.st_mtime, 3))
        with self._lock:
            cached = self._weights_sha_cache.get(key)
            if cached is not None:
                return cached
        digest = hashlib.sha256()
        try:
            with weights.open("rb") as handle:
                for chunk in iter(lambda: handle.read(1 << 20), b""):
                    digest.update(chunk)
        except OSError:
            return ""
        value = digest.hexdigest()
        with self._lock:
            self._weights_sha_cache[key] = value
        return value

    def _save_state(self) -> None:
        self._state["active"] = self._active_id
        store.write_json(store.state_path(), self._state)

    def state_value(self, key: str, default: Any = None) -> Any:
        return self._state.get(key, default)

    def set_state_value(self, key: str, value: Any) -> None:
        with self._lock:
            self._state[key] = value
            store.write_json(store.state_path(), self._state)

    # -- inference --------------------------------------------------------

    def _release(self) -> None:
        if self._instance is not None:
            try:
                self._instance.unload()
            except Exception:
                pass
        self._instance = None
        self._instance_id = ""

    def _ensure_loaded(self, detector_id: str) -> DetectorBackend:
        if self._instance is not None and self._instance_id == detector_id:
            return self._instance
        status = self.status(detector_id)
        if status is None:
            raise RuntimeError(f"Modele inconnu : {detector_id}")
        if not status.backend_ready:
            raise RuntimeError(f"{status.spec.label} - {status.reason()}")
        if not status.weights_present:
            raise FileNotFoundError(f"{status.spec.label} - {status.reason()}")

        backend_cls = get_backend(status.spec.backend)
        weights = status.weights if status.weights is not None else Path(".")
        instance = backend_cls(status.spec, weights)
        instance.load()
        self._release()
        self._instance = instance
        self._instance_id = detector_id
        return instance

    def is_available(self, detector_id: str | None = None) -> bool:
        status = self.status(detector_id or self._active_id)
        return bool(status and status.usable)

    def unavailable_reason(self, detector_id: str | None = None) -> str:
        status = self.status(detector_id or self._active_id)
        if status is None:
            return "Aucun modele de detection configure"
        return status.reason()

    def detect(
        self,
        bgr: np.ndarray,
        conf: float | None = None,
        detector_id: str | None = None,
    ) -> list[dict[str, Any]]:
        target = detector_id or self._active_id
        with self._lock:
            backend = self._ensure_loaded(target)
            threshold = conf if conf is not None else backend.spec.default_conf
            return backend.detect(bgr, float(threshold))

    def class_names(self, detector_id: str | None = None) -> dict[int, str]:
        target = detector_id or self._active_id
        with self._lock:
            return self._ensure_loaded(target).class_names()

    def inspect(self, detector_id: str) -> dict[str, Any]:
        """Charge et teste un modele, puis retourne une fiche serialisable.

        Le test utilise une image noire a la taille recommandee. Il valide le
        chargement, l'appel d'inference et, lorsqu'il existe des detections, le
        contrat de boites consomme par AquaMeasure.
        """
        status = self.status(detector_id)
        if status is None:
            return {
                "id": detector_id,
                "state": "error",
                "compatible": False,
                "inferencePassed": False,
                "error": f"Modele inconnu : {detector_id}",
            }

        backend_cls = get_backend(status.spec.backend)
        result: dict[str, Any] = {
            "id": detector_id,
            "label": status.spec.label,
            "engine": backend_cls.label if backend_cls else status.spec.backend,
            "architecture": status.spec.family or status.spec.backend,
            "task": "détection",
            "taskKey": "detect",
            "classNames": [],
            "classCount": 0,
            "inputSize": int(status.spec.imgsz or 640),
            "compatible": False,
            "inferencePassed": False,
            "state": "error",
            "error": "",
        }
        if not status.usable:
            result["error"] = status.reason()
            return result

        with self._lock:
            try:
                backend = self._ensure_loaded(detector_id)
                result.update(backend.inspection_info())
                names = [str(name) for name in (result.get("classNames") or [])]
                result["classNames"] = names
                result["classNamesLabel"] = ", ".join(names[:12])
                if len(names) > 12:
                    result["classNamesLabel"] += f" … (+{len(names) - 12})"
                task = str(result.get("taskKey") or "detect").lower()
                if task not in {"detect", "segment"}:
                    raise RuntimeError(
                        f"Tâche non compatible avec les boîtes AquaMeasure : {task}"
                    )

                requested_size = int(result.get("inputSize") or 640)
                test_size = min(2048, max(32, requested_size))
                blank = np.zeros((test_size, test_size, 3), dtype=np.uint8)
                raw_boxes = backend.infer(blank, float(status.spec.default_conf))
                if not isinstance(raw_boxes, list):
                    raise RuntimeError("Le backend n'a pas retourne une liste de detections")
                for box in raw_boxes:
                    if not isinstance(box, dict):
                        raise RuntimeError("Une detection du backend n'est pas un objet")
                    missing = [key for key in BBOX_KEYS if key not in box]
                    if missing:
                        raise RuntimeError(
                            "Contrat de detection incomplet : " + ", ".join(missing)
                        )
                boxes = normalize_boxes(
                    raw_boxes,
                    width=test_size,
                    height=test_size,
                    fish_classes=status.spec.fish_classes,
                )

                result.update({
                    "compatible": True,
                    "inferencePassed": True,
                    "state": "success",
                    "detectionsOnTestImage": len(boxes),
                })
            except Exception as exc:
                result.update({
                    "compatible": False,
                    "inferencePassed": False,
                    "state": "error",
                    "error": str(exc),
                })
            finally:
                # Un diagnostic d'un modele non actif ne doit pas le laisser
                # resident en memoire (important pour les gros checkpoints).
                if detector_id != self._active_id and self._instance_id == detector_id:
                    self._release()
        return result

    def weights_path(self, detector_id: str | None = None) -> Path | None:
        status = self.status(detector_id or self._active_id)
        return status.weights if status else None

    def tracking_model(self) -> tuple[Any, DetectorSpec]:
        """Modele natif exploitable par ByteTrack.

        Le tracking passe par Ultralytics : si le modele actif repose sur un
        autre backend, on retombe sur le meilleur modele Ultralytics installe.
        """
        ordered = [self._active_id, *(i for i in self._specs if i != self._active_id)]
        for detector_id in ordered:
            spec = self._specs.get(detector_id)
            backend_cls = get_backend(spec.backend) if spec else None
            if spec is None or backend_cls is None or not backend_cls.supports_tracking:
                continue
            status = self.status(detector_id)
            if not status or not status.usable:
                continue
            with self._lock:
                native = self._ensure_loaded(detector_id).native_model()
            if native is not None:
                return native, spec
        raise RuntimeError(
            "Aucun modele compatible tracking installe "
            "(le tracking ByteTrack exige un modele Ultralytics)"
        )

    # -- installation -----------------------------------------------------

    def download(
        self,
        detector_id: str,
        *,
        progress: Callable[[int, int], None] | None = None,
        cancelled: Callable[[], bool] | None = None,
    ) -> Path:
        spec = self._specs.get(detector_id)
        if spec is None:
            raise downloader.DownloadError(f"Modele inconnu : {detector_id}")
        return downloader.download(
            spec, store.models_dir(), progress=progress, cancelled=cancelled
        )

    def update_catalog(self, url: str | None = None) -> tuple[int, str]:
        target = url or str(self._state.get("catalog_url") or "")
        count, message = catalog.fetch_remote(target)
        if count:
            self.set_state_value("catalog_url", target)
            self.reload()
        return count, message

    def add_user_detector(self, entry: dict[str, Any]) -> DetectorSpec:
        spec = catalog.add_user_spec(entry)
        with self._lock:
            if self._instance_id == spec.id:
                self._release()
            self.reload()
        return spec

    def remove_user_detector(self, detector_id: str) -> bool:
        removed = catalog.remove_user_spec(detector_id)
        if removed:
            with self._lock:
                if self._instance_id == detector_id:
                    self._release()
                self.reload()
        return removed


_registry: DetectorRegistry | None = None
_registry_lock = threading.Lock()


def registry() -> DetectorRegistry:
    global _registry
    with _registry_lock:
        if _registry is None:
            _registry = DetectorRegistry()
        return _registry
