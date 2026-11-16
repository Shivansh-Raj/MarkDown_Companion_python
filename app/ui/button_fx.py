"""V1-style button outline: a 2px border plus a thinner hard offset shadow (CSS `box-shadow: 2px 2px 0`).

QSS cannot do box-shadows, so every push/tool button gets a zero-blur QGraphicsDropShadowEffect. The shadow
collapses while the button is pressed, like V1's `translate(2px, 2px)` press.
"""
from __future__ import annotations

import weakref

from PySide6.QtCore import QEvent, QObject
from PySide6.QtGui import QColor
from PySide6.QtWidgets import QAbstractButton, QGraphicsDropShadowEffect, QPushButton, QToolButton

from ..markdown.theme import load_theme

OFFSET = 2


class ButtonFx(QObject):
    def __init__(self, theme: str):
        super().__init__()
        self._color = QColor(load_theme(theme)["shadow"])
        self._effects: "weakref.WeakSet[QGraphicsDropShadowEffect]" = weakref.WeakSet()

    def set_theme(self, theme: str) -> None:
        self._color = QColor(load_theme(theme)["shadow"])
        for fx in list(self._effects):
            fx.setColor(self._color)

    def eventFilter(self, obj, e) -> bool:  # noqa: N802
        if isinstance(obj, (QPushButton, QToolButton)):
            t = e.type()
            if t == QEvent.Type.Polish and obj.graphicsEffect() is None:
                fx = QGraphicsDropShadowEffect(obj)
                fx.setBlurRadius(0)
                fx.setOffset(OFFSET, OFFSET)
                fx.setColor(self._color)
                obj.setGraphicsEffect(fx)
                self._effects.add(fx)
            elif t in (QEvent.Type.MouseButtonPress, QEvent.Type.MouseButtonRelease):
                fx = obj.graphicsEffect()
                if isinstance(fx, QGraphicsDropShadowEffect) and isinstance(obj, QAbstractButton):
                    pressed = t == QEvent.Type.MouseButtonPress and obj.isEnabled()
                    fx.setOffset(0 if pressed else OFFSET, 0 if pressed else OFFSET)
        return False
