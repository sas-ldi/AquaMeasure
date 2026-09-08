"""Classification espece Fishial (866 classes, MIT) — crops depuis detection YOLO."""

from __future__ import annotations

import importlib.util
import json
import sys
import threading
from pathlib import Path
from typing import Any, Optional

import cv2
import numpy as np

from app_paths import app_root

_APP_ROOT = Path(app_root())
_engine = None
_engine_lock = threading.Lock()
_model_dir: str | None = None
_input_size: tuple[int, int] = (154, 434)


def _models_root() -> Path:
    return _APP_ROOT / "fish-vision" / "models"


def default_model_dir() -> Path:
    for name in (
        "fishial_classification_v0.10.2",
        "fishial_classification_v0.10",
    ):
        p = _models_root() / name
        if (p / "model.pt").is_file():
            return p
        if p.is_dir() and any(p.rglob("model.pt")):
            return next(p.rglob("model.pt")).parent
    return _models_root() / "fishial_classification_v0.10.2"


def _labels_path() -> Path:
    p = _models_root() / "fishial_labels.json"
    return p if p.is_file() else _models_root() / "fishial_labels.json"


def is_available() -> bool:
    try:
        import torch  # noqa: F401
    except ImportError:
        return False
    d = default_model_dir()
    return (d / "model.pt").is_file()


def _load_inference_module(model_dir: Path):
    path = model_dir / "inference.py"
    if not path.is_file():
        raise FileNotFoundError(f"inference.py introuvable dans {model_dir}")
    spec = importlib.util.spec_from_file_location("fishial_inference", path)
    if spec is None or spec.loader is None:
        raise ImportError(f"Impossible de charger {path}")
    mod = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = mod
    spec.loader.exec_module(mod)
    return mod


def _read_input_size(model_dir: Path) -> tuple[int, int]:
    info = model_dir / "info.json"
    if info.is_file():
        try:
            data = json.loads(info.read_text(encoding="utf-8"))
            sz = data.get("image_size")
            if isinstance(sz, (list, tuple)) and len(sz) == 2:
                return int(sz[0]), int(sz[1])
        except (json.JSONDecodeError, TypeError, ValueError):
            pass
    return (154, 434)


def _pick_device() -> str:
    try:
        import torch
        if torch.cuda.is_available():
            return "cuda"
    except ImportError:
        pass
    return "cpu"


def _get_engine():
    # Le prechauffage et une detection peuvent demander le meme modele.
    with _engine_lock:
        return _load_engine()


def _load_engine():
    global _engine, _model_dir, _input_size
    model_dir = default_model_dir()
    key = str(model_dir.resolve())
    if _engine is not None and _model_dir == key:
        return _engine

    bundle = model_dir / "model.pt"
    if not bundle.is_file():
        raise FileNotFoundError(
            f"model.pt introuvable dans {model_dir}. "
            "Lancez : fish-vision\\scripts\\download_fishial.bat"
        )

    fishial_inf = _load_inference_module(model_dir)
    device = _pick_device()
    _input_size = _read_input_size(model_dir)
    _engine = fishial_inf.FishInferenceEngine.from_bundle(
        str(bundle),
        input_size=_input_size,
        device=device,
    )
    _model_dir = key
    return _engine


def _crop_box(bgr: np.ndarray, box: dict[str, Any], pad: float = 0.08) -> Optional[np.ndarray]:
    h, w = bgr.shape[:2]
    x1, y1, x2, y2 = float(box["x1"]), float(box["y1"]), float(box["x2"]), float(box["y2"])
    bw, bh = max(1.0, x2 - x1), max(1.0, y2 - y1)
    x1 = max(0, int(x1 - bw * pad))
    y1 = max(0, int(y1 - bh * pad))
    x2 = min(w, int(x2 + bw * pad))
    y2 = min(h, int(y2 + bh * pad))
    if x2 <= x1 or y2 <= y1:
        return None
    crop = bgr[y1:y2, x1:x2].copy()
    rgb = cv2.cvtColor(crop, cv2.COLOR_BGR2RGB)
    return rgb


