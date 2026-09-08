"""Backend Meta SAM 3 - detection par prompt texte (open vocabulary).

Deux facons de faire tourner SAM 3
----------------------------------
1. **transformers** (voie normale). Hugging Face implemente SAM 3 nativement
   (`transformers/models/sam3`) : `AutoModel` + `AutoProcessor`, puis
   `post_process_object_detection` rend directement des boites et des scores.
   Le paquet demande Python 3.10 ou plus, donc l'environnement actuel de
   l'application suffit.
2. **sam3** (le paquet de Meta, via GitHub). Conserve en repli pour les postes
   ou il est deja installe. Il n'apporte rien de plus ici.

Ce backend annoncait « Python 3.12+ requis » et n'acceptait que la voie 2.
C'etait une exigence inventee : le `pyproject.toml` du depot de Meta declare
`requires-python = ">=3.8"`, et la voie transformers tourne des 3.10. Cette
correction supprime la migration d'environnement qui semblait obligatoire.

Reste **la seule contrainte veritable** : `facebook/sam3` est un depot a acces
controle (gated : manual). Il faut demander l'acces a Meta avec un compte
Hugging Face, puis deposer un jeton. Rien, dans le code, ne peut contourner
cela - et rien ne doit essayer : c'est la licence du modele.
"""

from __future__ import annotations

import importlib.util
import os
import sys
from pathlib import Path
from typing import Any

import cv2
import numpy as np

from fish_detectors.backends.base import DetectorBackend, register_backend

#: Depot Hugging Face par defaut. Surchargeable par modele via
#: `options.hf_model_id`, pour suivre une variante publiee plus tard.
DEFAULT_HF_MODEL = "facebook/sam3"

#: Version minimale de Python. Celle de `transformers`, la voie normale.
MIN_PYTHON = (3, 10)

_TOKEN_ENV_VARS = ("HF_TOKEN", "HUGGING_FACE_HUB_TOKEN", "HUGGINGFACE_TOKEN")


def _python_ok() -> bool:
    return sys.version_info >= MIN_PYTHON


def _has(module: str) -> bool:
    try:
        return importlib.util.find_spec(module) is not None
    except (ImportError, ValueError):
        return False


def token_path() -> Path:
    """Fichier ou `huggingface_hub` lit et ecrit le jeton.

    `HF_HOME` deroute tout le cache Hugging Face, jeton compris. L'ignorer
    ferait ecrire l'application a un endroit que la bibliotheque ne lit pas :
    le jeton semblerait enregistre et l'acces echouerait quand meme.
    """
    hf_home = os.environ.get("HF_HOME", "").strip()
    base = Path(hf_home) if hf_home else Path.home() / ".cache" / "huggingface"
    return base / "token"


def hf_token_present() -> bool:
    """Y a-t-il de quoi s'authentifier aupres de Hugging Face ?

    Sans jeton, le premier chargement echoue sur un 401 apres avoir laisse
    croire que tout etait installe. Le signaler des le catalogue evite de
    perdre l'utilisateur au moment ou il clique.
    """
    for name in _TOKEN_ENV_VARS:
        if os.environ.get(name, "").strip():
            return True
    for candidate in (
        token_path(),
        Path.home() / ".cache" / "huggingface" / "token",
        Path.home() / ".huggingface" / "token",
    ):
        try:
            if candidate.is_file() and candidate.read_text(encoding="utf-8").strip():
                return True
        except OSError:
            continue
    return False


