# AquaMeasure Exports et jeux de données

Cette note décrit les fichiers exportés et les clés qui permettent de les relier. Pour analyser une session, partir du paquet CSV complet. Pour transmettre des images annotées, utiliser COCO ou COCO-VID. La sauvegarde du travail dans AquaMeasure reste une opération distincte.

## Exporter toute la session

Dans Données & IA, choisir l’export de la session. Le paquet rassemble les médias de toutes ses prises, pas seulement la paire ouverte. Si une session contient huit paires, les huit sont prises en compte selon le format et les annotations disponibles.

Garder le disque des vidéos branché jusqu’à la fin des exports qui extraient des images. COCO et COCO-VID écrivent leurs images dans le dossier exporté. Une fois l’opération terminée et contrôlée, ces images et annotations peuvent être utilisées sans les vidéos d’origine. Elles ne contiennent cependant pas toute la vidéo et ne permettent pas de reprendre n’importe quel instant dans AquaMeasure.

Un export accepté avec des médias absents est partiel. Le fichier `session.json` indique les médias omis. Vérifier ce fichier et le rapport avant de considérer la session entièrement exportée.

## Le paquet CSV pour les analyses

Le format `aquameasure.session_csv.v2` sépare les données selon ce que représente une ligne. Cela évite de répéter une mesure pour chaque position de piste ou pour chaque bouchée.

| Fichier | Une ligne représente |
|---|---|
| session.csv | Une observation, son identification, sa longueur éventuelle et son lien de piste. |
| pistes.csv | Une piste, même sans mesure ou sans position enregistrée. |
| positions_pistes.csv | Une position de piste sur une image, avec les coordonnées disponibles. |
| evenements.csv | Une action ponctuelle ou une durée, sur fiche ou sur piste, écrite une seule fois. |
| comptages.csv | Un comptage par image, ses valeurs IA et manuelle et son état de validation. |
| videos.csv | Une vidéo attachée à la session et ses informations de prise. |
| resume_session.csv | La session et ses effectifs enregistrés. |
| dictionnaire.csv | La définition d’une colonne, son unité et le fichier concerné. |

Les clés `annotation_id`, `track_id` et `media_id` relient les tables. Pour compter les actions, utiliser `evenements.csv`. Une piste liée à plusieurs observations ne doit pas être comptée plusieurs fois par une jointure. `fish_key` regroupe une identité de suivi ; il ne prouve pas que deux pistes distinctes correspondent à deux individus biologiques différents.

Les longueurs conservent leur précision. Les valeurs absentes restent vides, notamment les temps si la cadence n’est pas connue. Toutes les positions enregistrées sont écrites, sans interpolation ni décimation nouvelle. Les coordonnées 3D figurent lorsqu’elles existent ; une trajectoire en pixels ne devient pas automatiquement une trajectoire 3D.

Le MaxN utilise les comptages validés, avec priorité à la correction manuelle, y compris zéro. Sans comptage validé, il reste vide. Le paquet fournit les données et les effectifs de session ; il ne produit pas de tests statistiques ni de résultats d’étude à la place de l’utilisateur.

Pour Excel, importer en UTF-8, avec la virgule comme séparateur et le point comme séparateur décimal. Les fichiers contiennent un BOM UTF-8. Une apostrophe initiale protège les textes qui pourraient être interprétés comme des formules.

## Les actions dans COCO vidéo

COCO-VID contient les boîtes successives et l’identité des pistes. AquaMeasure ajoute l’extension `aquameasure.track_events.v1` pour conserver les actions ponctuelles et les durées. Une bouchée marquée sur une trajectoire garde donc son image exacte.

Les événements sont présents dans `coco_vid.json`, sous `events`, et dans `track_events.jsonl`. Le type conserve son nom, sa portée, son symbole et sa couleur, y compris pour un type personnalisé. La provenance distingue les annotations manuelles des résultats automatiques.

| Champ | Sens |
|---|---|
| track_id | Identifiant entier de la piste dans COCO-VID. |
| track_db_id | Identifiant conservé depuis la base AquaMeasure. |
| frame_index | Image exacte d’une action ponctuelle. |
| frame_start et frame_end | Bornes incluses d’une action sur une durée. |
| event_ids | Liens des pistes et des boîtes vers les actions concernées. |

Les index absolus commencent à zéro dans le fichier vidéo. Les temps en secondes utilisent sa cadence. Si une image d’action manque dans la sélection décimée du suivi, son JPEG est extrait dans `event_images/`. Cette extraction n’invente pas une boîte sur cette image.

Une frame hors vidéo garde son index avec un chemin d’image vide. Les événements non exportables, notamment lorsque l’offset historique est indéterminable, figurent dans `excluded_events`. Le rapport signale les images manquantes.

Ces champs sont une extension AquaMeasure. Un logiciel qui lit seulement le COCO-VID standard peut les ignorer : lire `events` ou `track_events.jsonl` pour exploiter les comportements.

## Les autres formats

| Format | Utilisation |
|---|---|
| COCO | Détection avec images, boîtes, catégories et zones ignorées. |
| YOLO | Détection avec labels normalisés et fichier data.yaml. |
| MOT | Suivi avec séquences et correspondance des numéros d’image. |
| AVA et Events JSONL | Actions temporelles et informations de provenance. |
| Crops de classification | Clichés recadrés des observations retenues et métadonnées. |
| Bibliothèque Fishial | Transfert des taxons, vecteurs et clichés locaux compatibles entre postes. |

Certains formats sont accessibles par les outils avancés ou les scripts. Ils n’ont pas tous un bouton séparé. Le transfert de bibliothèque Fishial et l’export de clichés pour entraîner un modèle ont des contenus différents ; le [guide des modèles](../DETECTEURS.md) explique leur usage.

## Construire un jeu fiable

Les exports de détection peuvent sélectionner le rang poisson, famille, genre ou espèce. Vérifier le statut de revue : une proposition IA n’est pas une identification humaine. Conserver les zones ignorées pour ne pas transformer un poisson visible mais non retenu en exemple négatif involontaire.

Une boîte rectifiée doit accompagner l’image rectifiée correspondante ou être convertie avec sa calibration. Les anciennes annotations en timeline demandent le décalage figé de la prise pour retrouver les images absolues. Une conversion impossible est signalée, sans inventer de numéro.

Séparer entraînement et évaluation par vidéo ou par session selon le protocole. Des images voisines du même poisson dans les deux partitions rendent l’évaluation trop favorable. Garder les filtres, classes, partitions et empreintes dans le manifeste.

Avant de transmettre un export, retrouver une mesure connue, vérifier une piste et quelques actions, puis ouvrir leurs images. Conserver les manifestes avec le paquet. Pour reprendre la session dans AquaMeasure, sauvegarder aussi la base, les vidéos et les calibrations.

## Points de modification dans le code

Le paquet CSV est défini dans [export_session_tables.py](../../src/annotations/src/annodb/export_session_tables.py). Les événements de suivi sont assemblés par [export_behavior.py](../../src/annotations/src/annodb/export_behavior.py) et écrits par [export_tracking.py](../../src/annotations/src/annodb/export_tracking.py). L’export de toutes les prises passe par [export_session.py](../../src/annotations/scripts/export_session.py).

Ajouter une colonne en base ne l’ajoute pas automatiquement aux exports. Adapter les listes de champs et les tests du format concerné, en conservant les anciennes données et les valeurs manquantes.
