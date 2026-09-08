# Composants tiers

La [licence AquaMeasure](LICENSE) concerne le code propre au projet. Elle ne change pas les droits accordés par les projets ci-dessous. Leurs bibliothèques et poids sont installés séparément ; ce dépôt ne les redistribue pas, à l’exception de la liste de noms Fishial mentionnée plus bas.

## Interfaces et calculs

| Composant | Usage | Licence ou référence officielle |
|---|---|---|
| Qt / PySide6 | Interface et lecteurs vidéo | [LGPLv3, GPLv3 ou licence commerciale, selon les composants](https://doc.qt.io/qtforpython-6/licenses.html) |
| OpenCV | Calibration et traitement des images | [Apache 2.0 pour les versions utilisées](https://github.com/opencv/opencv/blob/4.x/LICENSE) |
| NumPy et SciPy | Calcul numérique | [NumPy](https://github.com/numpy/numpy/blob/main/LICENSE.txt), [SciPy](https://github.com/scipy/scipy/blob/main/LICENSE.txt), BSD |
| SQLAlchemy | Base d’annotations | [MIT](https://github.com/sqlalchemy/sqlalchemy/blob/main/LICENSE) |
| PyTorch | Inférence IA | [Licence PyTorch et notices associées](https://github.com/pytorch/pytorch/blob/main/LICENSE) |
| ONNX Runtime | Inférence des modèles ONNX | [MIT](https://github.com/microsoft/onnxruntime/blob/main/LICENSE) |
| Transformers | Intégration SAM 3 | [Apache 2.0](https://github.com/huggingface/transformers/blob/main/LICENSE) |

## Détection et classification

| Composant | Conditions à consulter |
|---|---|
| Ultralytics et YOLOv5 | [AGPL-3.0](https://github.com/ultralytics/ultralytics/blob/main/LICENSE), ou accord distinct auprès d’[Ultralytics](https://www.ultralytics.com/license). |
| Fishial | [Code et liste de noms sous MIT](https://github.com/fishial/fish-identification). Consulter aussi les conditions du pack de poids choisi ; la licence du code ne suffit pas à déterminer celle de tous les poids ou jeux d’images. |
| Community Fish Detector | [Conditions par modèle](https://github.com/filippovarini/community-fish-detector). Le projet distingue le moteur RF-DETR sous Apache et le moteur YOLO sous AGPL ; cela ne décrit pas les licences des données d’entraînement. |
| MegaFishDetector et MBARI / FathomNet | Consulter la page d’origine de chaque modèle dans le [catalogue](fish_detectors/catalog.json). |
| SAM 3 | [Licence et accès Meta](https://huggingface.co/facebook/sam3). L’accès aux poids reste personnel. |

**Point de compatibilité :** l’AGPL autorise l’usage commercial et interdit de restreindre les droits qu’elle accorde. La clause non commerciale d’AquaMeasure ne peut pas s’appliquer à une œuvre couverte par cette licence. Avant de redistribuer une application combinant AquaMeasure et Ultralytics, il faut résoudre cette compatibilité : accord de licence distinct, adaptation des composants ou licence compatible pour l’œuvre combinée. Publier uniquement les sources ne supprime pas cette question. Voir les sections 5 et 10 du [texte AGPL](https://github.com/ultralytics/ultralytics/blob/main/LICENSE).

## Fichier Fishial fourni

`fish-vision/models/fishial_labels.json` contient la liste de 775 noms scientifiques utilisée par cette version du référentiel. Elle provient de [Fishial.AI](https://github.com/fishial/fish-identification/blob/main/labels.json), Copyright (c) 2021 Wye Foundation - Fishial.AI Project. Le texte de sa licence est conservé dans [LICENSES/Fishial-MIT.txt](LICENSES/Fishial-MIT.txt).

Ce fichier contient des noms d’espèces, pas les images ou les vecteurs créés par les utilisateurs. Les captures des manuels sont des illustrations de démonstration. Le logo IRD conserve son attribution institutionnelle.
