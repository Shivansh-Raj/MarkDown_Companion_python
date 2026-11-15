"""Custom pixel-style title bar: drag area + compact / refresh / opacity / minimize / close."""
from __future__ import annotations

from PySide6.QtCore import QPoint, Qt, Signal
from PySide6.QtGui import QMouseEvent
from PySide6.QtWidgets import QHBoxLayout, QLabel, QToolButton, QWidget

HEIGHT = 26


class TitleBar(QWidget):
    compact_clicked = Signal()
    refresh_clicked = Signal()
    opacity_clicked = Signal()
    minimize_clicked = Signal()
    close_clicked = Signal()

    def __init__(self, title: str, parent: QWidget | None = None):
        super().__init__(parent)
        self.setObjectName("titlebar")
        self.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        self.setFixedHeight(HEIGHT)
        lay = QHBoxLayout(self)
        lay.setContentsMargins(8, 2, 4, 2)
        lay.setSpacing(3)
        self.title = QLabel(title)
        self.title.setObjectName("title")
        lay.addWidget(self.title, 1)
        self.compact_btn = self._button("▴", "Roll up / expand (double-click title)", self.compact_clicked)
        self._button("↻", "Refresh from disk (F5)", self.refresh_clicked)
        self._button("◐", "Cycle opacity", self.opacity_clicked)
        self._button("_", "Minimize", self.minimize_clicked)
        self._button("×", "Close window", self.close_clicked, role="close")
        self._drag_origin: QPoint | None = None

    def _button(self, text: str, tip: str, signal: Signal, role: str = "") -> QToolButton:
        b = QToolButton(self)
        b.setObjectName("tbtn")
        b.setText(text)
        b.setToolTip(tip)
        b.setCursor(Qt.CursorShape.PointingHandCursor)
        b.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        if role:
            b.setProperty("role", role)
        b.clicked.connect(signal)
        self.layout().addWidget(b)
        return b

    def set_title(self, text: str) -> None:
        self.title.setText(text)

    def set_compact(self, compact: bool) -> None:
        self.compact_btn.setText("▾" if compact else "▴")

    # Dragging: prefer the OS-native move (smooth, snap-aware); fall back to manual.
    def mousePressEvent(self, e: QMouseEvent) -> None:  # noqa: N802
        if e.button() == Qt.MouseButton.LeftButton:
            handle = self.window().windowHandle()
            if handle is not None and handle.startSystemMove():
                return
            self._drag_origin = e.globalPosition().toPoint() - self.window().frameGeometry().topLeft()

    def mouseMoveEvent(self, e: QMouseEvent) -> None:  # noqa: N802
        if self._drag_origin is not None and e.buttons() & Qt.MouseButton.LeftButton:
            self.window().move(e.globalPosition().toPoint() - self._drag_origin)

    def mouseReleaseEvent(self, e: QMouseEvent) -> None:  # noqa: N802
        self._drag_origin = None

    def mouseDoubleClickEvent(self, e: QMouseEvent) -> None:  # noqa: N802
        if e.button() == Qt.MouseButton.LeftButton:
            self.compact_clicked.emit()