def classify_crop(bgr: np.ndarray, box: Optional[dict[str, Any]] = None) -> dict[str, Any]:
    """Classifie un crop BGR (ou image entiere + bbox) -> espece Fishial."""
    empty = {"species_name": None, "species_conf": 0.0, "species_top3": []}
    if bgr is None or bgr.size == 0:
        return empty

    engine = _get_engine()
    if box is not None:
        rgb = _crop_box(bgr, box)
        bbox = [0.0, 0.0, float(rgb.shape[1]), float(rgb.shape[0])] if rgb is not None else None
    else:
        rgb = cv2.cvtColor(bgr, cv2.COLOR_BGR2RGB)
        bbox = [0.0, 0.0, float(rgb.shape[1]), float(rgb.shape[0])]

    if rgb is None or rgb.size == 0:
        return empty

    try:
        result = engine.predict_single(
            rgb,
            bbox=bbox,
            method="natural_centroid",
        )
    except Exception:
        result = engine.predict_single(
            rgb,
            bbox=bbox,
            method="arcface_logits",
        )

    top3 = []
    for pred in (result.top_k or [])[:3]:
        top3.append({
            "species_name": pred.name,
            "species_conf": round(float(pred.accuracy), 3),
        })
    best = result.best
    if best is None:
        return empty
    return {
        "species_name": best.name,
        "species_conf": round(float(best.accuracy), 3),
        "species_top3": top3,
    }


def classify_boxes(
    bgr: np.ndarray,
    boxes: list[dict[str, Any]],
    *,
    use_gallery: bool = True,
) -> list[dict[str, Any]]:
    """Enrichit chaque bbox avec species_name / species_conf."""
    if not boxes:
        return boxes
    if use_gallery:
        try:
            import fishial_gallery as _fg
            if _fg.gallery_has_entries():
                return _fg.classify_boxes_with_gallery(bgr, boxes)
        except ImportError:
            pass
    if not is_available():
        return boxes
    out: list[dict[str, Any]] = []
    for box in boxes:
        enriched = dict(box)
        try:
            enriched.update(classify_crop(bgr, box))
        except Exception as exc:
            enriched["species_error"] = str(exc)
        out.append(enriched)
    return out


def embed_crop(bgr: np.ndarray, box: Optional[dict[str, Any]] = None) -> Optional[np.ndarray]:
    """Extrait un vecteur embedding Fishial (512-D) depuis un crop BGR."""
    if bgr is None or bgr.size == 0:
        return None
    try:
        engine = _get_engine()
    except Exception:
        return None

    if box is not None:
        rgb = _crop_box(bgr, box)
    else:
        rgb = cv2.cvtColor(bgr, cv2.COLOR_BGR2RGB)
    if rgb is None or rgb.size == 0:
        return None

    bbox = [0.0, 0.0, float(rgb.shape[1]), float(rgb.shape[0])]

    for method_name in ("extract_embedding", "get_embedding", "embed", "compute_embedding"):
        fn = getattr(engine, method_name, None)
        if callable(fn):
            try:
                vec = fn(rgb, bbox=bbox)
                if vec is not None:
                    arr = np.asarray(vec, dtype=np.float32).reshape(-1)
                    if arr.size > 0:
                        return arr
            except TypeError:
                try:
                    vec = fn(rgb)
                    if vec is not None:
                        arr = np.asarray(vec, dtype=np.float32).reshape(-1)
                        if arr.size > 0:
                            return arr
                except Exception:
                    pass
            except Exception:
                pass

    try:
        result = engine.predict_single(rgb, bbox=bbox, method="natural_centroid")
        for attr in ("embedding", "feature", "features", "vector"):
            vec = getattr(result, attr, None)
            if vec is not None:
                arr = np.asarray(vec, dtype=np.float32).reshape(-1)
                if arr.size > 0:
                    return arr
    except Exception:
        pass
    return None
