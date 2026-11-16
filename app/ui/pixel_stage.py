"""Tiny 90s arcade attract-mode animation for the home screen (marching invaders, patrol ship, stars)."""
from __future__ import annotations

import random

from PySide6.QtCore import QRect, QTimer, Qt
from PySide6.QtGui import QColor, QPainter
from PySide6.QtWidgets import QWidget

from ..markdown.theme import load_theme

_INV = (("00100000100", "00010001000", "00111111100", "01101110110", "11111111111", "10111111101", "10100000101", "00011011000"),
        ("00100000100", "10010001001", "10111111101", "11101110111", "11111111111", "01111111110", "00100000100", "01000000010"))
_SHIP = ("000010000", "000111000", "000111000", "111111111", "111111111")
_S = 2                     # screen pixels per sprite pixel
_COLS, _ROWS = 6, 2


class PixelStage(QWidget):
    def __init__(self, settings, parent=None):
        super().__init__(parent)
        self._settings = settings
        self.setFixedHeight(76)
        self.setObjectName("stage")
        self._rng = random.Random(7)
        self._tick = 0
        self._stars = [[self._rng.random(), self._rng.randrange(0, 60), self._rng.choice((1, 2))] for _ in range(26)]
        self._alive = [[True] * _COLS for _ in range(_ROWS)]
        self._ship_x, self._ship_dir = 40.0, 1
        self._bullet = None            # [x, y]
        self._boom = []                # [x, y, ttl]
        self._timer = QTimer(self)
        self._timer.setInterval(60)
        self._timer.timeout.connect(self._step)

    def showEvent(self, e) -> None:  # noqa: N802
        if self._settings.get("animations") is not False:
            self._timer.start()
        super().showEvent(e)

    def hideEvent(self, e) -> None:  # noqa: N802
        self._timer.stop()
        super().hideEvent(e)

    # simulation -------------------------------------------------------------------
    def _fleet_x(self) -> float:
        span = max(10, self.width() - _COLS * 15 * _S - 8)
        t = (self._tick * 0.9) % (2 * span)
        return 4 + (t if t < span else 2 * span - t)

    def _step(self) -> None:
        self._tick += 1
        w = self.width()
        for s in self._stars:
            s[0] = (s[0] - 0.0035 * s[2]) % 1.0
        self._ship_x += 2.2 * self._ship_dir
        if self._ship_x < 6 or self._ship_x > w - 9 * _S - 6:
            self._ship_dir *= -1
        if self._bullet is None and self._tick % 14 == 0:
            self._bullet = [self._ship_x + 4 * _S, 52.0]
        if self._bullet:
            self._bullet[1] -= 5
            hit = self._hit(self._bullet[0], self._bullet[1])
            if hit or self._bullet[1] < 0:
                self._bullet = None
        self._boom = [[x, y, t - 1] for x, y, t in self._boom if t > 1]
        if not any(any(r) for r in self._alive):
            self._alive = [[True] * _COLS for _ in range(_ROWS)]
        self.update()

    def _hit(self, bx: float, by: float) -> bool:
        fx = self._fleet_x()
        for r in range(_ROWS):
            for c in range(_COLS):
                if self._alive[r][c]:
                    x, y = fx + c * 15 * _S, 6 + r * 11 * _S
                    if x <= bx <= x + 11 * _S and y <= by <= y + 8 * _S:
                        self._alive[r][c] = False
                        self._boom.append([x + 5 * _S, y + 4 * _S, 6])
                        return True
        return False

    # drawing ----------------------------------------------------------------------
    def _sprite(self, p: QPainter, rows, x: float, y: float, color: QColor) -> None:
        p.setBrush(color)
        for j, row in enumerate(rows):
            for i, ch in enumerate(row):
                if ch == "1":
                    p.drawRect(int(x) + i * _S, int(y) + j * _S, _S, _S)

    def paintEvent(self, e) -> None:  # noqa: N802
        t = load_theme(self._settings.get("theme") or "retro")
        accent, accent2, fg = QColor(t["accent"]), QColor(t["accent2"]), QColor(t["fg"])
        p = QPainter(self)
        p.setPen(Qt.PenStyle.NoPen)
        p.fillRect(self.rect(), QColor(t["code_bg"]))
        w, h = self.width(), self.height()
        dim = QColor(t["muted"])
        for x, y, sz in self._stars:                       # twinkling parallax stars
            c = QColor(fg if (self._tick // 6 + int(y)) % 5 == 0 else dim)
            p.fillRect(int(x * w), int(y), sz, sz, c)
        frame = (self._tick // 8) % 2
        fx = self._fleet_x()
        for r in range(_ROWS):
            for c in range(_COLS):
                if self._alive[r][c]:
                    self._sprite(p, _INV[frame], fx + c * 15 * _S, 6 + r * 11 * _S, accent if r == 0 else accent2)
        for x, y, ttl in self._boom:                       # little pixel explosions
            for dx, dy in ((-6, -6), (6, -6), (-6, 6), (6, 6), (0, -9), (0, 9), (-9, 0), (9, 0)):
                s = 3 if ttl > 3 else 2
                p.fillRect(int(x + dx * (7 - ttl) / 3), int(y + dy * (7 - ttl) / 3), s, s, fg)
        if self._bullet:
            p.fillRect(int(self._bullet[0]), int(self._bullet[1]), 2, 6, fg)
        self._sprite(p, _SHIP, self._ship_x, h - 5 * _S - 6, accent2)
        p.fillRect(0, h - 4, w, 2, QColor(t["border"]))    # ground line
        p.end()
