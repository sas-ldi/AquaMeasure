# AquaMeasure Mesure stéréo

Cette note décrit le passage des quatre points image à une longueur, puis les erreurs qui peuvent affecter cette mesure. Elle sert à vérifier le protocole de pointage et à interpréter les contrôles.

## De quatre points à une longueur

Une mesure utilise deux points anatomiques, A et B, reconnus dans les deux caméras. L'opérateur fournit donc A gauche, B gauche, A droite et B droite. La calibration permet de trianguler A et B dans l'espace. Leur distance donne la longueur.

La convention anatomique doit être fixée pour la campagne : par exemple longueur totale ou longueur à la fourche. Un changement de convention ne peut pas être corrigé par la calibration.

## Images synchronisées et rectifiées

Les deux vues doivent correspondre au même instant. Le décalage de synchronisation est pris en compte lorsque la timeline est convertie en images sources.

La rectification corrige la distorsion et facilite le rapprochement des points homologues. Dans la géométrie rectifiée, ces points se trouvent approximativement sur la même ligne horizontale. La correspondance automatique aide à viser dans la vue droite ; l'opérateur doit vérifier qu'elle retient le même point anatomique.

Une mesure s'effectue à l'arrêt. Le zoom ne change pas l'échelle de calibration : les coordonnées affichées sont reconverties en coordonnées de l'image de référence avant le calcul.

## Calcul de profondeur

Pour une paire stéréo idéale et rectifiée :

```text
d = u_gauche - u_droite
Z = f × B / d
```

f est la focale en pixels, B l'écartement des caméras et d la disparité en pixels. Cette relation explique pourquoi un poisson lointain est plus difficile à mesurer : sa disparité est petite.

L'application utilise les matrices calibrées pour trianguler chaque point. Avec A = (XA, YA, ZA) et B = (XB, YB, ZB), la longueur est :

```text
L = √[(XB − XA)² + (YB − YA)² + (ZB − ZA)²]
```

L'unité dépend de celle de la mire. Le parcours normal utilise les millimètres.

## Ce que l'assistance automatique apporte

Le détecteur fournit une région où chercher le poisson. L'assistance de mesure peut proposer les extrémités et leur correspondance stéréo. Une boîte de détection n'est pas une définition anatomique de la longueur : queue, nageoires ou inclinaison peuvent déplacer ses limites.

Après une proposition, contrôler les deux extrémités dans les deux vues. Choisir une image plus nette si la tête ou la queue est masquée. Enregistrer le poisson sans longueur est préférable à conserver une valeur sans points vérifiables.

## Incertitude et contrôles

Dans l'approximation précédente, une petite incertitude de disparité σd donne :

```text
σZ ≈ Z² × σd / (f × B)
```

Cette relation décrit la sensibilité de profondeur ; elle n'est pas l'incertitude complète de longueur de chaque poisson. Les erreurs de calibration, de synchronisation, de réfraction et de choix anatomique s'ajoutent au pointage.

| Cause | Effet pratique |
|---|---|
| Mauvaise taille de case | Erreur d'échelle sur toutes les longueurs. |
| Support déplacé | Calibration devenue incompatible. |
| Mauvais instant entre caméras | Deux positions différentes du poisson. |
| Point mal placé | Erreur sur une extrémité triangulée. |
| Poisson trop éloigné | Disparité faible et forte sensibilité aux pixels. |
| Flou ou occultation | Correspondances difficiles à vérifier. |
| Conditions optiques différentes | Erreur systématique possible, notamment sous l'eau. |

Avant la campagne, mesurer un objet connu dans la plage de distances utilisée. Pendant la relecture, conserver les points et la calibration pour pouvoir vérifier une longueur suspecte.

## Image de mesure et début du suivi

La longueur appartient à l’image sur laquelle le poisson a été mesuré. Le suivi n’exige pas une longueur. Pour le parcours courant, mesurer et enregistrer le poisson, puis poser Début In sur cette même image avant d’avancer jusqu’à Fin Out. L’amorce doit disposer d’un cadre localisé sur l’image de départ. Naviguer sur une autre image ne suffit pas à savoir où se trouve ce poisson.

Pour pointer plusieurs bouchées le long du déplacement, créer une Trajectoire, puis utiliser Points sur la piste. Un Comportement sur une durée décrit l’intervalle d’action ; il ne représente pas une bouchée continue.

## Repères en lecture et pendant le suivi

La vidéo lue n'est pas nécessairement rectifiée, alors que les observations peuvent l'être. RectMapping convertit les boîtes, les centres et les points depuis la rectification vers la vidéo brute. MeasureStereoView applique ensuite le même zoom et le même déplacement à l'image et aux overlays.

Vérifier également les repères en lecture avec zoom et déplacement de la vue.

Le nom de la piste est celui validé sur le poisson. La mesure enregistrée sur une observation ne signifie pas qu'une nouvelle mesure 3D est recalculée pour toutes les images de sa piste.

## Données à conserver

Une observation mesurée doit garder les coordonnées et leur espace image, le numéro d'image et son repère, la longueur, la taxonomie retenue et la provenance de calibration. Les exports doivent éviter toute association entre une boîte rectifiée et un crop brut sans conversion explicite.

Les calculs reposent sur OpenCV ; voir la [référence de triangulation et rectification](https://docs.opencv.org/4.x/d9/d0c/group__calib3d.html).
