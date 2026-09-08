"""Squelette de backend de detection - renommer sans le « _ » pour l'activer.

Ce fichier montre le contrat minimal a respecter. La classe de base s'occupe du
reste : bornage des boites a l'image, filtrage des classes non-poisson declarees
dans le catalogue, tri par confiance, choix CPU/GPU.
"""

from __future__ import annotations

from typing import Any

import numpy as np

from fish_detectors.backends.base import DetectorBackend, register_backend


@register_backend
class ExempleBackend(DetectorBackend):
    #: identifiant reference par le champ "backend" des entrees de catalogue
    id = "exemple"
    #: libelle affiche dans « Étendre → Moteurs d'inference »
    label = "Exemple (squelette)"
    #: modules pip necessaires ; s'ils manquent, l'UI affiche la commande a lancer
    requires = ()
    #: extensions de poids attendues, a titre indicatif
    weight_suffixes = (".pt",)

    def load(self) -> None:
        """Charge le modele. Appele une seule fois, dans un thread de travail.

        `self.weights` est le chemin des poids resolu par le registre et
        `self.spec` l'entree de catalogue (imgsz, options, seuil par defaut...).
        """
        self._model = None
        self._names = {0: "fish"}

    def infer(self, bgr: np.ndarray, conf: float) -> list[dict[str, Any]]:
        """Inference brute sur une image BGR OpenCV, en coordonnees pixel.

        Retourner une liste de dictionnaires ; les cles hors contrat sont
        conservees telles quelles jusqu'a l'overlay et au registre.
        """
        return [{
            "x1": 0.0,
            "y1": 0.0,
            "x2": 10.0,
            "y2": 10.0,
            "conf": 0.99,
            "cls_id": 0,
            "cls_name": "fish",
        }]

    def unload(self) -> None:
        """Libere la memoire quand l'utilisateur change de modele."""
        self._model = None
        super().unload()
