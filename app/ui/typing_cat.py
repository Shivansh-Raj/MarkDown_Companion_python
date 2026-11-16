"""A pixel cat painted with QPainter.

Moods: "typing" (paws tap the keyboard while the AI works) and "waiting" (the AI is done: the cat looks
around and a bold pixel "?" pops in and bobs until the user accepts or discards). With animations off the
cat is static, and the "?" simply stays visible.
"""
from __future__ import annotations

from PySide6.QtCore import QRectF, QSize, QTimer, Qt
from PySide6.QtGui import QColor, QPainter
from PySide6.QtWidgets import QWidget

from ..markdown.theme import load_theme

GRID_W, GRID_H, SCALE = 32, 32, 3          # 8 rows of headroom above the cat for the "?"
TOP = 8                                    # cat artwork starts this many grid rows down
# bold 5x7 question mark: (x, y, width) per row, drawn from the top
_QUESTION = [(1, 0, 3), (0, 1, 2), (3, 1, 2), (3, 2, 2), (2, 3, 2), (2, 4, 1), (2, 6, 1)]
_LOOK = (0, -1, 0, 1)                      # eye offsets while waiting
POP_FRAMES = 4


class TypingCat(QWidget):
    def __init__(self, theme: str = "retro", animated: bool = True, mood: str = "typing", parent=None, scale: int = SCALE,
                 crop_top: int = 0):
        super().__init__(parent)
        self._t = load_theme(theme)
        self._animated = animated
        self._mood = mood
        self._frame = 0
        self._scale, self._crop = scale, crop_top      # crop_top: drop the empty "?" headroom rows
        self.setFixedSize(QSize(GRID_W * scale, (GRID_H - crop_top) * scale))
        self._timer = QTimer(self, interval=125)
        self._timer.timeout.connect(self._tick)

    @property
    def mood(self) -> str:
        return self._mood

    @property
    def running(self) -> bool:
        return self._timer.isActive()

    def set_mood(self, mood: str) -> None:
        if mood == self._mood:
            return
        self._mood = mood
        self._frame = 0
        self.update()

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
        self._frame = (self._frame + 1) % 400
        self.update()

    def paintEvent(self, _e) -> None:  # noqa: N802
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing, False)
        p.scale(self._scale, self._scale)
        p.translate(0, -self._crop)
        t, waiting, anim = self._t, self._mood == "waiting", self._animated

        def rect(x, y, w, h, color):
            p.fillRect(QRectF(x, y + TOP, w, h), QColor(color))

        ink, accent, muted = t["fg"], t["accent"], t["muted"]
        light = QColor(t["bg"]).lightness() > 128
        fill = t["code_bg"] if light else t["bg_alt"]        # on light themes use the lightest tone so fur != background
        edge = ink                                           # full dark outline keeps the cat readable on any theme
        rect(9, 3, 3, 4, fill); rect(20, 3, 3, 4, fill); rect(8, 6, 16, 10, fill)       # ears + head
        rect(9, 3, 3, 1, edge); rect(20, 3, 3, 1, edge)
        rect(9, 3, 1, 4, edge); rect(11, 3, 1, 3, edge); rect(20, 3, 1, 3, edge); rect(22, 3, 1, 4, edge)
        rect(12, 6, 8, 1, edge)                                                          # head top
        rect(8, 6, 1, 10, edge); rect(23, 6, 1, 10, edge); rect(9, 15, 14, 1, edge)

        blink = anim and self._frame % 40 in (38, 39)
        look = _LOOK[(self._frame // 5) % 4] if (waiting and anim) else 0               # eyes glance around
        eye_h = 1 if blink else 3
        rect(12 + look, 9, 2, eye_h, ink); rect(18 + look, 9, 2, eye_h, ink)
        rect(15, 12, 2, 1, accent)                                                       # nose

        typing = not waiting and anim                                                    # paws rest while waiting
        tap = self._frame % 4
        lift_l = 1 if typing and tap in (0, 1) else 0
        lift_r = 1 if typing and tap in (2, 3) else 0
        rect(10, 16 + lift_l, 4, 3 - lift_l, fill); rect(10, 18, 4, 1, edge)
        rect(18, 16 + lift_r, 4, 3 - lift_r, fill); rect(18, 18, 4, 1, edge)
        rect(4, 19, 24, 5, ink)                                                          # keyboard
        for i in range(8):
            hot = typing and (self._frame + i) % 4 == 0
            rect(5 + i * 3, 20, 2, 1, accent if hot else muted)
        for i in range(7):
            hot = typing and (self._frame + i + 2) % 4 == 0
            rect(6 + i * 3, 22, 2, 1, accent if hot else muted)

        if waiting:                                                                      # the "?"
            rows = len(_QUESTION)
            visible = rows if not anim else min(rows, 2 * (self._frame + 1))             # pops in from the top
            bob = -1 if anim and self._frame >= POP_FRAMES and (self._frame // 5) % 2 else 0
            for i, (x, y, w) in enumerate(_QUESTION[:visible]):
                p.fillRect(QRectF(24 + x, 1 + y + bob, w, 1), QColor(accent))
        p.end()
