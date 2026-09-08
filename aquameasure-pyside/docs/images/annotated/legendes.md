# Légendes des captures annotées

Généré par `annotate_screenshots.py`. À citer tel quel dans le manuel.

## Écran d'accueil (`01-accueil`)

Repères : **1** La barre de menus : Fichier, Édition, Affichage, Outils, Aide ; **2** L'état de la machine de prise de vues, toujours visible ; **3** Ce que l'application vous conseille de faire maintenant ; **4** Les trois voyants : machine, synchronisation, calibration ; **5** Vous emmène directement à la première étape qui reste à faire.

## Page Machine (`02-machine`)

Repères : **1** L'état du port, de la carte et de la séquence ; **2** Le port de la carte, puis le débit ; **3** Se connecter, se reconnecter, se déconnecter, tester ; **4** La séquence enregistrement puis veille, jouée par la carte ; **5** Écrire la séquence dans la carte, puis la relire.

## Synchronisation, choix des vidéos (`03-sync-videos`)

Repères : **1** Les cinq sections du panneau, dans l'ordre de travail ; **2** Les deux vidéos de la paire ; **3** L'aperçu de la caméra gauche ; **4** Désigner soi-même l'image du flash sur cette caméra ; **5** L'image affichée et son minutage ; **6** Passer à l'étape suivante une fois le décalage figé.

## Synchronisation, détection du flash (`03b-sync-flash`)

Repères : **1** Cherche le flash dans les deux vidéos et en déduit le décalage ; **2** Limite la recherche à une zone que vous dessinez ; **3** Efface la zone dessinée.

## Synchronisation manuelle (`03c-sync-manuelle`)

Repères : **1** Fige le décalage à partir des deux images affichées.

## Découpe des vidéos (`03d-sync-trim`)

Repères : **1** Mémorise les bornes utiles : tout le reste sera ignoré.

## Calibration, vidéos de la mire (`04-calibration-videos`)

Repères : **1** Reprend les vidéos de l'étape Synchronisation ; **2** Les deux vidéos où la mire est filmée ; **3** Le verdict, une fois le calcul terminé ; **4** Disponible seulement si une calibration est enregistrée.

## Calibration, réglages et lancement (`04b-calibration-reglages`)

Repères : **1** La géométrie de la planche imprimée ; **2** La finesse du balayage des vidéos ; **3** Démarre le calcul ; **4** Interrompt un calcul en cours.

## Mesure, section Session (`05-mesure-session`)

Repères : **1** Lieu et date sont obligatoires : sans eux, rien n'est publiable ; **2** Enregistre la paire, fige le décalage, trace la calibration ; **3** Ce menu n'apparaît que sur la page Mesure ; **4** La consigne de l'étape de mesure en cours ; **5** Crée une ligne de registre à partir du cadre choisi. N'écrit aucun taxon ; **6** Le seul bouton qui écrit Famille, Genre et Espèce en base ; **7** Le comptage image par image, d'où sort le MaxN.

## Mesure, section Vidéos (`05b-mesure-videos`)

Repères : **1** La paire annotée, reprise de la Synchronisation ; **2** Quelle calibration sert réellement à mesurer.

## Mesure, section Détection IA (`05c-mesure-detection`)

Repères : **1** Le modèle de détection actif ; **2** Installer, comparer ou changer de modèle ; **3** Active ou coupe les propositions de l'IA ; **4** Relance la détection à chaque arrêt de la lecture ; **5** Bas : l'IA propose beaucoup, dont des erreurs ; **6** Lance l'IA sur l'image affichée.

## Mesure, section Comportement (`05d-mesure-comportement`)

Repères : **1** Le type d'événement à noter ; **2** Marque la première image du comportement ; **3** Marque la dernière image, suit le poisson et enregistre l'événement.

## Mesure, section Affichage et mesure (`05e-mesure-affichage`)

Repères : **1** Vérifie qu'un même point est à la même hauteur dans les deux images ; **2** Recommence la mesure au premier point.

## Données et IA, onglet Registre (`06-registre`)

Repères : **1** Le registre de la session, et ses compteurs ; **2** Les quatre actions sur la ligne sélectionnée ; **3** L'identification, rang par rang. NA est une décision, pas un vide ; **4** Une ligne par poisson annoté.

## Données et IA, onglet Explorateur (`06b-explorateur`)

Repères : **1** Les filtres, qui ne s'appliquent qu'au clic ; **2** Relit la base avec ces critères ; **3** Sessions, espèces et observations retenues.

## Données et IA, onglet Exports (`06c-exports`)

Repères : **1** Les résultats de la sortie en cours ; **2** Les trois exports d'analyse de la session ; **3** Le rang des classes et le grain du découpage ; **4** COCO est le format pivot. YOLO en dérive ; **5** Les exports de suivi et de comportement.

## Exports, galerie de références Fishial (`06c2-exports-galerie`)

Repères : **1** Les espèces et leur nombre d'images de référence ; **2** En dessous de ce nombre, l'espèce n'est jamais proposée par ressemblance.

## Données et IA, onglet Pistes (`06d-pistes`)

Repères : **1** Les quatre corrections possibles sur une piste ; **2** Au-delà de ce nombre d'images manquantes, la piste est signalée ; **3** Les pistes, les plus douteuses en tête.

## Hub Pro (`07-hub-pro`)

Repères : **1** Récupérer un jeu de données public ; **2** Réimporter un export CVAT dans la base ; **3** Régénérer COCO ou YOLO depuis la base ; **4** Le rang des classes et le grain du découpage.

## Page Sessions (`08-sessions`)

Repères : **1** Qui annote. Cette identité signe chaque validation ; **2** Prépare une sortie : lieu, date, notes ; **3** Où sont réellement les vidéos de cette session ; **4** Charge la paire et le registre, puis ouvre Mesure ; **5** Rattache les vidéos chargées et fige le décalage de synchronisation.

## Préférences (`09-parametres`)

Repères : **1** Choisir une autre racine pour vos données ; **2** Les quatre emplacements réels, et leur taille.

## Préférences, sauvegarde et comportements (`09b-parametres-sauvegarde`)

Repères : **1** La sauvegarde est manuelle : personne ne la fait à votre place ; **2** Créer un type de comportement.

## Dialogue « Qui annote ? » (`10-annotateur`)

Repères : **1** Le nom enregistré avec chaque identification ; **2** Facultatif, mais il lève toute ambiguïté lors d'une publication ; **3** Valide l'identité.

## Gestionnaire de modèles (`11-modeles-detection`)

Repères : **1** Trois onglets : catalogue, comparaison, ajout ; **2** Relit le catalogue.

## Réglages de la mire ChArUco (`12-mire-charuco`)

Repères : **1** La géométrie de la planche imprimée ; **2** La taille de case est la seule source de l'échelle en millimètres ; **3** Doit correspondre à la planche réellement imprimée.

## Réglages de la calibration (`13-reglages-calibration`)

Repères : **1** La finesse du balayage des vidéos ; **2** Combien d'images alimentent le calcul.
