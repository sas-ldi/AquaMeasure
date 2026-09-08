# AquaMeasure Calibration stéréo

Cette note explique le calcul de calibration et les contrôles à faire avant de mesurer. Elle complète les gestes du manuel utilisateur. Une calibration cohérente doit aussi retrouver la longueur d’un objet connu dans les conditions de la campagne.

## Ce que la calibration détermine

La calibration estime la géométrie du montage : paramètres de chaque objectif, distorsion, rotation et translation entre les caméras. Les matrices servent ensuite à rectifier les images et à trianguler les points de mesure.

L'échelle vient de la taille des cases de la mire. Une erreur de 2 % sur cette taille entraîne environ 2 % d'erreur sur les longueurs, même si la reprojection paraît bonne.

## Préparer une acquisition utile

Utiliser une mire ChArUco plane et rigide. Mesurer les cases réellement imprimées ; vérifier le nombre de cases, le dictionnaire et la taille des marqueurs dans les réglages. Les valeurs proposées par défaut ne prouvent pas qu'elles correspondent à la planche.

Filmer la mire dans les deux caméras à la fois, à plusieurs distances et inclinaisons, au centre et près des bords. Garder des images nettes. Éviter une séquence composée uniquement de vues presque identiques.

Le montage et les conditions optiques doivent correspondre à la mesure : résolution, mode caméra, position du support et immersion. Recalibrer après déplacement du support. La réfraction dans les caissons rend une calibration hors de l'eau peu représentative d'une mesure immergée.

## Calcul effectué par AquaMeasure

1. Les vidéos synchronisées sont parcourues pour détecter les marqueurs et coins ChArUco.
2. Les observations sont mises en cache et sélectionnées pour couvrir des positions variées.
3. Chaque caméra est calibrée à partir des points image et de la géométrie connue de la mire.
4. Les coins communs aux deux vues servent au calcul stéréo.
5. Le calcul robuste écarte des paires problématiques et produit les paramètres, indicateurs et vues de contrôle.

Le moteur de scan rapide et la sélection limitent le coût du calcul sans demander à l'utilisateur de pointer les coins. Affiner le scan peut retrouver plus de vues, mais ne répare pas une mire floue ou filmée dans une seule position.

## Scan et précision

La densité du scan règle les images analysées : saut lorsque la mire est absente, rafale après une détection et pas dans cette rafale. Les limites de vues et de paires règlent ensuite le volume envoyé au calcul OpenCV. Les réglages rapides réduisent le travail demandé ; la durée dépend aussi de la vidéo et du nombre de détections.

Ces réglages sont maintenant regroupés dans le panneau de calibration. Le résultat reste fondé sur les coins de la mire et les paramètres optiques ; changer la présentation du panneau ne constitue pas une nouvelle méthode de calibration.

## Modèle géométrique

La projection s'écrit, à un facteur d'échelle près :

```text
s [u, v, 1]ᵀ = K [R | t] [X, Y, Z, 1]ᵀ
```

K contient notamment les focales en pixels et le point principal. R et t décrivent une pose. Les coefficients de distorsion corrigent les écarts au modèle de projection idéal. Le calcul minimise les différences entre les coins observés et leurs projections.

Le calcul stéréo fournit une rotation et une translation entre les caméras. La norme de la translation est l'écartement, dans l'unité utilisée pour décrire la mire. Les matrices de rectification et de projection sont calculées à partir de cet ensemble.

## Lire les contrôles

| Indicateur | Ce qu'il permet de vérifier |
|---|---|
| RMSE gauche et droite | Accord des projections avec les coins de chaque caméra. |
| RMSE stéréo | Accord global des correspondances entre caméras. |
| Écartement | Cohérence avec la distance physique entre les objectifs. |
| Écart vertical après rectification | Qualité de l'alignement des points correspondants. |
| Mesure d'un objet connu | Justesse de la chaîne complète à la distance de travail. |

Les paliers de verdict dans l'interface sont des aides au diagnostic. Ils ne donnent pas une incertitude de longueur. Examiner aussi la couverture spatiale des vues utilisées et les paires rejetées.

Un objet connu doit être mesuré à plusieurs endroits du champ et à plusieurs distances. Relever les erreurs, puis fixer la plage d'utilisation acceptable pour la campagne. Un essai uniquement au centre et près de la caméra masque les problèmes de bord ou de profondeur.

## Fichiers et profils

Le dossier camera_parameters de la racine de données contient la calibration active : matrices intrinsèques, distorsion, rotation, translation et métadonnées. Exporter un ZIP permet de conserver un jeu cohérent pour le réimporter plus tard. Les diagnostics peuvent inclure un dossier verify et un fichier calib_meta.json.

La prise de la session conserve une référence au profil et à son empreinte. La réouverture d’une session ne recharge pas automatiquement un ancien ZIP : si la calibration active a changé, réimporter le bon paquet et vérifier la synchronisation. Pour transférer une calibration, utiliser le paquet prévu par l'application plutôt qu'une sélection manuelle de quelques matrices. Une matrice provenant d'un autre profil peut rendre le jeu incohérent.

La rectification dépend aussi de la taille des images et du paramètre alpha. Les exports de géométries rectifiées doivent conserver ces informations et les matrices nécessaires à leur reproduction.

## Diagnostiquer un échec

| Symptôme | Vérification prioritaire |
|---|---|
| Peu de coins détectés | Dictionnaire, dimensions, netteté, taille apparente de la mire. |
| Peu de paires stéréo | Mire visible simultanément et synchronisation correcte. |
| Écartement aberrant | Taille de case, unité, ordre gauche/droite et montage. |
| Bon score mais longueur fausse | Échelle imprimée, réfraction et mesure d'un objet connu. |
| Erreur surtout sur les bords | Couverture des coins du champ et modèle optique. |
| Résultat différent selon la distance | Disparité faible, pointage et conditions d'acquisition. |

## Références

L'implémentation utilise OpenCV : [calibration et reconstruction 3D](https://docs.opencv.org/4.x/d9/d0c/group__calib3d.html) et [calibration avec ChArUco](https://docs.opencv.org/4.x/da/d13/tutorial_aruco_calibration.html). Les calculs du projet sont dans [src/aquameasure.py](../../src/aquameasure.py), pilotés par le [service de calibration](../../src/interface/src/backend/calib_service.py).
