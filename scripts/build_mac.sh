#!/usr/bin/env bash
# Build macOS — AquaMeasure.app + IA (modèles déjà présents dans fish-vision/models/)
# Prérequis : macOS 12+, Python 3.10+, Xcode CLT
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT"

# shellcheck disable=SC1091
source "$ROOT/scripts/find_python_mac.sh"

echo "=== AquaMeasure — build macOS (avec IA) ==="
echo "Racine : $ROOT"

if [[ "$(uname -s)" != "Darwin" ]]; then
  echo "ERREUR : ce script doit être exécuté sur macOS."
  exit 1
fi

MODELS_SRC="$ROOT/fish-vision/models"

has_detect_model=false
for name in fish_detect_family.pt fish_detect_public.pt; do
  if [[ -f "$MODELS_SRC/$name" ]]; then
    has_detect_model=true
    break
  fi
done
if [[ "$has_detect_model" != true ]]; then
  echo "ERREUR : modèle YOLO introuvable dans fish-vision/models/"
  echo "  Copiez le dossier fish-vision/models/ depuis votre machine Windows."
  exit 1
fi

PYTHON="$(ensure_python310)"
echo "Python : $("$PYTHON" --version)"
ensure_venv_mac "$ROOT" "$PYTHON"

# shellcheck disable=SC1091
source "$ROOT/.venv-mac/bin/activate"

pip install --upgrade pip
pip install -r requirements-aquameasure.txt

rm -rf build dist
pyinstaller --noconfirm aquameasure.spec

OUT="$ROOT/dist"
APP="$OUT/AquaMeasure.app"
if [[ ! -d "$APP" ]]; then
  echo "ERREUR : $APP introuvable"
  exit 1
fi

mkdir -p "$OUT/camera_parameters"
touch "$OUT/camera_parameters/.gitkeep"

# Modèles IA à côté du .app (copie locale, pas de téléchargement)
mkdir -p "$OUT/fish-vision"
rm -rf "$OUT/fish-vision/models"
cp -R "$MODELS_SRC" "$OUT/fish-vision/models"

ARCHIVE="$OUT/AquaMeasure-macos-test.zip"
rm -f "$ARCHIVE"
(
  cd "$OUT"
  zip -r "$(basename "$ARCHIVE")" AquaMeasure.app camera_parameters fish-vision
)

echo ""
echo "OK — Artefacts :"
echo "  App     : $APP"
echo "  Modèles : $OUT/fish-vision/models/"
echo "  Zip     : $ARCHIVE"
echo ""
echo "Test : dézipper, calib dans camera_parameters/, puis :"
echo "  open AquaMeasure.app"
