"""Custom pixel-style title bar: drag area + compact / refresh / opacity / minimize / close."""
from __future__ import annotations

from PySide6.QtCore import QPoint, QSize, Qt, Signal
from PySide6.QtGui import QMouseEvent
from PySide6.QtWidgets import QHBoxLayout, QLabel, QSizePolicy, QToolButton, QWidget

HEIGHT = 26
BTN_W, BTN_WIDE_W, BTN_H = 18, 38, 18


class ElidedLabel(QLabel):
    """Shrinks (with an ellipsis) instead of pushing its neighbours; the full text stays in the tooltip."""

    def __init__(self, text: str = "", parent: QWidget | None = None):
        super().__init__(parent)
        self._full = ""
        self.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Preferred)
        self.setText(text)

    def setText(self, text: str) -> None:  # noqa: N802
        self._full = text
        self._elide()

    def minimumSizeHint(self) -> QSize:  # noqa: N802
        return QSize(0, super().minimumSizeHint().height())

    def resizeEvent(self, e) -> None:  # noqa: N802
        super().resizeEvent(e)
        self._elide()

    def _elide(self) -> None:
        shown = self.fontMetrics().elidedText(self._full, Qt.TextElideMode.ElideRight, max(self.width(), 0))
        super().setText(shown if self.width() > 0 else self._full)
        self.setToolTip(self._full if shown != self._full else "")


class TitleBar(QWidget):
    ai_clicked = Signal()
    edit_clicked = Signal()
    home_clicked = Signal()
    compact_clicked = Signal()
    refresh_clicked = Signal()
    opacity_clicked = Signal()
    minimize_clicked = Signal()
    close_clicked = Signal()

    def __init__(self, title: str, parent: QWidget | None = None,
                 buttons: tuple[str, ...] = ("ai", "edit", "home", "compact", "refresh", "opacity", "minimize", "close")):
        super().__init__(parent)
        self.setObjectName("titlebar")
        self.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        self.setFixedHeight(HEIGHT)
        lay = QHBoxLayout(self)
        lay.setContentsMargins(8, 2, 4, 2)
        lay.setSpacing(3)
        self.title = ElidedLabel(title)
        self.title.setObjectName("title")
        lay.addWidget(self.title, 1)
        self.edit_btn = None
        self.ai_btn = None
        if "ai" in buttons:
            self.ai_btn = self._button("AI", "Edit with AI", self.ai_clicked, role="wide")
        if "edit" in buttons:
            self.edit_btn = self._button("EDIT", "Edit the Markdown source (Ctrl+E)", self.edit_clicked, role="wide")
        if "home" in buttons:
            self._button("⌂", "Home screen (Ctrl+Shift+H)", self.home_clicked)
        self.compact_btn = self._button("▴", "Roll up / expand (double-click title)", self.compact_clicked)
        self.compact_btn.setVisible("compact" in buttons)
        for name, glyph, tip, sig in (("refresh", "↻", "Refresh from disk (F5)", self.refresh_clicked),
                                      ("opacity", "◐", "Cycle opacity", self.opacity_clicked),
                                      ("minimize", "_", "Minimize", self.minimize_clicked)):
            if name in buttons:
                self._button(glyph, tip, sig)
        if "close" in buttons:
            self._button("×", "Close window", self.close_clicked, role="close")
        self._drag_origin: QPoint | None = None

    def _button(self, text: str, tip: str, signal: Signal, role: str = "") -> QToolButton:
        b = QToolButton(self)
        b.setObjectName("tbtn")
        b.setText(text)
        b.setToolTip(tip)
        b.setCursor(Qt.CursorShape.PointingHandCursor)
        b.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        b.setFixedSize(BTN_WIDE_W if role == "wide" else BTN_W, BTN_H)   # never squeezed by the layout
        if role:
            b.setProperty("role", role)
        b.clicked.connect(signal)
        self.layout().addWidget(b)
        return b

    def set_editing(self, editing: bool, available: bool = True) -> None:
        if self.edit_btn is not None:
            self.edit_btn.setVisible(not editing)
            self.edit_btn.setEnabled(available)
        if self.ai_btn is not None:
            self.ai_btn.setEnabled(available)

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
        if e.button() == Qt.MouseButton.LeftButton and self.compact_btn.isVisible():
            self.compact_clicked.emit()
