#!/usr/bin/env python3
"""Verifie la machine a etats du suivi assiste (sans lancer de vrai tracking).

Exerce le cycle complet : suivi -> perte -> correction manuelle -> reprise,
puis l'escalade vers le pointage image par image quand les reprises echouent.

Usage : python aquameasure-pyside/tools/assist_tracking_check.py
Sortie : une ligne par scenario, code retour 1 si l'un d'eux echoue.
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import main as app_main  # noqa: E402

FAILURES: list[str] = []


def check(label: str, got, expected) -> None:
    ok = got == expected
    mark = "OK " if ok else "ECHEC"
    print(f"  [{mark}] {label} : {got!r}" + ("" if ok else f" (attendu {expected!r})"))
    if not ok:
        FAILURES.append(label)


def main() -> int:
    app_main._setup_paths()

    from PySide6.QtWidgets import QApplication

    from src.controllers.app_controller import AppController
    from src.controllers import fish_controller as fc

    app = QApplication(sys.argv)  # noqa: F841 — requis par les QObject
    ctrl = AppController()
    fish = ctrl.fish()

    print("\n--- etat initial ---")
    check("assistState", fish.assistState, fc.ASSIST_IDLE)
    check("assistActive", fish.assistActive, False)
    check("assistWaiting", fish.assistWaiting, False)

    # Simule un suivi lance de f100 a f200.
    fish._assist_start = 100
    fish._assist_end = 200
    fish._assist_cursor = 100
    fish._set_assist(fc.ASSIST_RUNNING, "Suivi en cours…")

    print("\n--- progression ---")
    fish._assist_cursor = 150
    check("assistProgress a mi-parcours", fish.assistProgress, 50)

    print("\n--- perte apres un suivi utile : on reste sur la frame fautive ---")
    fish._follow_frames = 40
    fish._assist_on_lost(150, "poisson perdu")
    check("etat", fish.assistState, fc.ASSIST_WAITING)
    check("assistWaiting", fish.assistWaiting, True)
    check("curseur cale sur la perte", fish._assist_cursor, 150)

    print("\n--- une bbox manuelle relance le suivi ---")
    check("bbox consommee par le suivi", fish._assist_on_manual_box(), True)

    print("\n--- le tracker ne repart pas : le rectangle fait foi, on avance ---")
    before = fish._assist_cursor
    fish._follow_frames = 0
    fish._assist_on_lost(before, "aucune detection")
    check("le curseur a avance d'une image", fish._assist_cursor, before + 1)
    check("toujours en attente d'une bbox", fish.assistState, fc.ASSIST_WAITING)

    print("\n--- pointage image par image : chaque reprise sterile avance ---")
    for _ in range(3):
        cur = fish._assist_cursor
        fish._follow_frames = 0
        fish._assist_on_lost(cur, "aucune detection")
        check(f"f{cur} -> f{cur + 1}", fish._assist_cursor, cur + 1)

    print("\n--- en fin de segment, on n'avance plus au-dela de Out ---")
    fish._assist_cursor = fish._assist_end
    fish._follow_frames = 0
    fish._assist_on_lost(fish._assist_end, "aucune detection")
    check("curseur borne a Out", fish._assist_cursor, fish._assist_end)

    print("\n--- fin de segment sans perte : retour au repos ---")
    fish._follow_lost_frame = -1
    fish._assist_on_finished()
    check("etat final", fish.assistState, fc.ASSIST_IDLE)
    check("assistActive", fish.assistActive, False)
    check("curseur cale sur la fin", fish._assist_cursor, fish._assist_end)

    print("\n--- hors suivi assiste, une bbox manuelle reste ordinaire ---")
    check("bbox non consommee", fish._assist_on_manual_box(), False)

    print()
    if FAILURES:
        print(f"{len(FAILURES)} verification(s) en echec : {', '.join(FAILURES)}")
        return 1
    print("Toutes les verifications passent.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
