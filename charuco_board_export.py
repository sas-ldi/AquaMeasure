"""Génération PNG de mires ChArUco (OpenCV) — partagé CLI + PySide."""

from __future__ import annotations

import os

import cv2 as cv
import numpy as np

DEFAULT_MARKER_RATIO = 37.0 / 49.5

DICT_BY_NAME: dict[str, int] = {
    "DICT_4X4_50": cv.aruco.DICT_4X4_50,
    "DICT_4X4_100": cv.aruco.DICT_4X4_100,
    "DICT_4X4_250": cv.aruco.DICT_4X4_250,
    "DICT_4X4_1000": cv.aruco.DICT_4X4_1000,
    "DICT_5X5_50": cv.aruco.DICT_5X5_50,
    "DICT_5X5_100": cv.aruco.DICT_5X5_100,
    "DICT_5X5_250": cv.aruco.DICT_5X5_250,
    "DICT_5X5_1000": cv.aruco.DICT_5X5_1000,
    "DICT_6X6_50": cv.aruco.DICT_6X6_50,
    "DICT_6X6_100": cv.aruco.DICT_6X6_100,
    "DICT_6X6_250": cv.aruco.DICT_6X6_250,
    "DICT_6X6_1000": cv.aruco.DICT_6X6_1000,
}

DICT_SIZES = [50, 100, 250, 1000]


def dict_name_for_id(dict_id: int) -> str:
    for name, did in DICT_BY_NAME.items():
        if did == dict_id:
            return name
    return "DICT_5X5_50"


def pick_dict_id(cols: int, rows: int, bit_size: int = 5) -> int:
    need = (cols * rows + 1) // 2
    for n in DICT_SIZES:
        if need <= n:
            return DICT_BY_NAME[f"DICT_{bit_size}X{bit_size}_{n}"]
    return DICT_BY_NAME[f"DICT_{bit_size}X{bit_size}_1000"]


def resolve_dict_id(cols: int, rows: int, dict_name: str, *, auto: bool) -> tuple[int, str]:
    if auto or dict_name not in DICT_BY_NAME:
        dict_id = pick_dict_id(cols, rows)
        return dict_id, dict_name_for_id(dict_id)
    dict_id = DICT_BY_NAME[dict_name]
    need = (cols * rows + 1) // 2
    auto_id = pick_dict_id(cols, rows)
    auto_name = dict_name_for_id(auto_id)
    if dict_id != auto_id:
        auto_n = int(auto_name.rsplit("_", 1)[-1])
        chosen_n = int(dict_name.rsplit("_", 1)[-1]) if dict_name.rsplit("_", 1)[-1].isdigit() else 0
        if need > chosen_n:
            return auto_id, auto_name
    return dict_id, dict_name


def mm_to_px(mm: float, dpi: int) -> int:
    return max(1, int(round(mm / 25.4 * dpi)))


def default_output_name(
    cols: int,
    rows: int,
    square_mm: float,
    page_w_mm: float,
    page_h_mm: float,
) -> str:
    sq = int(square_mm) if abs(square_mm - round(square_mm)) < 0.05 else round(square_mm, 1)
    pw = int(page_w_mm)
    ph = int(page_h_mm)
    return f"mire_{pw}x{ph}_{cols}x{rows}_carre{sq}mm.png"


def write_charuco_board_png(
    dest_dir: str,
    *,
    cols: int,
    rows: int,
    square_mm: float,
    marker_mm: float,
    page_w_mm: float,
    page_h_mm: float,
    dpi: int = 300,
    dict_name: str = "DICT_5X5_50",
    auto_dict: bool = True,
    filename: str | None = None,
) -> tuple[str, str]:
    """Écrit un PNG centré sur la feuille. Retourne (chemin, dict_name utilisé)."""
    if cols < 3 or rows < 3:
        raise ValueError("La grille doit avoir au moins 3 colonnes et 3 lignes.")
    if square_mm <= 0:
        raise ValueError("Côté de case invalide.")
    if marker_mm <= 0 or marker_mm >= square_mm:
        raise ValueError("Marqueur invalide (doit être > 0 et < côté case).")
    if page_w_mm <= 0 or page_h_mm <= 0:
        raise ValueError("Dimensions de feuille invalides.")

    dict_id, dict_used = resolve_dict_id(cols, rows, dict_name, auto=auto_dict)
    dictionary = cv.aruco.getPredefinedDictionary(dict_id)
    board = cv.aruco.CharucoBoard((cols, rows), square_mm, marker_mm, dictionary)

    board_w_mm = cols * square_mm
    board_h_mm = rows * square_mm
    if board_w_mm > page_w_mm + 0.5 or board_h_mm > page_h_mm + 0.5:
        raise ValueError(
            f"Mire {board_w_mm:.0f}×{board_h_mm:.0f} mm plus grande que la feuille "
            f"{page_w_mm:.0f}×{page_h_mm:.0f} mm.")

    bw = mm_to_px(board_w_mm, dpi)
    bh = mm_to_px(board_h_mm, dpi)
    pw = mm_to_px(page_w_mm, dpi)
    ph = mm_to_px(page_h_mm, dpi)

    board_img = board.generateImage((bw, bh), marginSize=0, borderBits=1)
    canvas = np.full((ph, pw), 255, dtype=np.uint8)
    x0 = max(0, (pw - bw) // 2)
    y0 = max(0, (ph - bh) // 2)
    canvas[y0 : y0 + bh, x0 : x0 + bw] = board_img

    name = filename or default_output_name(cols, rows, square_mm, page_w_mm, page_h_mm)
    out_path = os.path.join(dest_dir, name)
    os.makedirs(dest_dir, exist_ok=True)
    if not cv.imwrite(out_path, canvas):
        raise OSError(f"Écriture impossible : {out_path}")
    return os.path.abspath(out_path), dict_used
