# AquaMeasure Évolutions de septembre 2026

Les changements de la version du 8 septembre 2026 concernent l’espace de mesure, les annotations, les exports et la livraison. Le manuel utilisateur décrit les gestes ; cette note permet de repérer ce qui a changé.

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
