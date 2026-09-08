#!/usr/bin/env bash
# AquaMeasure macOS — lance l'app en Python (sans build PyInstaller)
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT"

# shellcheck disable=SC1091
source "$ROOT/scripts/find_python_mac.sh"

if [[ "$(uname -s)" != "Darwin" ]]; then
  echo "ERREUR : script réservé à macOS."
  exit 1
fi

MODELS="$ROOT/fish-vision/models"
if ! ls "$MODELS"/fish_detect_*.pt 1>/dev/null 2>&1; then
  echo "ERREUR : copiez fish-vision/models/ depuis votre machine Windows."
  exit 1
fi

PYTHON="$(ensure_python310)"
echo "Python : $("$PYTHON" --version)"
ensure_venv_mac "$ROOT" "$PYTHON"

# shellcheck disable=SC1091
source "$ROOT/.venv-mac/bin/activate"

pip install --upgrade pip -q
pip install -r requirements-aquameasure.txt -q

echo "=== Lancement AquaMeasure ==="
python aquameasure.py
