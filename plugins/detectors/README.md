# Plugins de détection

Tout fichier `.py` de ce dossier est chargé au démarrage d'AquaMeasure, avant la
lecture du catalogue. C'est le point d'extension pour brancher une **nouvelle
architecture** de détection sans modifier le cœur de l'application.

Un fichier dont le nom commence par `_` est ignoré : c'est ainsi qu'on désactive
un plugin sans le supprimer.

Voir `_exemple_backend.py` pour un squelette commenté, et
`aquameasure-pyside/docs/DETECTEURS.md` pour le guide complet.

Pour charger les plugins depuis un autre dossier (poste client, dossier réseau),
définir la variable d'environnement `AQUAMEASURE_DETECTOR_PLUGINS` avec un ou
plusieurs chemins séparés par `;`.

Ajouter un simple **modèle** (mêmes architectures déjà connues) ne passe pas par
ici : il suffit d'une entrée JSON dans `camera_parameters/detectors.local.json`.
