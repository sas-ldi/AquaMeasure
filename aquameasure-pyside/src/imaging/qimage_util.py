"""Conversion OpenCV BGR → QImage affichable (image:// provider)."""

from __future__ import annotations

import cv2
import numpy as np
from PySide6.QtGui import QImage


def bgr_array_to_qimage(bgr: np.ndarray) -> QImage:
    if bgr is None or bgr.size == 0:
        return QImage()
    if bgr.ndim == 2:
        bgr = cv2.cvtColor(bgr, cv2.COLOR_GRAY2BGR)
    h, w = bgr.shape[:2]
    rgb = cv2.cvtColor(bgr, cv2.COLOR_BGR2RGB)
    rgb = np.ascontiguousarray(rgb)
    qimg = QImage(rgb.data, w, h, rgb.strides[0], QImage.Format_RGB888)
    return qimg.copy()


def strip_image_url_id(image_id: str) -> str:
    if not image_id:
        return image_id
    return image_id.split("?", 1)[0].split("#", 1)[0]
