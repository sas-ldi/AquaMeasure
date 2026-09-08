# fish-vision — Détection, comptage et annotations poissons (100 % local)

Module ML et base d'annotations unifiée pour poissons tropicaux (Madagascar).
Complète [AquaMeasure](../README.md) (mesure stéréo) sans dépendance cloud.

## Architecture

- **Source de vérité** : `data/fish_annotations.db` (SQLite)
- **Exports** : vues YOLO / COCO régénérables à tout niveau taxonomique
- **Inférence** : YOLO11 + ByteTrack + comptage ligne/zone
- **Broute** : heuristiques trajectoire → `temporal_events`

## Installation rapide (Windows)

```bat
cd fish-vision
scripts\setup_env.bat
```

Ou manuellement :

```bat
py -3.10 -m venv .venv
.venv\Scripts\pip install -r requirements.txt
.venv\Scripts\python scripts\db_init.py
```

## Workflow annotation

### 1. Annoter dans CVAT (Docker, local)

- Installer CVAT + SAM via Nuclio (voir [doc CVAT serverless](https://docs.cvat.ai/docs/guides/serverless-tutorial/))
- Exporter en **YOLO 1.1** ou **CVAT for images 1.1**

### 2. Importer dans la base unifiée

```bat
.venv\Scripts\python scripts\db_ingest_cvat.py ^
  --export path\to\export.zip ^
  --project madagascar_2026 ^
  --mapping configs\cvat_label_map.yaml
```

### 3. Exporter pour entraînement

```bat
.venv\Scripts\python scripts\db_export.py --format coco --rank family --split-by media --version 1.0.0
.venv\Scripts\python scripts\db_export.py --format yolo --rank genus  --split-by session --version 1.0.0
```

Rangs : `fish` | `family` | `genus` | `species`

`--split-by` (`media` | `session` | `site`) est **obligatoire** : un split par
frame gonfle les métriques de 20 à 40 points. Le dossier produit s'appelle
`<nom>_v<version>_<date>_<hash8>` sous `../data/exports` et contient son
`manifest.json` (empreintes, split, effectifs par classe, exclusions) et son
`splits.json` (rejouable avec `--replay-splits` pour figer le découpage).
Autres réglages : `--seed`, `--ratios 70,15,15`, `--frame-stride N`,
`--min-instances`, `--min-media`, `--created-at`.

## Entraînement YOLO

### Sur quelle « base » on entraîne ?

| Étape | Source des images | Lien avec la DB |
|-------|-------------------|-----------------|
| **Bootstrap** | `data/public/fish/` (Roboflow ou synthétique) | **Pas** la DB — valide la plomberie uniquement |
| **Fine-tuning** | `../data/exports/<nom>_v<version>_<date>_<hash8>/yolo/` | **Export** de `fish_annotations.db` via `db_export.py` (`--split-by` obligatoire) |

YOLO ne lit **pas** SQLite directement : il lit un dossier `images/` + `labels/` + `data.yaml` généré par export.

### Durées indicatives (RTX 5070 Ti, 640 px)

| Scénario | Images | Modèle | Epochs | Durée estimée |
|----------|--------|--------|--------|---------------|
| Bootstrap synthétique (secours) | ~10 | yolo11n | 50 | **2–5 min** |
| Bootstrap Roboflow Fish | ~680 | yolo11n, batch 32 | 50 | **20–40 min** |
| Fine-tuning vos poissons | 200–500 | yolo11s, batch 16 | 100 | **30–60 min** |
| Fine-tuning vos poissons | 1000+ | yolo11s, batch 16 | 100 | **1–2 h** |
| Précision max | 1000+ | yolo11m, batch 8 | 150 | **2–4 h** |

Les durées varient selon le nombre de poissons par image et la résolution (`imgsz`).

### Bootstrap (dataset public) — à télécharger d'abord

Le projet **ne contient pas** les images publiques. Sans téléchargement, seules 10 images synthétiques de test existent (inutiles pour un vrai modèle).

```bat
REM Voir les options
.venv\Scripts\python scripts\download_public_dataset.py --list

REM Recommandé bootstrap (4.9k images, 1 classe) — clé API Roboflow gratuite
set ROBOFLOW_API_KEY=ta_cle
.venv\Scripts\python scripts\download_public_dataset.py --dataset deepfish

REM Alternative sans clé (3.8 Go, Méditerranée, YOLO prêt)
.venv\Scripts\python scripts\download_public_dataset.py --dataset obsea

REM Alternative récif tropical sans clé (FishInv, 17 espèces)
.venv\Scripts\python scripts\download_public_dataset.py --dataset fishinv --single-class

REM Puis entraînement
.venv\Scripts\python scripts\train_detect.py --profile bootstrap
```

| Dataset | Images | Classes | Proche tropique ? | Bootstrap détection ? |
|---------|--------|---------|-------------------|------------------------|
| **deepfish** (Roboflow) | ~4.9k | 1 | Moyen (bacs AU) | **Oui — recommandé** |
| **fish416** (Roboflow) | ~680 | 1 | Faible | Oui (rapide) |
| **obsea** (Zenodo) | milliers | 23 | Méditerranée récif | Oui avec `--single-class` |
| **fishinv** (direct) | variable | 17 | **Récif tropical** | Oui avec `--single-class` |
| **kakadu** (Zenodo) | ~44k | 23 | **Eau douce tropicale AU** | Oui avec `--single-class` |
| **fish4knowledge** | variable | multi | Tropiques | **Non** — plutôt phase espèce |
| DeepFish Zenodo brut | ~40k | espèces | Australie | Non — format COCO/masques, conversion lourde |

**Fish4Knowledge** : utile pour tester le pipeline **multi-espèces** (phase 2), pas le meilleur choix pour le premier bootstrap « poisson / pas poisson ».

**DeepFish** (via Roboflow) : meilleur compromis volume + simplicité (1 classe, YOLO prêt).

Clé Roboflow : compte gratuit sur [app.roboflow.com](https://app.roboflow.com) → Settings → API Key.

### Fine-tuning (vos données Madagascar)

```bat
.venv\Scripts\python scripts\retrain_from_db.py --rank fish --split-by media
```

`retrain_from_db.py` exporte **puis** entraîne sur ce même export (et met
`configs\data_custom.yaml` à jour). Pour n'entraîner que sur le dernier export
déjà produit : `--skip-export`.

Profil `finetune` : `yolo11s`, 100 epochs, batch 16 — calibré RTX 5070 Ti.

## Inférence + comptage

```bat
.venv\Scripts\python scripts\infer_track_count.py ^
  --model models\fish_detect_public.pt ^
  --source video.mp4 ^
  --line 0.5 0.0 0.5 1.0 ^
  --save-db ^
  --json data\last_inference.json
```

- `--line x1 y1 x2 y2` : ligne de comptage normalisée (0–1)
- `--save-db` : persiste les tracks dans la base
- `--show` : affichage OpenCV

## Détection broute (heuristiques)

```bat
.venv\Scripts\python scripts\detect_grazing.py ^
  --json data\last_inference.json ^
  --substrate-y 0.65 ^
  --save-db --video video.mp4
```

## Export ONNX (phase 2 Qt)

```bat
.venv\Scripts\python scripts\export_onnx.py --model models\fish_detect_custom.pt
```

Le `.onnx` produit s'ajoute au catalogue de détecteurs avec le backend `onnx`
(voir `aquameasure-pyside/docs/DETECTEURS.md`).

## Scripts utiles

| Script | Rôle |
|--------|------|
| `db_init.py` | Crée la DB + taxonomie seed (Acanthuridae) |
| `db_seed_samples.py` | Données de test pour valider export |
| `db_ingest_cvat.py` | Import export CVAT |
| `db_export.py` | Export COCO (pivot) ou YOLO (dérivé) — `--split-by media\|session\|site` obligatoire, dossier versionné + `manifest.json` |
| `audit_dataset.py` | Audit du **dernier export** (`export_runs`) : effectifs par classe, split, manifeste |
| `train_detect.py` | Entraînement Ultralytics |
| `infer_track_count.py` | Détection + tracking + comptage |
| `detect_grazing.py` | Broute par heuristiques |
| `export_onnx.py` | Export pour C++ |

## Taxonomie

Arbre extensible dans la DB. Labels provisoires (`is_provisional=1`) affinables via reclassification :

Au chargement des menus taxonomiques, l'application synchronise aussi le
référentiel embarqué « Fricke et al. — Liste des poissons de Madagascar,
nouvelle classification v230202 » : 249 familles, 849 genres et 1 812 espèces.
La synchronisation est idempotente, conserve les noms vernaculaires corrigés
localement et ne lance aucun entraînement Fishial ou YOLO. Pour une nouvelle
espèce, le libellé proposé privilégie le nom français, puis le nom anglais et
enfin le nom malgache quand les précédents manquent.

```python
from src.annodb.connection import session_scope
from src.annodb.taxonomy import reclassify_annotations
with session_scope() as s:
    reclassify_annotations(s, "taxon-sp-unknown-acanthurus", "nouveau-node-id")
```

## Vision produit (phase 3)

Onglet Mesure AquaMeasure : pause frame → overlay bbox/espèce/compteur → correction → alimente la DB → ré-entraînement périodique.

## Structure

```
fish-vision/
├── db/              schema SQL + seed taxonomie
├── src/annodb/      ORM, export, ingestion CVAT
├── src/counter.py   comptage ligne/zone
├── src/grazing.py   heuristiques broute
├── src/track_store.py
├── configs/
├── scripts/
└── data/            DB + médias (gitignored)
```
