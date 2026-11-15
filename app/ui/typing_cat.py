"""A pixel cat typing on a keyboard, painted with QPainter. Static when animations are off."""
from __future__ import annotations

from PySide6.QtCore import QRectF, QSize, QTimer, Qt
from PySide6.QtGui import QColor, QPainter
from PySide6.QtWidgets import QWidget

from ..markdown.theme import load_theme

GRID_W, GRID_H, SCALE = 32, 24, 4
_HEAD = [(9, 3, 3, 3), (20, 3, 3, 3), (9, 6, 14, 9), (12, 6, 8, 1)]   # ears + head blocks


class TypingCat(QWidget):
    def __init__(self, theme: str = "retro", animated: bool = True, parent=None):
        super().__init__(parent)
        self._t = load_theme(theme)
        self._animated = animated
        self._frame = 0
        self.setFixedSize(QSize(GRID_W * SCALE, GRID_H * SCALE))
        self._timer = QTimer(self, interval=125)
        self._timer.timeout.connect(self._tick)

    def start(self) -> None:
        if self._animated:
            self._timer.start()

    def stop(self) -> None:
        self._timer.stop()

    def set_theme(self, theme: str) -> None:
        self._t = load_theme(theme)
        self.update()

    def set_animated(self, on: bool) -> None:
        self._animated = on
        if not on:
            self._timer.stop()
            self._frame = 0
            self.update()

    def _tick(self) -> None:
        self._frame = (self._frame + 1) % 40
        self.update()

    def paintEvent(self, _e) -> None:  # noqa: N802
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing, False)
        p.scale(SCALE, SCALE)
        t = self._t

        def rect(x, y, w, h, color):
            p.fillRect(QRectF(x, y, w, h), QColor(color))

        fill, edge, ink, accent, muted = t["bg_alt"], t["border_hi"], t["fg"], t["accent"], t["muted"]
        rect(9, 3, 3, 4, fill); rect(20, 3, 3, 4, fill); rect(8, 6, 16, 10, fill)       # ears + head
        rect(9, 3, 3, 1, edge); rect(20, 3, 3, 1, edge)
        rect(8, 6, 1, 10, edge); rect(23, 6, 1, 10, edge); rect(9, 15, 14, 1, edge)
        blink = self._animated and self._frame % 40 in (38, 39)
        eye_h = 1 if blink else 3
        rect(12, 9, 2, eye_h, ink); rect(18, 9, 2, eye_h, ink)                           # eyes
        rect(15, 12, 2, 1, accent)                                                       # nose
        tap = self._frame % 4
        lift_l = 1 if self._animated and tap in (0, 1) else 0
        lift_r = 1 if self._animated and tap in (2, 3) else 0
        rect(10, 16 + lift_l, 4, 3 - lift_l, fill); rect(10, 18, 4, 1, edge)             # paws
        rect(18, 16 + lift_r, 4, 3 - lift_r, fill); rect(18, 18, 4, 1, edge)
        rect(4, 19, 24, 5, ink)                                                          # keyboard
        for i in range(8):
            hot = self._animated and (self._frame + i) % 4 == 0
            rect(5 + i * 3, 20, 2, 1, accent if hot else muted)
        for i in range(7):
            hot = self._animated and (self._frame + i + 2) % 4 == 0
            rect(6 + i * 3, 22, 2, 1, accent if hot else muted)
        p.end()
