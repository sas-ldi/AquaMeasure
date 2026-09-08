# AquaMeasure Base de données

Cette note décrit le schéma SQLite et les liens entre sessions, observations, pistes et événements. Elle permet de retrouver une donnée ou de faire évoluer le code sans perdre sa provenance.

## Où se trouvent les données

La base est une SQLite nommée fish_annotations.db. La résolution utilise FISH_VISION_DB lorsqu'il est défini, puis la racine choisie dans la configuration de stockage, avec un repli historique dans src/annotations/data. Édition → Préférences affiche le dossier utilisé.

Les vidéos ne sont pas stockées dans la SQLite. Une sauvegarde de travail doit donc conserver aussi les médias et la calibration. Ne pas copier seulement le fichier principal d'une base en cours d'écriture : utiliser l'outil de sauvegarde ou une sauvegarde SQLite cohérente.

## Entités principales

| Entité | Contenu et liens |
|---|---|
| projects | Regroupement des médias et des annotations. |
| media_assets | Chemin, dimensions, cadence et empreinte d'un média. |
| sessions | Sortie de terrain, lieu, date, annotateur et état de travail. |
| session_media_pairs | Prises d'une session, chacune avec sa paire gauche/droite, son décalage et sa calibration. |
| annotators | Identité des personnes qui annotent. |
| calibrations | Identité et provenance du profil stéréo. |
| spatial_annotations | Observation sur une image, géométrie, taxon, longueur et piste éventuelle. |
| tracks | Identifiant de suivi sur une vidéo, sans garantie d’identité biologique entre pistes. |
| track_samples | Position d'une piste à une image absolue. |
| temporal_events | Événements temporels, type, piste et provenance. |
| event_types | Types de comportement, portée, symbole et état actif. |
| spatial_behavior_flags | Étiquettes ponctuelles posées sur les observations. |
| frame_abundance | Comptage IA, correction manuelle et validation. |
| taxon_nodes | Référentiel taxonomique. |
| taxon_reference_embeddings | Références de classification locales. |
| export_runs | Journal et provenance des exports. |

Une session regroupe plusieurs prises stéréo. Le code empêche de rattacher un même média à plusieurs sessions. Les champs de paire historiques sur sessions sont conservés pour compatibilité ; lire aussi session_media_pairs. Les anciennes colonnes d'auteur peuvent contenir le nom affiché en texte : ne pas les traiter comme des clés étrangères vers annotators.

## Observation et identification

La création d'un poisson et la correction de sa taxonomie passent par Data.saveFish. Le premier appel crée la fiche ; les appels suivants sur la même sélection la mettent à jour. La longueur peut rester absente.

Les états de revue distinguent une proposition non relue, un taxon identifié et un poisson déclaré non identifiable. Le modèle d'origine et sa confiance restent conservés après correction. Les exports ne doivent pas déduire la validation de la simple présence d'un nom.

Le nom humain retenu sur une observation liée à une piste est prioritaire pour l'affichage de cette piste. Le lien ne doit pas fabriquer un nouveau poisson à chaque image.

## Images et coordonnées

frame_ref indique comment lire le numéro d'image : absolute pour l'index dans le fichier source, timeline_legacy pour certains anciens enregistrements. La conversion utilise le décalage conservé pour la prise concernée, avec le repli historique de session ; les échantillons de piste utilisent directement des images absolues.

Une boîte conserve son espace image et ses dimensions de référence. raw désigne la vidéo d'origine ; stereo_rectified_left désigne l'image gauche rectifiée. Le même couple x/y n'a pas le même sens dans ces espaces.

Une géométrie rectifiée nécessite sa calibration pour reproduire un crop dans la vidéo brute. Les exports qui ne peuvent pas convertir une ancienne référence doivent signaler ou exclure la ligne selon leur contrat, et ne pas inventer un décalage.

## Pistes et événements

tracks porte l'identifiant du poisson suivi ; track_samples porte ses positions successives. Un événement ponctuel sur piste date une bouchée ou un autre geste. Un flag sur une observation est une annotation de fiche différente : il n'est pas une série de bouchées.

Le mode Comportement enregistre une action ponctuelle ou une durée. Le mode Trajectoire suit le déplacement entre In et Out ; des événements ponctuels peuvent ensuite être ajoutés sur ses images. Les intervalles existants restent lisibles et exportables. Détacher une observation de sa piste conserve les échantillons et les événements ; ce n'est pas une suppression de la piste.

## Références Fishial

La bibliothèque locale rassemble les images validées de toutes les sessions du poste. Ajouter les nouvelles images calcule leurs vecteurs et complète les références existantes, avec contrôle du seuil et des doublons. Les poids du modèle d’origine ne sont pas réentraînés par cette action.

Le transfert de bibliothèque produit un ZIP au format aquameasure.fishial-local.v1. Il contient les taxons, vecteurs et clichés disponibles. L’import contrôle la compatibilité du modèle et fusionne les références ; il ne crée pas de sessions ni de poissons fictifs. Ce transfert est distinct d’un export de clichés destiné à l’entraînement.

## Migrations et sauvegardes

Le schéma ORM est dans src/annotations/src/annodb/models.py. connection.py initialise et met à niveau les bases existantes ; db/schema.sql décrit le schéma initial. Vérifier ces chemins ensemble lors d'une évolution.

Une migration doit accepter les bases anciennes et une seconde exécution. Prévoir des valeurs par défaut compatibles et tester sur une copie. Ne jamais utiliser la base de travail pour une recette qui peut se faire avec des données fictives.

Pour transférer une séance, garder la base, les médias référencés, la calibration et les réglages nécessaires. Les manifests d'export décrivent des sorties dérivées ; ils ne remplacent pas cette sauvegarde.
