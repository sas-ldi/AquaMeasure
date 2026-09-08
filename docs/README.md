# Documentation AquaMeasure

Huit guides courants, disponibles en ligne, en Word et en PDF. Commencer par le manuel utilisateur ; les notes techniques complètent les calculs et le code.

## Utilisation

| Guide | Contenu | Word | PDF |
|---|---|---|---|
| [Manuel utilisateur](MANUEL_UTILISATEUR.md) | Sessions, synchronisation, calibration, mesure et suivi | [Télécharger](word/01_Utilisation/AquaMeasure_Manuel_Utilisateur.docx) | [Lire](pdf/01_Utilisation/AquaMeasure_Manuel_Utilisateur.pdf) |
| [Modèles IA](DETECTEURS.md) | Charger des poids, mettre à jour les modèles et transférer Fishial | [Télécharger](word/01_Utilisation/AquaMeasure_Guide_Extension_Modeles_Detection.docx) | [Lire](pdf/01_Utilisation/AquaMeasure_Guide_Extension_Modeles_Detection.pdf) |

## Calibration et mesure

| Guide | Contenu | Word | PDF |
|---|---|---|---|
| [Calibration stéréo](methodes/CALIBRATION_STEREO.md) | Mire, modèle géométrique, scan et contrôles de calibration | [Télécharger](word/02_Calibration_et_mesure/AquaMeasure_Note_Calibration_Stereo.docx) | [Lire](pdf/02_Calibration_et_mesure/AquaMeasure_Note_Calibration_Stereo.pdf) |
| [Mesure stéréo](methodes/MESURE_STEREO.md) | Triangulation, disparité, erreurs et vérification des longueurs | [Télécharger](word/02_Calibration_et_mesure/AquaMeasure_Note_Mesure_Stereo.docx) | [Lire](pdf/02_Calibration_et_mesure/AquaMeasure_Note_Mesure_Stereo.pdf) |

## Code et données

| Guide | Contenu | Word | PDF |
|---|---|---|---|
| [Guide du code](GUIDE_DEVELOPPEUR.md) | Organisation du projet, contrôleurs, tests et compilation | [Télécharger](word/03_Code_et_donnees/AquaMeasure_Guide_Developpeur.docx) | [Lire](pdf/03_Code_et_donnees/AquaMeasure_Guide_Developpeur.pdf) |
| [Base de données](technique/BASE_DE_DONNEES.md) | Schéma, coordonnées, observations, pistes et références Fishial | [Télécharger](word/03_Code_et_donnees/AquaMeasure_Note_Base_De_Donnees.docx) | [Lire](pdf/03_Code_et_donnees/AquaMeasure_Note_Base_De_Donnees.pdf) |
| [Exports et jeux de données](technique/EXPORTS_DATASETS.md) | Tables CSV, COCO, événements COCO-VID et formats techniques | [Télécharger](word/03_Code_et_donnees/AquaMeasure_Note_Exports_Datasets.docx) | [Lire](pdf/03_Code_et_donnees/AquaMeasure_Note_Exports_Datasets.pdf) |

## Évolutions

| Guide | Contenu | Word | PDF |
|---|---|---|---|
| [Évolutions de septembre 2026](evolutions/EVOLUTIONS_2026_09.md) | Changements de l’interface, du suivi, des exports et du dépôt | [Télécharger](word/04_Evolutions/AquaMeasure_Evolutions_2026_09.docx) | [Lire](pdf/04_Evolutions/AquaMeasure_Evolutions_2026_09.pdf) |

Les captures proviennent de l’interface du 8 septembre 2026, avec une copie de session de démonstration. Les mesures et espèces fictives illustrent les commandes.

Les anciens guides complets et leurs versions successives ne sont pas repris. Les explications générales de détection sont intégrées au guide des modèles et au guide du code.

`documents.json` liste les sources et les catégories. `build_word.py` régénère les Word ; les PDF utilisent le même classement.
