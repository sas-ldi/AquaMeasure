# AquaMeasure Guide développeur

Ce guide est la référence du code sur GitHub : organisation du projet, calculs, données, tests et compilation. Pour les gestes dans l'application, lire le [manuel utilisateur](word/AquaMeasure_Manuel_Utilisateur.docx). Pour importer ou mettre à jour des poids, lire le [manuel des modèles IA](word/AquaMeasure_Guide_Extension_Modeles_Detection.docx).

## Lancer le projet

Le point d'entrée de l'application est aquameasure-pyside/main.py. Le lanceur Lancer-AquaMeasure.bat utilise l'environnement fish-vision/.venv. La GUI actuelle est en PySide6 et QML ; aquameasure.py contient encore le moteur historique de vision.

Depuis la racine du dépôt, sous Windows :

```powershell
& fish-vision/.venv/Scripts/python.exe aquameasure-pyside/main.py
```

Pour créer l’environnement, suivre les commandes du [README](../../README.md#lancer-depuis-les-sources). Python 3.10 et 3.11 conviennent à cette version. La distribution Windows a été vérifiée ; les autres systèmes nécessitent une validation de leurs lecteurs vidéo et de leurs moteurs IA.

Pour travailler sur une correction, utiliser une branche dédiée. Les poids ne sont pas versionnés : les télécharger avec le gestionnaire ou indiquer AQUAMEASURE_MODELS_DIR. Utiliser une base isolée pour les tests.

## Où se trouve chaque responsabilité

| Dossier ou fichier | Rôle |
|---|---|
| aquameasure-pyside/qml/Main.qml | Fenêtre, menus et navigation. |
| aquameasure-pyside/qml/pages | Pages de l'application. |
| aquameasure-pyside/qml/components | Lecteurs, fiche poisson, panneau des bouchées et dialogues. |
| aquameasure-pyside/qml/style/Theme.qml | Couleurs, polices et dimensions de l'interface. |
| aquameasure-pyside/src/controllers | Propriétés et actions exposées à QML. |
| aquameasure-pyside/src/backend | Services de calcul et de vidéo. |
| aquameasure-pyside/src/imaging | Images Qt et conversion des coordonnées. |
| fish-vision/src/annodb | Schéma, accès à la base et exports. |
| fish_annotate.py | Façade des observations, pistes et comportements. |
| fish_detectors | Catalogue et moteurs de détection. |
| fish_track.py | Suivi vidéo et intégration des pistes. |
| fishial_classify.py | Classification des images de poissons avec Fishial. |
| fishial_gallery.py | Images validées, références locales Fishial et ajout des nouveaux exemples. |
| scripts et packaging | Préparation de la distribution Windows et de l'installeur. |

Le dossier qml/AquaMeasure est généré au démarrage à partir de qml/pages, components et style. Modifier uniquement les sources, sinon la modification sera écrasée au prochain lancement.

## Des vidéos à la longueur

La synchronisation repère un flash commun aux deux vidéos, ou utilise les images désignées manuellement. Le décalage en images est conservé pour la prise stéréo concernée. Les bornes de découpe des vidéos et les bornes In/Out d'un suivi ont des rôles distincts.

La calibration détecte les coins d'une mire ChArUco dans des vues variées. Elle estime les paramètres intrinsèques et la distorsion de chaque caméra, puis leur rotation et translation relatives. La taille réelle des cases donne l'échelle. Les matrices, cartes de rectification et métadonnées sont conservées dans le profil de calibration.

La mesure utilise A et B dans chaque caméra. Les correspondances gauche/droite permettent de trianguler deux positions 3D ; leur distance donne la longueur en millimètres. Dans une stéréo idéale, `Z = f × B / d` relie profondeur, focale, écartement et disparité. Le code utilise les matrices calibrées. Une faible disparité rend les erreurs de pointage plus sensibles ; une faible erreur de reprojection ne remplace pas le contrôle d'un objet de longueur connue.

La synchronisation et la calibration s'appuient sur `aquameasure.py` et les services de `src/backend`. `MeasureController` pilote la mesure. Une boîte de détection situe le poisson ; elle ne définit pas ses extrémités anatomiques.

## Du clic à l'enregistrement

Main.qml expose les contrôleurs sous des noms comme Measure, Fish, Data et Pecks. Une propriété Python marquée Property devient une valeur lisible en QML ; un Slot devient une action ; un Signal actualise l'interface.

`FishWorkflowPanel.qml` regroupe la fiche poisson, la mesure, le suivi et ses événements à gauche des vidéos. `CompactRegistryPanel.qml` affiche la liste simple à droite ; le registre détaillé reste dans Données & IA. Le clic droit sur une boîte passe par `Data.selectBoxExplicit` et ouvre la fiche. `Data.saveFish` enregistre ses champs taxonomiques et sa longueur.

Les cartes utilisent `SidePanelSection`, `AppCard` et `SectionSurface`. `WorkspaceIcon` fournit leurs petites icônes vectorielles ; les couleurs viennent de `Theme.qml`. `SidePanelSplitShell` garde le panneau de paramètres redimensionnable sur les autres pages et le transforme en tiroir dans Mesure.

Data.saveFish crée l'observation si la fiche est nouvelle et met à jour l'observation sélectionnée sinon. C'est l'unique action « Enregistrer le poisson ». Ne pas recréer un chemin de validation concurrent : une correction de taxonomie doit conserver l'identifiant de l'observation.

Après un enregistrement réussi, les points A/B de travail disparaissent des deux vues. La fiche reste sélectionnée et sa longueur enregistrée est conservée. En cas d'échec, les points restent disponibles pour réessayer.

Mesurer et enregistrer utilisent la calibration de l'image affichée. Ne pas la recharger au milieu de ces actions : le rafraîchissement relancerait la détection et pourrait invalider le cadre du brouillon.

Les noms automatiques restent des propositions. La validation humaine est enregistrée séparément de la provenance et de la confiance du modèle. La propagation du nom validé sur une piste passe par fish_annotate.list_video_annotation_overlays et le cache de FishController.

## Suivi et marqueurs

`FishController.beginTrackFollow` et `finishTrackFollow` produisent une trajectoire et la rattachent au poisson sélectionné, sans comportement implicite. `beginBehaviorFollow` capture au début un type de durée : après le rattachement réussi, `Data.completeAssistedBehavior` écrit cet intervalle. Un conflit de rattachement empêche cette écriture.

Si le poisson porte déjà une piste, le mode durée utilise directement ses bornes : aucun recalcul, aucun changement de piste. In et Out doivent rester dans la plage enregistrée. L'annulation efface le choix en attente ; elle ne supprime ni la piste ni ses points.

L'amorce exige un cadre localisé sur l'image de départ et une observation enregistrée ; la longueur est facultative. Pour le parcours le plus simple, poser In sur l'image de la fiche avant de naviguer vers Out. Si aucun cadre courant n'est utilisable, `_resolved_seed` tente `Data.focusSelectedObservation` pour retrouver cette image.

Le suivi utilise Ultralytics et ByteTrack. Si le détecteur choisi ne propose pas cette intégration, le logiciel cherche un modèle Ultralytics installé pour le suivi. Une piste peut perdre le poisson lors d'un croisement ou d'une occultation : son identifiant ne garantit pas la justesse de toutes ses positions.

`FishFollowPanel.qml` distingue **Comportement → À cet instant**, **Comportement → Sur une durée** et **Trajectoire**. `TrackPeckPanel.qml` porte l'unique bouton de pointage : sans piste, il appelle `Data.toggleSelectedPointEvent` sur l'image de l'observation ; avec une piste, il appelle `PeckController` sur l'image courante.

`PeckController` gère sélection de piste, ajout, suppression, navigation et relecture des points. `TrackEventList.qml` permet de revoir et retirer les événements, y compris les durées. `EventMarkerStrip.qml` affiche les repères temporels ; `MeasureStereoView.qml` dessine les cadres et le symbole animé.

Les types sont créés dans SettingsPage.qml, section Comportements, via Data.addBehaviorType. L'interface demande le nom, le symbole et la portée `instant` (Ponctuel) ou `interval` (Durée). Le type choisi à In est figé jusqu'à la fin ou l'annulation. Un type ponctuel ne peut pas devenir un intervalle. Les lettres de raccourci se règlent sur ces types. Ajouter un nouveau symbole proposé se fait dans behaviorLogos de SettingsPage.qml ; ajouter un nouveau comportement utilisateur ne demande aucun code.

`fish-vision/src/annodb/event_types.py` fournit les types par défaut. Bouchée utilise la portée `instant`. Le peuplement conserve les anciens intervalles et réutilise une Bouchée ponctuelle personnalisée lorsqu'elle existe.

## Coordonnées et numéros d'image

Une image absolue est un index dans la vidéo source. Un index de timeline tient compte de la synchronisation et des bornes utiles. Les observations déclarent leur frame_ref ; les échantillons de piste utilisent les images absolues.

Les coordonnées raw appartiennent à la vidéo d'origine. Les coordonnées stereo_rectified_left appartiennent à l'image gauche rectifiée. Ne pas les mélanger. RectMapping, dans src/imaging/rect_mapping.py, transforme les positions rectifiées vers la vidéo brute avec les cartes de calibration.

À l'arrêt, la page affiche une image rectifiée. En lecture native, elle peut afficher la vidéo brute. MeasureStereoView applique aux repères la conversion adaptée, puis le même zoom et le même déplacement que l'image. Tester aussi bien le cadre que la trajectoire, les points et les marqueurs de bouchées.

## Opérations longues

La détection, la classification, le suivi, la calibration et les exports ne doivent pas bloquer le thread graphique. Utiliser les workers et signaux existants. Ne pas modifier directement un objet QML depuis un worker.

Un résultat de calcul doit encore correspondre à la vidéo, à l'image et à la sélection actives lorsqu'il est reçu. Conserver les mécanismes de génération et d'invalidation des caches. Arrêter les workers proprement à la fermeture ; le préchauffage Fishial partage un verrou de chargement pour éviter deux copies du modèle en mémoire.

## Ajouter un modèle ou un moteur

Pour des poids compatibles, passer par le gestionnaire ou le catalogue JSON. Voir le [manuel des modèles IA](DETECTEURS.md). Le registre combine le catalogue livré, le catalogue distant et les entrées locales, dans cet ordre.

Un moteur nouveau implémente DetectorBackend, avec load, infer et unload. infer retourne x1, y1, x2, y2, conf, cls_id et cls_name. Le nettoyage commun borne les boîtes et filtre les classes. Les plugins se placent dans plugins/detectors ; les dépendances Python doivent aussi être présentes.

Dans un exécutable, ajouter un fichier .pt compatible ne nécessite pas de recompilation. Ajouter une bibliothèque Python ou une architecture absente demande de reconstruire la distribution. Le bouton d'installation pip refuse donc d'installer des paquets dans le binaire.

## Modifier les données et les exports

La base SQLite `fish_annotations.db` utilise `FISH_VISION_DB` si défini, puis la racine de stockage configurée, avec un repli historique dans `fish-vision/data`. Les vidéos restent des fichiers externes. Une sauvegarde de travail conserve la base, les vidéos, la calibration et les réglages ; utiliser une sauvegarde SQLite cohérente si l'application écrit encore.

Une session regroupe plusieurs prises dans `session_media_pairs`, chacune avec deux médias, son décalage et sa calibration. `spatial_annotations` contient les observations ; `tracks` et `track_samples` portent les pistes et leurs positions. Les bouchées sur piste sont dans `temporal_events`. Les étiquettes de fiche sont dans `spatial_behavior_flags` : elles ne constituent pas une série de bouchées. Détacher une fiche de sa piste conserve les positions et événements.

La calibration liée à une prise est une référence de provenance en base. La réouverture par `SessionController` ne réimporte pas son ZIP : `MeasureService.load_calibration` utilise les fichiers actifs de `camera_parameters`. Le parcours utilisateur demande donc de réimporter la bonne calibration si elle a changé et de vérifier la synchronisation avant de reprendre une ancienne paire.

Le modèle ORM est dans fish-vision/src/annodb/models.py. Lire aussi connection.py et le schéma initial avant d'ajouter un champ. Une migration doit conserver les anciennes bases et pouvoir être réexécutée. Tester sur une copie isolée, avec des lignes anciennes et des valeurs manquantes.

Les exports de jeux passent par export_core.py. Les formats de session et les extractions CSV ont aussi leurs propres modules. Conserver le repère des images, l'espace des coordonnées, le statut de revue et les liens de provenance. Ajouter un champ dans une table ne le rend pas automatiquement disponible dans tous les exports.

Les formats comprennent COCO et YOLO pour la détection, COCO-VID et MOT pour le suivi, AVA et Events JSONL pour les événements, ainsi que les CSV de session. Respecter les états de revue, les zones ignorées et la conversion des géométries rectifiées. Pour les jeux d'entraînement, séparer les partitions par session ou vidéo afin de ne pas évaluer sur des images voisines du même poisson. Les manifestes décrivent les filtres, classes, partitions et empreintes.

`scripts/export_session.py` sélectionne tous les médias de `session_media_ids`, pas seulement la paire ouverte. COCO et COCO-VID matérialisent les images dans le dossier exporté ; CSV conserve les mesures. Ces fichiers ne remplacent pas une sauvegarde de la base, des vidéos et des calibrations. Un export autorisé avec `--allow-missing` reste partiel et déclare les médias omis dans `session.json`.

Le format CSV appelle `export_session_tables.py` et produit le paquet `aquameasure.session_csv.v2` : observations (`session.csv`), pistes, positions, événements, comptages, vidéos et résumé de session. `dictionnaire.csv` est généré depuis les mêmes listes de colonnes. Aucun calcul statistique de résultats n'est ajouté. Les mesures gardent leur précision et les valeurs manquantes restent vides. Le CSV utilise UTF-8 avec BOM, une virgule de séparation et un point décimal ; les textes susceptibles d'être interprétés comme des formules Excel reçoivent une apostrophe initiale.

Les actions viennent de `temporal_events` et de `spatial_behavior_flags`, chacune écrite une fois avec sa table d'origine. Les points sans piste et les événements de pistes sans position sont conservés. Une seconde fiche liée à la même piste ne répète pas ses événements. `fish_key` regroupe uniquement cette identité de suivi ; il ne certifie pas une identité biologique entre pistes. Les positions sont écrites en flux, sans décimation ni interpolation nouvelle, avec leur origine et leurs coordonnées 3D disponibles.

Les frames historiques utilisent l'offset figé de la prise. Sans cet offset, les colonnes absolues restent vides et `frame_status=offset_unknown`, tandis que les index et le référentiel source sont conservés. Les temps ne sont pas inventés sans FPS. Le MaxN est le maximum des seuls `frame_abundance.validated` avec priorité au comptage manuel, y compris zéro. Le CSV le laisse vide sans validation. `sessions.session_counts` utilise la même définition et compte aussi les actions de fiche dans son total d'événements.

COCO-VID inclut les actions des pistes exportées dans l'extension **`aquameasure.track_events.v1`**. `export_behavior.collect_events` lit `temporal_events` et convertit les anciens index de timeline avec l'offset figé de la prise. `export_tracking.py` écrit les mêmes événements dans `coco_vid.json` (`events`) et `track_events.jsonl` :

- `track_id` est l'entier COCO-VID ; `track_db_id` conserve l'identifiant de la base. `tracks[].event_ids` liste les actions de la piste ; `annotations[].attributes.event_ids` liste celles actives sur cette boîte à cette frame.
- `frame_index` donne l'instant d'un point. `frame_start` et `frame_end` délimitent la durée, bornes incluses. Tous ces index sont absolus dans la vidéo et commencent à zéro ; les temps en secondes utilisent sa fréquence d'image.
- `event_types` conserve noms, portées, symboles et couleurs, y compris les types personnalisés ou désactivés encore utilisés. Chaque événement conserve sa source (`manual`, `heuristic`…), son auteur et sa confiance : un résultat automatique ne devient pas une annotation humaine.
- Les chemins `start_image_file_name` et `end_image_file_name` sont relatifs à la racine du dataset. Si une borne ne figure pas parmi les images de tracking exportées, son JPEG exact va dans `event_images/`, même avec décimation. Aucune boîte n'est créée pour cette image et elle n'entre pas dans les négatifs MOT.
- Les pistes non exportables et les offsets indéterminables figurent dans `excluded_events`. Le rapport et le manifeste comptent les actions et signalent leurs images manquantes ; une frame hors vidéo garde son index avec un chemin d'image nul. `session.json` reprend le nombre d'actions et les images manquantes.

Ces champs sont une extension AquaMeasure : un lecteur COCO-VID ou MOT standard peut les ignorer. Les intégrations qui exploitent les comportements doivent lire `events` ou `track_events.jsonl`.

La bibliothèque Fishial réunit les observations validées de toutes les sessions locales. `list_species_summary` compte les images en attente à partir des identifiants d'observations déjà associés à une référence valide. `promote_all_new_references` applique le même seuil et la même déduplication que l'ajout par espèce, sans remplacer les poids d'origine. `DataController` lance le calcul en arrière-plan et actualise les compteurs, y compris lorsqu'une espèce échoue après d'autres ajouts réussis.

Le transfert entre postes passe par `annodb/fishial_transfer.py` et `scripts/transfer_fishial_library.py`. Le ZIP `aquameasure.fishial-local.v1` contient taxonomie, vecteurs float32 et clichés disponibles. L’import vérifie les empreintes du modèle et de son code d’inférence, la dimension des vecteurs et les fichiers avant la transaction. Il fusionne les espèces par rang et nom scientifique, déduplique les références par identifiant et conserve une référence locale conflictuelle. Les clichés importés utilisent le projet `fishial_local_imports` ; aucune `SpatialAnnotation` ni session fictive n’est créée. Les références sans observation liée sont visibles dans la bibliothèque, participent aux centroïdes et restent exportables. `fishialLibraryImported` recharge la taxonomie et invalide le cache d’identification dans le processus de l’interface.

## Vérifier une modification

Les tests d'interface et de contrôleurs sont dans aquameasure-pyside/tests ; ceux de la bibliothèque de données dans fish-vision/tests. Les tests unittest se lancent avec le Python du projet. Choisir les suites concernées, puis ouvrir réellement l'application pour les gestes visuels.

```powershell
& fish-vision/.venv/Scripts/python.exe -m unittest discover -s aquameasure-pyside/tests -p "test_measure_qml_runtime.py"
& fish-vision/.venv/Scripts/python.exe aquameasure-pyside/tools/verify_playback_registration.py
```

Pour une modification du suivi, contrôler une sélection à la pause, un zoom, le passage en lecture, un événement et sa relecture depuis la base. Pour une modification du packaging, les tests Python du dépôt ne suffisent pas : lancer aussi les autotests de l'exécutable et installer dans un dossier isolé.

## Produire la version Windows

Les scripts ci-dessous construisent la distribution complète, qui suppose que tous les poids du manifeste soient disponibles localement. Les poids internes `fish_detect_public` et `fish_detect_family` ne sont pas téléchargeables depuis ce dépôt. Pour une autre sélection de modèles, adapter le manifeste, `scripts/prepare_release_models.py` et les contrôles de livraison correspondants. Consulter les [conditions des composants](../../THIRD_PARTY_NOTICES.md) avant de redistribuer une application compilée.

1. Préparer un environnement Python avec les versions testées dans packaging/requirements-windows-full.txt.
2. Placer les poids Fishial extraits dans fish-vision/models, puis lancer scripts/prepare_release_models.py. Le script récupère les autres poids publics et vérifie les empreintes du catalogue.
3. Vérifier que les deux manuels Word sont à jour dans `aquameasure-pyside/docs/word`. Pour fournir aussi les PDF, les rendre avec `scripts/render_documentation.ps1 -OutputDir build/documentation-release`.
4. Lancer `scripts/build_windows.ps1 -SkipArchive` (ajouter `-DocumentationPdfDir build/documentation-release` si les PDF ont été rendus). Le build utilise PyTorch CPU, actualise le QML et le manifeste des modèles, copie les manuels et contrôle l'absence de données de travail. `scripts/package_release.py` permet ensuite de créer un ZIP portable si nécessaire.
5. Lancer scripts/build_installer.ps1 avec ce dossier portable.
6. Exécuter `AquaMeasure.exe --self-test-models rapport-modeles.json` pour les inférences hors ligne, puis `scripts/smoke_test_windows_installer.ps1 -ReportPath rapport-installation.json`. Ce dernier teste une installation isolée, le catalogue de 2 438 espèces, la base vide, l'icône, le suivi CPU, la fenêtre et la désinstallation.

Ne jamais embarquer les vidéos de travail, la base réelle, les calibrations personnelles ou un jeton Hugging Face. La distribution complète fournit les moteurs et poids publics prévus ; SAM 3 utilise l'accès personnel de l'utilisateur.

`prepare_release_models.py` utilise une liste explicite de fichiers. Les noms du référentiel sont livrés ; les photos et vecteurs Fishial acquis pendant les sessions restent des données personnelles. Les transmettre volontairement passe par l'export de bibliothèque Fishial, pas par le build de l'application.

Le logo IRD est fourni en PNG et en ICO dans `aquameasure-pyside/resources`. L'ICO est partagé par PyInstaller, les fenêtres Qt et Inno Setup. La version Windows est définie dans `packaging/windows-version.txt` et le script Inno. Sans certificat de signature de code, le résultat est non signé ; une empreinte SHA-256 vérifie le fichier reçu mais ne donne pas une identité d'éditeur vérifiée.

Le dépôt GitHub est destiné aux sources et aux manuels. Ne pas y pousser `release/`, `dist/`, `build/`, `LIVRAISON_CLIENT/`, les exécutables ni les poids téléchargés. Les scripts de compilation et les liens du catalogue permettent de reconstruire l'application. Aucune publication de binaires GitHub Releases n'est nécessaire.

`python scripts/export_sources.py release/AquaMeasure-Sources.zip` crée une archive des sources du dernier commit. `python scripts/check_publication.py` vérifie les fichiers suivis et les liens de documentation avant publication. GitHub Actions exécute ce contrôle et les tests de données sans produire de binaire.

## Mettre à jour la documentation

La documentation courante comprend deux Word et ce Markdown. `MANUEL_UTILISATEUR.md` et `DETECTEURS.md` sont les sources des deux manuels ; `build_word.py` génère uniquement ces deux fichiers dans `docs/word`. Ce guide du code reste en Markdown. `capture_screenshots.py` ouvre la vraie interface avec une base de démonstration ; passer explicitement la paire de vidéos. Sous Windows, les lecteurs vidéo natifs nécessitent une capture de fenêtre à l'écran : le backend logiciel du mode hors écran ne rend pas leurs images. `--onscreen` affiche l'application pour cette prise de vue.

Pour reprendre une session existante sans la modifier, utiliser `--source-db`, `--session-id` et `--data-root` : le script copie SQLite par son API de sauvegarde, puis travaille uniquement sur cette copie. `--frame` choisit l'observation de référence, `--videos` la paire de poissons et `--calibration-videos` la paire de mire. Les recadrages de suivi passent par le même contrôleur que le geste utilisateur. Les captures de détail proviennent des cartes QML réellement affichées. Vérifier le code de sortie et les images ; ne pas publier des lecteurs noirs ni un état de calcul en échec.

Relire les libellés dans l'interface, régénérer les captures utiles et contrôler les pages Word après rendu. Publier uniquement les deux manuels courants, leurs PDF et leurs sources Markdown.
