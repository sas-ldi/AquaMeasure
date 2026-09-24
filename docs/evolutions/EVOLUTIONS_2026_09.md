# AquaMeasure Évolutions de septembre 2026

Cette note recense les changements des versions du 24 et du 8 septembre 2026. Le manuel utilisateur décrit les gestes ; cette note permet de repérer ce qui a changé.

## Version du 24 septembre 2026

Ces changements font suite à la première sortie de calibration en mer.

### Dossier de calibration

Une fois installée, l'application rangeait la calibration à côté du programme et y cherchait la fenêtre In/Out, alors que le reste des données se trouve dans le dossier choisi (par défaut Documents\AquaMeasure - Donnees). Le bouton Passer à la mesure restait grisé après une calibration réussie, et la fenêtre était ignorée : deux passages de mire donnaient le même résultat. La calibration utilise désormais le dossier des données. Une calibration qui n'aboutit pas l'indique au lieu de réafficher le résultat précédent.

### Mires préréglées

Paramètres ChArUco propose deux mires : la petite mire A3 (5×7 cases de 49,5 mm, marqueurs de 37 mm) et la grande mire de 84 cm (20×7 cases de 39 mm, marqueurs de 29 mm), toutes deux en DICT_5X5_100. Un clic remplit colonnes, lignes, tailles et dictionnaire. Les colonnes correspondent aux cases dans la largeur du fichier imprimé ; inversées, aucun coin n'est détecté. La mire choisie est retenue au lancement suivant.

### Résultat de calibration

La rotation relative est détaillée en horizontal, vertical et roulis. Sur un support fixe, l'écartement des caméras se lit dans la part horizontale ; le vertical et le roulis restent proches de zéro. Comparer ces valeurs entre passages de mire indique si la calibration est stable. L'aperçu de détection ne reprend plus l'image du calcul précédent.

### Synchronisation

La fenêtre In/Out est enregistrée dès que la poignée est relâchée, et la calibration utilise donc la fenêtre affichée. Le bouton Enregistrer In-Out reste disponible. Une autre paire de vidéos, par exemple l'extrait d'un seul passage de mire, repart sur la vidéo entière et sans synchro. Après une poignée In/Out, la vue revient à son image : l'application ne propose plus de valider un faux décalage.

### Sessions

Le bouton Enregistrer session disparaît. Lieu, titre, date et notes s'enregistrent en quittant le champ. La dernière session se rouvre au démarrage ; les vidéos ouvertes la rejoignent, ou rouvrent la session qui les contient. La page Sessions permet de déplacer une prise vers une autre session avec ses mesures. Le décalage de synchro d'une prise n'est plus réécrit ; refaire la synchro le met à jour.

### Installation

L'édition légère, d'environ 900 Mo, contient la suite Fishial et les petits détecteurs. Les autres modèles s'ajoutent depuis l'onglet Modèles IA. Installée par-dessus une version précédente, elle conserve modèles et données.

## Version du 8 septembre 2026

## Interface et fiche poisson

La fiche, la mesure, le suivi et les événements sont regroupés à gauche des vidéos. Le registre compact reste à droite ; le détail se trouve dans Données & IA. Les réglages se replient dans un tiroir. Des sections et icônes rendent les autres pages et les préférences plus lisibles.

Enregistrer le poisson crée une fiche ou corrige celle qui est sélectionnée. Le nom confirmé reste prioritaire pendant le suivi. Un cadre automatique peut être redimensionné au clic droit en conservant son identification.

## Comportement et trajectoire

**Comportement** enregistre une action à un instant ou sur une durée. **Trajectoire** suit le déplacement entre In et Out ; on peut ensuite y pointer des bouchées image par image. Bouchée ponctuel est fourni par défaut. Les types personnalisés et raccourcis se règlent dans les préférences.

Pour amorcer le suivi, partir d’un cadre localisé et d’une fiche enregistrée. La longueur est facultative. Les points sont enregistrés immédiatement et leurs symboles apparaissent en relecture. Les conversions de coordonnées ont été corrigées pour conserver leur position lors du passage pause et lecture, avec zoom et déplacement.

## Calibration

Le panneau sépare densité du scan, précision du calcul et montage des caméras. Le bouton Passer à la mesure a été corrigé. Le calcul reste fondé sur les coins ChArUco et la géométrie optique. La durée dépend de la vidéo et des réglages ; contrôler le résultat avec les indicateurs et un objet connu.

## Exports et Fishial

Le paquet CSV contient mesures, pistes, positions, événements, comptages, vidéos, résumé et dictionnaire des colonnes. Les actions personnalisées et points sans piste sont conservés. Le MaxN utilise les comptages validés, y compris zéro.

COCO-VID conserve les actions sur piste et leurs images exactes dans l’extension AquaMeasure. L’export de session couvre toutes ses prises et signale les médias absents. Les boutons d’export restent visibles quand la liste des espèces défile.

Fishial accepte de nouvelles images par espèce ou en une fois, avec seuil et déduplication. Cela complète les références locales sans remplacer les poids. L’export puis l’import de bibliothèque transfère taxons, vecteurs et clichés disponibles vers un poste compatible.

## Livraison et dépôt source

L’installation Windows utilise le logo IRD et démarre avec une base vide. Le référentiel de 2 438 noms d’espèces est fourni ; les observations et références de démonstration sont exclues.

Le dépôt sépare `src`, `tests`, `docs` et `packaging`. L’ancien module fish-vision est devenu `src/annotations` : sa base et ses exports restent utilisés. Les lanceurs en double, mires d’essai et configurations locales d’entraînement ont été retirés. Les mires se génèrent depuis Calibration.

Les huit guides sont classés par thème et accessibles depuis l’accueil GitHub. Le dépôt contient les sources et manuels, sans exécutables, poids IA, vidéos ni bases de travail.