@register_backend
class Sam3Backend(DetectorBackend):
    id = "sam3"
    label = "Meta SAM 3 (prompt texte)"
    # `requires` reste vide : les deux voies n'ont pas les memes paquets, et la
    # verification module par module de la classe de base ne sait pas exprimer
    # un « l'un ou l'autre ». Tout est fait dans missing_requirements.
    requires = ()
    weight_suffixes = ()
    needs_local_weights = False

    @classmethod
    def uses_transformers(cls) -> bool:
        """Voie retenue : transformers d'abord, paquet Meta en repli."""
        return _has("transformers") or not _has("sam3")

    @classmethod
    def missing_requirements(cls) -> list[str]:
        missing: list[str] = []
        if not _python_ok():
            missing.append("python>=%d.%d" % MIN_PYTHON)
        if not _has("torch"):
            missing.append("torch")
        if not _has("PIL"):
            missing.append("pillow")
        # Une seule des deux voies suffit. On reclame transformers, celle qui
        # s'installe d'une commande, sans compilateur ni depot git.
        if not _has("transformers") and not _has("sam3"):
            missing.append("transformers")
        if not hf_token_present():
            missing.append("huggingface-login")
        return missing

    # -- chargement --------------------------------------------------------

    def load(self) -> None:
        self._text_prompt = str(self.spec.options.get("text_prompt") or "fish")
        self._names = {0: self._text_prompt}
        self._device = self._pick_device()
        self._via_transformers = _has("transformers")
        if self._via_transformers:
            self._load_transformers()
        else:
            self._load_meta_package()

    def _load_transformers(self) -> None:
        from transformers import Sam3Model, Sam3Processor

        model_id = str(self.spec.options.get("hf_model_id") or DEFAULT_HF_MODEL)
        self._model = Sam3Model.from_pretrained(model_id).to(self._device)
        self._model.eval()
        self._processor = Sam3Processor.from_pretrained(model_id)

    def _load_meta_package(self) -> None:
        from sam3.model.sam3_image_processor import Sam3Processor
        from sam3.model_builder import build_sam3_image_model

        checkpoint = self.spec.options.get("checkpoint_path")
        kwargs: dict[str, Any] = {
            "device": self._device,
            "load_from_HF": not bool(checkpoint),
            "enable_segmentation": True,
        }
        if checkpoint:
            kwargs["checkpoint_path"] = str(checkpoint)
        self._model = build_sam3_image_model(**kwargs)
        self._processor = Sam3Processor(
            self._model,
            device=self._device,
            confidence_threshold=float(self.spec.default_conf),
        )

    # -- inference ---------------------------------------------------------

    def infer(self, bgr: np.ndarray, conf: float) -> list[dict[str, Any]]:
        if getattr(self, "_via_transformers", False):
            boxes, scores = self._infer_transformers(bgr, conf)
        else:
            boxes, scores = self._infer_meta_package(bgr, conf)
        return self._as_bboxes(boxes, scores)

    def _infer_transformers(self, bgr: np.ndarray, conf: float):
        import torch
        from PIL import Image

        rgb = cv2.cvtColor(bgr, cv2.COLOR_BGR2RGB)
        image = Image.fromarray(rgb)
        inputs = self._processor(
            images=image, text=self._text_prompt, return_tensors="pt",
        ).to(self._device)

        with torch.no_grad():
            outputs = self._model(**inputs)

        height, width = bgr.shape[:2]
        results = self._processor.post_process_object_detection(
            outputs, threshold=float(conf), target_sizes=[(height, width)],
        )
        if not results:
            return None, None
        return results[0].get("boxes"), results[0].get("scores")

    def _infer_meta_package(self, bgr: np.ndarray, conf: float):
        from PIL import Image

        self._processor.confidence_threshold = float(conf)
        rgb = cv2.cvtColor(bgr, cv2.COLOR_BGR2RGB)
        state = self._processor.set_image(Image.fromarray(rgb))
        state = self._processor.set_text_prompt(self._text_prompt, state)
        return state.get("boxes"), state.get("scores")

    def _as_bboxes(self, boxes, scores) -> list[dict[str, Any]]:
        """Boites (x1, y1, x2, y2) + scores -> contrat BBOX_KEYS."""
        if boxes is None or scores is None or len(boxes) == 0:
            return []

        def to_numpy(value):
            return (
                value.detach().cpu().numpy()
                if hasattr(value, "detach") else np.asarray(value)
            )

        boxes_np = to_numpy(boxes)
        scores_np = to_numpy(scores)
        prompt = self._text_prompt
        out: list[dict[str, Any]] = []
        for box, score in zip(boxes_np, scores_np):
            out.append({
                "x1": float(box[0]),
                "y1": float(box[1]),
                "x2": float(box[2]),
                "y2": float(box[3]),
                "conf": float(score),
                "cls_id": 0,
                "cls_name": prompt,
            })
        return out

    # -- diagnostic --------------------------------------------------------

    def inspection_info(self) -> dict[str, Any]:
        info = super().inspection_info()
        voie = "transformers" if getattr(self, "_via_transformers", False) else "sam3 (Meta)"
        info.update({
            "engine": f"SAM 3 via {voie}",
            "architecture": "SAM 3",
            "task": "détection guidée par texte",
            "taskKey": "detect",
            "classNames": [self._text_prompt],
            "classCount": 1,
            "prompt": self._text_prompt,
        })
        return info

    def unload(self) -> None:
        self._model = None
        self._processor = None
        super().unload()
