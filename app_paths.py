"""Chemins racine — dev (aquameasure.py) ou binaire PyInstaller (.app macOS)."""

from __future__ import annotations

import os
import sys


def app_root() -> str:
    if getattr(sys, 'frozen', False):
        exe = os.path.abspath(sys.executable)
        exe_dir = os.path.dirname(exe)
        if (os.path.basename(exe_dir) == 'MacOS'
                and os.path.basename(os.path.dirname(exe_dir)) == 'Contents'):
            return os.path.dirname(os.path.dirname(os.path.dirname(exe_dir)))
        return exe_dir
    return os.path.dirname(os.path.abspath(__file__))
