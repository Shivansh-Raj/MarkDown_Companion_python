"""Tiny 90s arcade attract-mode animation for the home screen (slowly marching invaders over drifting stars)."""
from __future__ import annotations

import random

from PySide6.QtCore import QRect, QTimer, Qt
from PySide6.QtGui import QColor, QPainter
from PySide6.QtWidgets import QWidget

from ..markdown.theme import load_theme

_INV = (("00100000100", "00010001000", "00111111100", "01101110110", "11111111111", "10111111101", "10100000101", "00011011000"),
        ("00100000100", "10010001001", "10111111101", "11101110111", "11111111111", "01111111110", "00100000100", "01000000010"))
_SHIP = ("000010000", "000111000", "000111000", "111111111", "111111111")
_COIN = (("0011100", "0111110", "1111111", "1111111", "1111111", "0111110", "0011100"),
         ("0001000", "0011100", "0011100", "0011100", "0011100", "0011100", "0001000"),
         ("0011100", "0110110", "1101011", "1101011", "1101011", "0110110", "0011100"),
         ("0001000", "0011100", "0011100", "0011100", "0011100", "0011100", "0001000"))
_S = 2                     # screen pixels per sprite pixel
_COLS, _ROWS = 5, 1


class PixelStage(QWidget):
    def __init__(self, settings, parent=None):
        super().__init__(parent)
        self._settings = settings
        self.setFixedHeight(40)
        self.setObjectName("stage")
        self._rng = random.Random(7)
        self._tick = 0
        self._stars = [[self._rng.random(), self._rng.randrange(0, 32), self._rng.choice((1, 2))] for _ in range(14)]
        self._alive = [[True] * _COLS for _ in range(_ROWS)]
        self._timer = QTimer(self)
        self._timer.setInterval(110)
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
        t = (self._tick * 0.35) % (2 * span)
        return 4 + (t if t < span else 2 * span - t)

    def _step(self) -> None:
        self._tick += 1
        for s in self._stars:
            s[0] = (s[0] - 0.0012 * s[2]) % 1.0
        self.update()

    # drawing ----------------------------------------------------------------------
    def _sprite(self, p: QPainter, rows, x: float, y: float, color: QColor) -> None:
        p.setBrush(color)
        for j, row in enumerate(rows):
            for i, ch in enumerate(row):
                if ch == "1":
                    p.drawRect(int(x) + i * _S, int(y) + j * _S, _S, _S)

    def paintEvent(self, e) -> None:  # noqa: N802
        t = load_theme(self._settings.get("theme") or "retro")
        accent, fg = QColor(t["accent"]), QColor(t["fg"])
        accent.setAlpha(150)
        p = QPainter(self)
        p.setPen(Qt.PenStyle.NoPen)
        p.fillRect(self.rect(), QColor(t["code_bg"]))
        w, h = self.width(), self.height()
        dim = QColor(t["muted"])
        for x, y, sz in self._stars:                       # twinkling parallax stars
            c = QColor(fg if (self._tick // 6 + int(y)) % 5 == 0 else dim)
            p.fillRect(int(x * w), int(y), sz, sz, c)
        coin = QColor(t["accent2"])
        coin.setAlpha(170)                                 # slow spinning coin in the corner
        cs = _COIN[(self._tick // 4) % 4]
        self._sprite(p, cs, w - 7 * _S - 10, 6 + (1 if (self._tick // 10) % 2 else 0), coin)
        k = self._tick % 90                                # an occasional shooting star
        if k < 14:
            sx, sy = int(w * 0.55) - k * 9, 4 + k * 2
            for i in range(5):
                a = QColor(fg)
                a.setAlpha(150 - i * 30)
                p.fillRect(sx + i * 4, sy - i * 2, 2, 2, a)
        frame = (self._tick // 5) % 2
        fx = self._fleet_x()
        for r in range(_ROWS):
            for c in range(_COLS):
                if self._alive[r][c]:
                    self._sprite(p, _INV[frame], fx + c * 15 * _S, 12 + r * 11 * _S, accent)
        p.fillRect(0, h - 4, w, 2, QColor(t["border"]))    # ground line
        p.end()
