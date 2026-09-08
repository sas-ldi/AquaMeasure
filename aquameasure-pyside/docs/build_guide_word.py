"""Compatibilité de l'ancien lanceur : les Markdown sont la source unique."""
from build_word import main

if __name__ == "__main__":
    raise SystemExit(main())
