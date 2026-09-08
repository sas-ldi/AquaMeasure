#!/usr/bin/env bash
# Trouve Python >= 3.10 (requis par aquameasure.py — syntaxe str | None)
find_python310() {
  local cmd ver major minor
  for cmd in python3.12 python3.11 python3.10 python3; do
    if ! command -v "$cmd" >/dev/null 2>&1; then
      continue
    fi
    ver="$("$cmd" -c 'import sys; print(f"{sys.version_info.major}.{sys.version_info.minor}")')"
    major="${ver%%.*}"
    minor="${ver#*.}"
    if (( major > 3 || (major == 3 && minor >= 10) )); then
      echo "$cmd"
      return 0
    fi
  done
  return 1
}

ensure_python310() {
  local py
  if ! py="$(find_python310)"; then
    echo "ERREUR : Python 3.10+ requis (macOS fournit souvent 3.9)."
    echo "  Installez : brew install python@3.11"
    echo "  Puis : rm -rf .venv-mac && ./scripts/run_mac.sh"
    exit 1
  fi
  echo "$py"
}

ensure_venv_mac() {
  local root="$1" py="$2"
  local venv="$root/.venv-mac"
  if [[ -d "$venv/bin" ]]; then
    local vver
    vver="$("$venv/bin/python" -c 'import sys; print(sys.version_info.minor)' 2>/dev/null || echo 0)"
    if (( vver < 10 )); then
      echo "Suppression .venv-mac (Python 3.$vver trop ancien)…"
      rm -rf "$venv"
    fi
  fi
  if [[ ! -d "$venv" ]]; then
    echo "Création venv ($py) : $venv"
    "$py" -m venv "$venv"
  fi
}
