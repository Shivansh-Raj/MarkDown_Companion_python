"""V1-style button outline: a 2px border plus a thinner hard offset shadow (CSS `box-shadow: 2px 2px 0`).

QSS cannot do box-shadows, so every push/tool button gets a zero-blur QGraphicsDropShadowEffect. The shadow
collapses while the button is pressed, like V1's `translate(2px, 2px)` press.
"""
from __future__ import annotations

import weakref

from PySide6.QtCore import QEvent, QObject
from PySide6.QtGui import QColor
from PySide6.QtWidgets import QAbstractButton, QGraphicsDropShadowEffect, QProxyStyle, QPushButton, QToolButton

from ..markdown.theme import load_theme

OFFSET = 2


class _Press(QObject):
    """Shared filter installed on each button only (never on the app, which would run Python for every event)."""

    def eventFilter(self, obj, e) -> bool:  # noqa: N802
        t = e.type()
        if t in (QEvent.Type.MouseButtonPress, QEvent.Type.MouseButtonRelease) and isinstance(obj, QAbstractButton):
            fx = obj.graphicsEffect()
            if isinstance(fx, QGraphicsDropShadowEffect):
                pressed = t == QEvent.Type.MouseButtonPress and obj.isEnabled()
                fx.setOffset(0 if pressed else OFFSET, 0 if pressed else OFFSET)
        return False


class ButtonFx(QProxyStyle):
    """Style wrapper: `polish()` runs once per widget, so there is no per-event overhead."""

    def __init__(self, theme: str, base: str | None = None):
        super().__init__(base)
        self._color = QColor(load_theme(theme)["shadow"])
        self._effects: "weakref.WeakSet[QGraphicsDropShadowEffect]" = weakref.WeakSet()
        self._press = _Press()

    def set_theme(self, theme: str) -> None:
        self._color = QColor(load_theme(theme)["shadow"])
        for fx in list(self._effects):
            fx.setColor(self._color)

    def polish(self, widget) -> None:
        super().polish(widget)
        if isinstance(widget, (QPushButton, QToolButton)) and widget.graphicsEffect() is None:
            fx = QGraphicsDropShadowEffect(widget)
            fx.setBlurRadius(0)
            fx.setOffset(OFFSET, OFFSET)
            fx.setColor(self._color)
            widget.setGraphicsEffect(fx)
            widget.installEventFilter(self._press)
            self._effects.add(fx)
