"""The shared window chrome of the note and home windows, for any other window that should look the same:
frameless, always on top, a pixel frame, the accent title bar and edge/corner resizing."""
from __future__ import annotations

from PySide6.QtCore import QPoint, Qt
from PySide6.QtGui import QMouseEvent
from PySide6.QtWidgets import QVBoxLayout, QWidget

from .markdown_window import CORNER, EDGE
from .title_bar import TitleBar

_E = Qt.Edge


def chrome_flags(dialog: bool = False) -> Qt.WindowType:
    base = Qt.WindowType.Dialog if dialog else Qt.WindowType.Window
    return base | Qt.WindowType.FramelessWindowHint | Qt.WindowType.WindowStaysOnTopHint


def build_chrome(window: QWidget, title: str, buttons: tuple[str, ...] = ("minimize", "close")) -> tuple[TitleBar, QVBoxLayout]:
    """Give `window` the standard frame and title bar. Returns the title bar and the layout of the (themed) body to fill."""
    window.setObjectName("root")                              # the dark outer edge, as in the other windows
    window.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
    window.setMouseTracking(True)
    outer = QVBoxLayout(window)
    outer.setContentsMargins(EDGE, EDGE, EDGE, EDGE)
    frame = QWidget(window)
    frame.setObjectName("frame")
    frame.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
    outer.addWidget(frame)
    fl = QVBoxLayout(frame)
    fl.setContentsMargins(2, 2, 2, 2)
    fl.setSpacing(0)
    bar = TitleBar(title, buttons=buttons)
    fl.addWidget(bar)
    body = QWidget()
    body.setObjectName("home")                                # themed background
    body.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
    fl.addWidget(body, 1)
    layout = QVBoxLayout(body)
    layout.setContentsMargins(12, 10, 12, 10)
    layout.setSpacing(8)
    window._chrome_body = body                                # kept for callers that need the body widget itself
    return bar, layout


class EdgeResize:
    """Mixin: resize a frameless window by its edges and corners (the OS does the actual resizing). Put it first in the bases."""

    def _edges_at(self, pos: QPoint) -> Qt.Edge:
        w, h = self.width(), self.height()
        edges = Qt.Edge(0)
        if pos.x() < EDGE or (pos.x() < CORNER and pos.y() > h - EDGE):
            edges |= _E.LeftEdge
        if pos.x() > w - EDGE or (pos.x() > w - CORNER and pos.y() > h - EDGE):
            edges |= _E.RightEdge
        if pos.y() < EDGE:
            edges |= _E.TopEdge
        if pos.y() > h - EDGE:
            edges |= _E.BottomEdge
        return edges

    def mouseMoveEvent(self, e: QMouseEvent) -> None:  # noqa: N802
        edges = self._edges_at(e.position().toPoint())
        horiz, vert = bool(edges & (_E.LeftEdge | _E.RightEdge)), bool(edges & (_E.TopEdge | _E.BottomEdge))
        if horiz and vert:
            fwd = bool(edges & _E.LeftEdge) == bool(edges & _E.TopEdge)
            self.setCursor(Qt.CursorShape.SizeFDiagCursor if fwd else Qt.CursorShape.SizeBDiagCursor)
        elif horiz:
            self.setCursor(Qt.CursorShape.SizeHorCursor)
        elif vert:
            self.setCursor(Qt.CursorShape.SizeVerCursor)
        else:
            self.unsetCursor()
        super().mouseMoveEvent(e)

    def mousePressEvent(self, e: QMouseEvent) -> None:  # noqa: N802
        edges = self._edges_at(e.position().toPoint())
        if e.button() == Qt.MouseButton.LeftButton and edges and self.windowHandle() is not None:
            self.windowHandle().startSystemResize(edges)
            return
        super().mousePressEvent(e)
