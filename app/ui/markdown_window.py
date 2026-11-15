"""One independent floating Markdown window."""
from __future__ import annotations

import os

from PySide6.QtCore import QPoint, QTimer, Qt, Signal
from PySide6.QtGui import QCursor, QKeySequence, QMouseEvent, QShortcut
from PySide6.QtWidgets import QHBoxLayout, QLabel, QPlainTextEdit, QPushButton, QVBoxLayout, QWidget

from ..ai.edit import locate_selection, utf16_to_index
from ..markdown.renderer import render_markdown
from ..services.synchronization import ToggleStatus
from .markdown_view import create_view
from .flow_layout import FlowLayout
from .title_bar import HEIGHT as TITLE_H, TitleBar

EDGE = 6                      # resize/shadow margin around the frame
CORNER = 14
MIN_W, MIN_H = 300, 140
OPACITY_STEPS = (1.0, 0.85, 0.7, 0.55)
DEFAULT_SIZE = (420, 520)
_E = Qt.Edge


class MarkdownWindow(QWidget):
    toggle_requested = Signal(str, int, bool)   # key, line, was_checked
    home_requested = Signal()
    edit_requested = Signal(str)                # key: user pressed EDIT
    save_requested = Signal(str, str, str)      # key, base digest, full text
    refresh_requested = Signal(str)
    close_requested = Signal(str)               # user hit close
    state_changed = Signal(str)                 # moved / resized / mode changed
    link_requested = Signal(str)                # relative .md link to open in another window

    def __init__(self, key: str, path: str, theme: str, opacity: float = 1.0, compact: bool = False):
        super().__init__(None, Qt.WindowType.Window | Qt.WindowType.FramelessWindowHint
                         | Qt.WindowType.WindowStaysOnTopHint)
        self.key, self.path, self.theme = key, path, theme
        self._compact = False
        self._editing = False
        self._available = True
        self._edit_base = ""
        self._draft0 = ""
        self._confirm_discard = False
        self._pending_text: str | None = None   # newer disk content queued while editing
        self._expanded_h = DEFAULT_SIZE[1]
        self._source = ""                        # latest file text shown (for AI edit)
        self._ext_version = 0                    # bumped whenever the file changes underneath us
        self._ai_services = None
        self.ai_panel = None
        self._ai_open = False
        self._ai_pending: str | None = None      # AI result waiting for edit mode to start
        self._ai_undo: list[str] = []
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

        self.editbar = QWidget()
        self.editbar.setObjectName("editbar")
        self.editbar.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        eb = FlowLayout(self.editbar, 0, 6, 4)          # wraps onto a second row in a narrow window
        eb.setContentsMargins(8, 4, 8, 4)
        self.save_btn = QPushButton("SAVE")
        self.save_btn.setObjectName("primary")
        self.cancel_btn = QPushButton("CANCEL")
        hint = QLabel("Ctrl+S saves \u00b7 Esc cancels")
        hint.setObjectName("hint")
        self.undo_ai_btn = QPushButton("UNDO AI")
        self.undo_ai_btn.setToolTip("Undo the last AI edit")
        self.undo_ai_btn.setEnabled(False)
        self.undo_ai_btn.clicked.connect(self.undo_ai)
        eb.addWidget(self.save_btn)
        eb.addWidget(self.cancel_btn)
        eb.addWidget(self.undo_ai_btn)
        eb.addWidget(hint)
        self.editbar.hide()
        self.editor = QPlainTextEdit()
        self.editor.setObjectName("editor")
        self.editor.setTabStopDistance(28)
        self.editor.hide()

        lay.addWidget(self.title_bar)
        lay.addWidget(self.status)
        lay.addWidget(self.editbar)
        lay.addWidget(self.view, 1)
        lay.addWidget(self.editor, 1)

        self._status_timer = QTimer(self, singleShot=True, interval=6000)
        self._status_timer.timeout.connect(self.clear_status)
        self.title_bar.close_clicked.connect(self._request_close)
        self.title_bar.edit_clicked.connect(lambda: self.edit_requested.emit(self.key))
        self.title_bar.ai_clicked.connect(self.open_ai)
        self.save_btn.clicked.connect(self.request_save)
        self.cancel_btn.clicked.connect(self.cancel_edit)
        self.title_bar.minimize_clicked.connect(self.showMinimized)
        self.title_bar.home_clicked.connect(self.home_requested)
        self.title_bar.compact_clicked.connect(self.toggle_compact)
        self.title_bar.refresh_clicked.connect(lambda: self.refresh_requested.emit(self.key))
        self.title_bar.opacity_clicked.connect(self.cycle_opacity)
        self.view.toggle_requested.connect(lambda line, chk: self.toggle_requested.emit(self.key, line, chk))
        self.view.file_link_requested.connect(self.link_requested)
        QShortcut(QKeySequence("F5"), self, activated=lambda: self.refresh_requested.emit(self.key))
        QShortcut(QKeySequence("Ctrl+W"), self, activated=self._request_close)
        QShortcut(QKeySequence("Ctrl+E"), self, activated=self._start_edit_shortcut)
        ctx = Qt.ShortcutContext.WidgetWithChildrenShortcut
        QShortcut(QKeySequence("Ctrl+S"), self.editor, activated=self.request_save, context=ctx)
        QShortcut(QKeySequence("Escape"), self.editor, activated=self.cancel_edit, context=ctx)

        self.set_opacity(opacity)
        if compact:
            self.set_compact(True)

    # content -------------------------------------------------------------------
    def load_markdown(self, text: str) -> None:
        self._source = text.lstrip("\ufeff")
        self.view.set_content(render_markdown(text), self.theme, os.path.dirname(self.path))

    def update_markdown(self, text: str) -> None:
        self._source = text.lstrip("\ufeff")
        self._ext_version += 1
        if self.ai_panel is not None:
            self.ai_panel.note_external_change(self._ext_version)
        if self._editing:           # never disturb the editor; show the newest version afterwards
            self._pending_text = text
            return
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

    # edit mode (mirrors V1: raw source, SAVE / CANCEL, hash-guarded save) --------------
    @property
    def editing(self) -> bool:
        return self._editing

    def is_dirty(self) -> bool:
        return self._editing and self.editor.toPlainText() != self._draft0

    def set_available(self, available: bool) -> None:
        self._available = available
        self.title_bar.set_editing(self._editing, available)

    def _start_edit_shortcut(self) -> None:
        if not self._editing and self._available:
            self.edit_requested.emit(self.key)

    def enter_edit(self, text: str, digest: str) -> None:
        if self._compact:
            self.set_compact(False)
        self._edit_base = digest
        self._close_armed = False
        self._pending_text = None
        self._confirm_discard = False
        self.editor.setPlainText(text.lstrip("\ufeff"))
        self._draft0 = self.editor.toPlainText()
        self._editing = True
        self._ai_undo = []
        self.clear_status()
        self.view.hide()
        self.editbar.show()
        self.editor.show()
        self.cancel_btn.setText("CANCEL")
        self.title_bar.set_editing(True)
        self.editor.setFocus()
        if self._ai_pending is not None:          # an AI result was waiting for edit mode
            pending, self._ai_pending = self._ai_pending, None
            self._ai_undo = [self.editor.toPlainText()]
            self.editor.setPlainText(pending)
        self._update_undo_ai()

    def exit_edit(self) -> None:
        self._editing = False
        self._ai_undo = []
        self._update_undo_ai()
        self._close_armed = False
        self._confirm_discard = False
        self.editor.hide()
        self.editbar.hide()
        self.view.show()
        self.title_bar.set_editing(False, self._available)
        if self._pending_text is not None:
            text, self._pending_text = self._pending_text, None
            self.view.update_body(render_markdown(text))

    def cancel_edit(self) -> None:
        if not self._editing:
            return
        if self.is_dirty() and not self._confirm_discard:
            self._confirm_discard = True          # second press discards
            self.cancel_btn.setText("DISCARD?")
            return
        self.exit_edit()

    def request_save(self) -> None:
        if not self._editing:
            return
        text = self.editor.toPlainText()
        if text == self._draft0 and self._pending_text is None:
            self.exit_edit()                      # nothing to write
            return
        self.save_requested.emit(self.key, self._edit_base, text)

    def save_finished(self, result) -> None:
        """Outcome of a save_requested (a ToggleResult-like object)."""
        if result is None:
            self.show_status("Could not save: the file is no longer tracked.", "error")
        elif result.status is ToggleStatus.OK:
            if result.snapshot is not None:
                self._pending_text = result.snapshot.text
            self.exit_edit()
            self.show_status("Saved.", "info")
        elif result.status is ToggleStatus.CONFLICT:
            # keep the user's text; the next SAVE deliberately overwrites the newer disk version
            if result.snapshot is not None:
                self._edit_base = result.snapshot.digest
                self._pending_text = result.snapshot.text
            self.show_status(result.message, "error", persistent=True)
        else:
            self.show_status(result.message, "error", persistent=True)

    # AI edit -----------------------------------------------------------------------------------
    def set_ai_services(self, ai) -> None:
        from .ai_edit_panel import AiEditPanel
        self._ai_services = ai
        self.ai_panel = AiEditPanel(ai, self)
        self.ai_panel.hide()
        self.ai_panel.applied.connect(self._on_ai_applied)
        self.ai_panel.closed.connect(self.close_ai)
        self.frame.layout().insertWidget(self.frame.layout().indexOf(self.view), self.ai_panel, 1)
        QShortcut(QKeySequence("Escape"), self.ai_panel, activated=self._ai_escape,
                  context=Qt.ShortcutContext.WidgetWithChildrenShortcut)

    def _ai_escape(self) -> None:
        panel = self.ai_panel
        if panel.stack.currentIndex() == 1:
            panel.stop()
        elif panel.stack.currentIndex() == 2:
            panel.stack.setCurrentIndex(0)
        else:
            panel.close_panel()

    def open_ai(self) -> None:
        if self.ai_panel is None or self._ai_open or not self._available:
            return
        if self._compact:
            self.set_compact(False)
        if self._editing:
            source = self.editor.toPlainText()
            cur = self.editor.textCursor()
            selection = None
            if cur.hasSelection():
                a = utf16_to_index(source, cur.selectionStart())
                b = utf16_to_index(source, cur.selectionEnd())
                if source[a:b].strip():
                    selection = (a, b)
        else:
            source = self._source
            selection = locate_selection(source, self.view.selected_text())
        self.ai_panel.open_for(source, selection, self._ext_version)
        self._set_ai_open(True)

    def _set_ai_open(self, on: bool) -> None:
        self._ai_open = on
        self.ai_panel.setVisible(on)
        if on:
            self.view.hide()
            self.editor.hide()
            self.editbar.hide()
        elif self._editing:
            self.editbar.show()
            self.editor.show()
            self.editor.setFocus()
        else:
            self.view.setVisible(not self._compact)

    def close_ai(self) -> None:
        if self._ai_open:
            self._set_ai_open(False)

    def _on_ai_applied(self, text: str) -> None:
        self._set_ai_open(False)
        if self._editing:
            self._ai_undo.append(self.editor.toPlainText())
            self.editor.setPlainText(text)
        else:
            self._ai_pending = text
            self.edit_requested.emit(self.key)      # starts edit mode, which then loads the pending text
            self._ai_pending = None                 # (cleared here if edit mode could not start)
        self._update_undo_ai()

    def undo_ai(self) -> None:
        if self._ai_undo:
            self.editor.setPlainText(self._ai_undo.pop())
        self._update_undo_ai()

    def _update_undo_ai(self) -> None:
        self.undo_ai_btn.setEnabled(bool(self._ai_undo))

    def _request_close(self) -> None:
        if self.is_dirty() and not getattr(self, "_close_armed", False):
            self._close_armed = True
            self.show_status("Unsaved changes! Press SAVE, or close again to discard them.", "error")
            return
        self._close_armed = False
        self.close_requested.emit(self.key)

    # modes ---------------------------------------------------------------------
    @property
    def compact(self) -> bool:
        return self._compact

    def toggle_compact(self) -> None:
        if self._editing:
            return
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
        if self.ai_panel is not None:
            self.ai_panel.cancel_worker()
        self._closing = True
        self.close()
