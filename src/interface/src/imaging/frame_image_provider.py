from __future__ import annotations

from PySide6.QtGui import QImage
from PySide6.QtQuick import QQuickImageProvider

from src.imaging.qimage_util import strip_image_url_id


class FrameImageProvider(QQuickImageProvider):
    def __init__(self):
        super().__init__(QQuickImageProvider.Image)
        self._images: dict[str, QImage] = {}

    def requestImage(self, image_id, size, requested_size):  # noqa: N802
        key = strip_image_url_id(image_id)
        img = self._images.get(key)
        if img is None or img.isNull():
            fallback = QImage(1, 1, QImage.Format_RGB32)
            fallback.fill(0)
            return fallback
        if size is not None:
            size.setWidth(img.width())
            size.setHeight(img.height())
        return img

    def set_image(self, image_id: str, image: QImage) -> None:
        self._images[image_id] = image

    def has_image(self, image_id: str) -> bool:
        return image_id in self._images
