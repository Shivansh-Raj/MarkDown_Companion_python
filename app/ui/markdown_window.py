"""One independent floating Markdown window."""
from __future__ import annotations

import os

from PySide6.QtCore import QPoint, QTimer, Qt, Signal
from PySide6.QtGui import QCursor, QKeySequence, QMouseEvent, QShortcut
from PySide6.QtWidgets import QLabel, QVBoxLayout, QWidget

from ..markdown.renderer import render_markdown
from .markdown_view import create_view
from .title_bar import HEIGHT as TITLE_H, TitleBar

EDGE = 6                      # resize/shadow margin around the frame
CORNER = 14
MIN_W, MIN_H = 240, 140
OPACITY_STEPS = (1.0, 0.85, 0.7, 0.55)
DEFAULT_SIZE = (420, 520)
_E = Qt.Edge


class MarkdownWindow(QWidget):
    toggle_requested = Signal(str, int, bool)   # key, line, was_checked
    home_requested = Signal()
    refresh_requested = Signal(str)
    close_requested = Signal(str)               # user hit close
    state_changed = Signal(str)                 # moved / resized / mode changed
    link_requested = Signal(str)                # relative .md link to open in another window

    def __init__(self, key: str, path: str, theme: str, opacity: float = 1.0, compact: bool = False):
        super().__init__(None, Qt.WindowType.Window | Qt.WindowType.FramelessWindowHint
                         | Qt.WindowType.WindowStaysOnTopHint)
        self.key, self.path, self.theme = key, path, theme
        self._compact = False
        self._expanded_h = DEFAULT_SIZE[1]
        self.setObjectName("root")
        self.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        self.setMouseTracking(True)
        self.setMinimumSize(MIN_W, TITLE_H + 2 * EDGE + 4)
        self.resize(*DEFAULT_SIZE)
        self.setWindowTitle(os.path.basename(path))

        outer = QVBoxLayout(self)
        outer.setContentsMargins(EDGE, EDGE, EDGE, EDGE)
        self.frame = QWidget(self)
        self.frame.setObjectName("frame")
        self.frame.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        outer.addWidget(self.frame)
        lay = QVBoxLayout(self.frame)
        lay.setContentsMargins(2, 2, 2, 2)
        lay.setSpacing(0)

        self.title_bar = TitleBar(os.path.basename(path))
        self.title_bar.setToolTip(path)
        self.status = QLabel()
        self.status.setObjectName("status")
        self.status.setWordWrap(True)
        self.status.hide()
        self.view = create_view(self.frame)
        lay.addWidget(self.title_bar)
        lay.addWidget(self.status)
        lay.addWidget(self.view, 1)

        self._status_timer = QTimer(self, singleShot=True, interval=6000)
        self._status_timer.timeout.connect(self.clear_status)
        self.title_bar.close_clicked.connect(lambda: self.close_requested.emit(self.key))
        self.title_bar.minimize_clicked.connect(self.showMinimized)
        self.title_bar.home_clicked.connect(self.home_requested)
        self.title_bar.compact_clicked.connect(self.toggle_compact)
        self.title_bar.refresh_clicked.connect(lambda: self.refresh_requested.emit(self.key))
        self.title_bar.opacity_clicked.connect(self.cycle_opacity)
        self.view.toggle_requested.connect(lambda line, chk: self.toggle_requested.emit(self.key, line, chk))
        self.view.file_link_requested.connect(self.link_requested)
        QShortcut(QKeySequence("F5"), self, activated=lambda: self.refresh_requested.emit(self.key))
        QShortcut(QKeySequence("Ctrl+W"), self, activated=lambda: self.close_requested.emit(self.key))

        self.set_opacity(opacity)
        if compact:
            self.set_compact(True)

    # content -------------------------------------------------------------------
    def load_markdown(self, text: str) -> None:
        self.view.set_content(render_markdown(text), self.theme, os.path.dirname(self.path))

    def update_markdown(self, text: str) -> None:
        self.view.update_body(render_markdown(text))

    def set_theme(self, theme: str, text: str) -> None:
        self.theme = theme
        self.load_markdown(text)

    # status banner -------------------------------------------------------------
    def show_status(self, message: str, kind: str = "error", persistent: bool = False) -> None:
        self.status.setText(message)
        self.status.setProperty("kind", kind)
        self.status.style().unpolish(self.status)
        self.status.style().polish(self.status)
        self.status.setVisible(not self._compact)
        self._status_timer.stop()
        if not persistent:
            self._status_timer.start()

    def clear_status(self) -> None:
        self.status.hide()
        self.status.clear()

    # modes ---------------------------------------------------------------------
    @property
    def compact(self) -> bool:
        return self._compact

    def toggle_compact(self) -> None:
        self.set_compact(not self._compact)
        self.state_changed.emit(self.key)

    def set_compact(self, compact: bool) -> None:
        if compact == self._compact:
            return
        if compact:
            self._expanded_h = self.height()
            h = TITLE_H + 2 * EDGE + 4
            self._compact = True
            self.view.hide()
            self.status.hide()
            self.setFixedHeight(h)
        else:
            self._compact = False
            self.setMinimumHeight(TITLE_H + 2 * EDGE + 4)
            self.setMaximumHeight(16777215)
            self.view.show()
            self.status.setVisible(bool(self.status.text()))
            self.resize(self.width(), max(self._expanded_h, MIN_H))
        self.title_bar.set_compact(self._compact)

    def set_expanded_height(self, h: int) -> None:
        self._expanded_h = max(h, MIN_H)

    def set_opacity(self, value: float) -> None:
        self._opacity = min(1.0, max(0.3, value))
        self.setWindowOpacity(self._opacity)

    @property
    def opacity(self) -> float:
        return self._opacity

    def cycle_opacity(self) -> None:
        lower = [s for s in OPACITY_STEPS if s < self._opacity - 0.01]
        self.set_opacity(lower[0] if lower else OPACITY_STEPS[0])
        self.state_changed.emit(self.key)

    def state(self) -> dict:
        g = self.geometry()
        return {"x": g.x(), "y": g.y(), "w": g.width(),
                "h": self._expanded_h if self._compact else g.height(),
                "compact": int(self._compact), "expanded_h": self._expanded_h,
                "opacity": self._opacity}

    # focus ---------------------------------------------------------------------
    def bring_to_front(self) -> None:
        if self.isMinimized():
            self.setWindowState(self.windowState() & ~Qt.WindowState.WindowMinimized)
        self.show()
        self.raise_()
        self.activateWindow()

    # events --------------------------------------------------------------------
    def moveEvent(self, e) -> None:  # noqa: N802
        super().moveEvent(e)
        self.state_changed.emit(self.key)

    def resizeEvent(self, e) -> None:  # noqa: N802
        super().resizeEvent(e)
        if not self._compact:
            self._expanded_h = self.height()
        self.state_changed.emit(self.key)

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
        if self._compact:  # height is fixed while rolled up
            edges &= ~(_E.TopEdge | _E.BottomEdge)
        return edges

    def mouseMoveEvent(self, e: QMouseEvent) -> None:  # noqa: N802
        edges = self._edges_at(e.position().toPoint())
        diag = bool(edges & (_E.LeftEdge | _E.RightEdge)) and bool(edges & (_E.TopEdge | _E.BottomEdge))
        if diag:
            fwd = (edges & _E.LeftEdge and edges & _E.TopEdge) or (edges & _E.RightEdge and edges & _E.BottomEdge)
            self.setCursor(Qt.CursorShape.SizeFDiagCursor if fwd else Qt.CursorShape.SizeBDiagCursor)
        elif edges & (_E.LeftEdge | _E.RightEdge):
            self.setCursor(Qt.CursorShape.SizeHorCursor)
        elif edges & (_E.TopEdge | _E.BottomEdge):
            self.setCursor(Qt.CursorShape.SizeVerCursor)
        else:
            self.unsetCursor()

    def mousePressEvent(self, e: QMouseEvent) -> None:  # noqa: N802
        if e.button() == Qt.MouseButton.LeftButton:
            edges = self._edges_at(e.position().toPoint())
            handle = self.windowHandle()
            if edges and handle is not None:
                handle.startSystemResize(edges)

    def closeEvent(self, e) -> None:  # noqa: N802
        # Closing through the OS (Alt+F4, taskbar) behaves like the close button.
        if self.isVisible() and not getattr(self, "_closing", False):
            e.ignore()
            self.close_requested.emit(self.key)
        else:
            e.accept()

    def force_close(self) -> None:
        self._closing = True
        self.close()
