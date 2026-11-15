"""Programmatic pixel-art app icon (a sticky note), so no binary asset is required."""
from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtGui import QColor, QIcon, QImage, QPainter

_ART = [
    "................",
    ".KKKKKKKKKKKKKK.",
    ".KYYYYYYYYYYYYK.",
    ".KYOOOOOOOOOOYK.",
    ".KYYYYYYYYYYYYK.",
    ".KYKKKKKKKYYYYK.",
    ".KYYYYYYYYYYYYK.",
    ".KYKKKKKKKKKYYK.",
    ".KYYYYYYYYYYYYK.",
    ".KYKKKKKYYYYYYK.",
    ".KYYYYYYYYYYYYK.",
    ".KYYYYYYYYYYDDK.",
    ".KYYYYYYYYYDDDK.",
    ".KKKKKKKKKKKKKK.",
    "................",
    "................",
]
_COLORS = {"K": "#1e1f24", "Y": "#f2b134", "O": "#c8841a", "D": "#b8751a"}


def pixel_image(size: int = 256) -> QImage:
    img = QImage(16, 16, QImage.Format.Format_ARGB32)
    img.fill(Qt.GlobalColor.transparent)
    p = QPainter(img)
    for y, row in enumerate(_ART):
        for x, ch in enumerate(row):
            if ch in _COLORS:
                p.fillRect(x, y, 1, 1, QColor(_COLORS[ch]))
    p.end()
    return img.scaled(size, size, Qt.AspectRatioMode.IgnoreAspectRatio, Qt.TransformationMode.FastTransformation)


def app_icon() -> QIcon:
    return QIcon(__import__("PySide6.QtGui", fromlist=["QPixmap"]).QPixmap.fromImage(pixel_image(64)))
