"""AI edit panel shown inside a note window: scope + instruction -> streamed result -> diff -> apply."""
from __future__ import annotations

from PySide6.QtCore import QTimer, Signal
from PySide6.QtGui import QColor, QFont, QTextBlockFormat, QTextCharFormat, QTextCursor
from PySide6.QtCore import Qt
from PySide6.QtWidgets import (QButtonGroup, QComboBox, QFrame, QHBoxLayout, QLabel, QPlainTextEdit, QPushButton,
                               QRadioButton, QScrollArea, QStackedWidget, QTextEdit, QVBoxLayout, QWidget)

from ..ai.diff import diff_lines, has_changes, with_context
from ..ai.edit import (MAX_EDIT_CHARS, Scope, build_edited_document, list_sections, scope_text, system_prompt,
                       user_prompt)
from ..ai.services import AiServices
from ..markdown.theme import load_theme
from ..ai.worker import StreamWorker
from .flow_layout import FlowLayout
from .typing_cat import TypingCat

QUICK_ACTIONS = (
    ("Shorten", "Make this more concise without losing any meaning."),
    ("Fix grammar", "Fix spelling and grammar. Do not change the meaning or structure."),
    ("Add subtasks", "Break each task into 2-4 smaller subtasks as nested checkboxes."),
    ("Reorder by priority", "Reorder the items by priority, most important first."),
)
PAGE_INPUT, PAGE_RUN, PAGE_REVIEW = range(3)
# (background, text) for added / removed lines, per light or dark theme
_DIFF = {
    "dark": {"add": ("#12361f", "#b6f0c4"), "del": ("#451a1d", "#ffc9c6")},
    "light": {"add": ("#d1f2d8", "#0b3d1a"), "del": ("#ffd7d5", "#5a0f0b")},
}


def _is_light(theme: dict) -> bool:
    c = QColor(theme["bg"])
    return (0.299 * c.red() + 0.587 * c.green() + 0.114 * c.blue()) > 140


class AiEditPanel(QWidget):
    applied = Signal(str)       # the new full document text
    closed = Signal()

    def __init__(self, ai: AiServices, parent=None):
        super().__init__(parent)
        self.setObjectName("aipanel")
        self.ai = ai
        self._worker: StreamWorker | None = None
        self._source = ""
        self._version = 0
        self._start_version = 0
        self._result: str | None = None
        self._selection: tuple[int, int] | None = None
        self._rows: list = []
        self._shown = 0

        self.stack = QStackedWidget()
        self.stack.addWidget(self._input_page())
        self.stack.addWidget(self._run_page())
        self.stack.addWidget(self._review_page())
        self.error = QLabel()
        self.error.setObjectName("status")
        self.error.setWordWrap(True)
        self.error.hide()
        lay = QVBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.addWidget(self.error)
        lay.addWidget(self.stack, 1)
        self._reveal = QTimer(self, interval=25)
        self._reveal.timeout.connect(self._reveal_step)

    # pages -------------------------------------------------------------------------------------
    def _input_page(self) -> QWidget:
        w = QWidget()                              # the form scrolls instead of being crushed in a short window
        lay = QVBoxLayout(w)
        lay.setContentsMargins(10, 8, 10, 8)
        title = QLabel("AI EDIT")
        title.setObjectName("section")
        self.rb_sel = QRadioButton("My selected text")
        self.sel_preview = QLabel()
        self.sel_preview.setObjectName("hint")
        self.sel_preview.setWordWrap(True)
        self.rb_sec = QRadioButton("A heading section")
        self.sec_box = QComboBox()
        self.rb_whole = QRadioButton("Whole file")
        self.group = QButtonGroup(self)
        for rb in (self.rb_sel, self.rb_sec, self.rb_whole):
            self.group.addButton(rb)
        self.instruction = QPlainTextEdit()
        self.instruction.setObjectName("editor")
        self.instruction.setPlaceholderText("What should change? e.g. Add a rollback step")
        self.instruction.setMinimumHeight(72)
        self.instruction.setMaximumHeight(130)
        chips = FlowLayout(None, 0, 6, 6)          # wraps; long labels are never cut off
        for label, text in QUICK_ACTIONS:
            b = QPushButton(label)
            b.clicked.connect(lambda _=False, t=text: self.instruction.setPlainText(t))
            chips.addWidget(b)
        self.history_box = QComboBox()
        self.history_box.activated.connect(self._pick_history)
        note = QLabel("Completed tasks stay completed. Nothing is saved until you press SAVE.")
        note.setObjectName("hint")
        note.setWordWrap(True)
        self.go_btn = QPushButton("✎  Edit")
        self.go_btn.setObjectName("primary")
        self.go_btn.clicked.connect(self.start)
        cancel = QPushButton("Cancel")
        cancel.clicked.connect(self.close_panel)
        row = FlowLayout(None, 0, 6, 6)
        row.addWidget(self.go_btn)
        row.addWidget(cancel)
        for x in (title, self.rb_sel, self.sel_preview, self.rb_sec, self.sec_box, self.rb_whole):
            lay.addWidget(x)
        lay.addWidget(self.instruction)
        lay.addLayout(chips)
        lay.addWidget(self.history_box)
        lay.addWidget(note)
        lay.addLayout(row)
        lay.addStretch(1)
        scroll = QScrollArea()
        scroll.setObjectName("aiscroll")
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.Shape.NoFrame)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        scroll.setWidget(w)
        return scroll

    def _run_page(self) -> QWidget:
        w = QWidget()
        lay = QVBoxLayout(w)
        lay.setContentsMargins(10, 8, 10, 8)
        top = QHBoxLayout()
        self.cat = TypingCat(self.ai.theme_name(), self.ai.animations())
        self.run_label = QLabel("Editing")
        self.run_label.setObjectName("section")
        top.addWidget(self.cat)
        top.addWidget(self.run_label, 1)
        self.live = QPlainTextEdit()
        self.live.setReadOnly(True)
        self.live.setObjectName("editor")
        stop = QPushButton("■  Stop")
        stop.clicked.connect(self.stop)
        lay.addLayout(top)
        lay.addWidget(self.live, 1)
        lay.addWidget(stop)
        return w

    def _review_page(self) -> QWidget:
        w = QWidget()
        lay = QVBoxLayout(w)
        lay.setContentsMargins(10, 8, 10, 8)
        self.apply_btn = QPushButton("Apply")
        self.apply_btn.setObjectName("primary")
        self.apply_btn.clicked.connect(self.apply)
        again = QPushButton("Try again")
        again.clicked.connect(lambda: self.stack.setCurrentIndex(PAGE_INPUT))
        discard = QPushButton("Discard")
        discard.clicked.connect(self.close_panel)
        row = FlowLayout(None, 0, 6, 6)
        row.addWidget(self.apply_btn)
        row.addWidget(again)
        row.addWidget(discard)
        self.diff_view = QTextEdit()
        self.diff_view.setReadOnly(True)
        self.diff_view.setObjectName("editor")
        self.diff_view.setFont(QFont("Consolas", 10))
        lay.addLayout(row)
        lay.addWidget(self.diff_view, 1)
        return w

    # opening -------------------------------------------------------------------------------------
    def open_for(self, source: str, selection: tuple[int, int] | None, version: int) -> None:
        """`source` is the text the AI will see; `selection` indexes into it; `version` detects outside changes."""
        self._source, self._selection, self._start_version = source, selection, version
        self._version = version
        self.show_error()
        self.sec_box.clear()
        sections = list_sections(source)
        for i, s in enumerate(sections):
            self.sec_box.addItem("  " * (s.depth - 1) + (s.title or "(untitled)"), i)
        self._sections = sections
        self.rb_sec.setEnabled(bool(sections))
        self.sec_box.setEnabled(bool(sections))
        self.rb_sel.setEnabled(selection is not None)
        if selection is not None:
            text = source[selection[0]:selection[1]]
            self.sel_preview.setText("“" + text[:160] + ("…" if len(text) > 160 else "") + "”")
        else:
            self.sel_preview.setText("Highlight text in EDIT mode (or in the preview, if it appears only once), then click AI.")
        (self.rb_sel if selection is not None else self.rb_sec if sections else self.rb_whole).setChecked(True)
        self._refresh_history()
        self.stack.setCurrentIndex(PAGE_INPUT)
        self.instruction.setFocus()

    def note_external_change(self, version: int) -> None:
        self._version = version

    def _refresh_history(self) -> None:
        self.history_box.clear()
        self.history_box.addItem("Recent instructions…")
        for h in self.ai.history():
            self.history_box.addItem(h if len(h) < 60 else h[:59] + "…", h)

    def _pick_history(self, index: int) -> None:
        if index > 0:
            self.instruction.setPlainText(self.history_box.itemData(index))

    def show_error(self, text: str = "") -> None:
        self.error.setText(text)
        self.error.setVisible(bool(text))

    # running ---------------------------------------------------------------------------------------
    def _scope(self) -> Scope:
        if self.rb_sel.isChecked() and self._selection:
            return Scope.selection(*self._selection)
        if self.rb_sec.isChecked() and self._sections:
            return Scope.section(self._sections[self.sec_box.currentData() or 0])
        return Scope.whole()

    def start(self) -> None:
        instruction = self.instruction.toPlainText().strip()
        if not instruction or self._worker is not None:
            return
        self.show_error()
        scope = self._scope()
        text = scope_text(self._source, scope)
        if len(text) > MAX_EDIT_CHARS:
            self.show_error(f"That is too long for one AI edit ({len(text):,} characters, max {MAX_EDIT_CHARS:,}). "
                            "Pick a smaller section.")
            return
        key = self.ai.api_key()
        if not key:
            self.show_error("No Groq API key yet. Add one in Settings (AI) or via New file with AI.")
            return
        self.ai.push_history(instruction)
        self._start_version = self._version
        self._run_scope = scope
        self._run_source = self._source
        self.live.clear()
        self.run_label.setText("Editing file" if scope.kind == "whole" else "Editing selection")
        self.cat.set_theme(self.ai.theme_name())
        self.cat.set_animated(self.ai.animations())
        self.cat.start()
        self.stack.setCurrentIndex(PAGE_RUN)
        w = StreamWorker(key, system_prompt(scope.kind), user_prompt(instruction, text), self)
        w.text.connect(self._on_text)
        w.done.connect(self._on_done)
        w.stopped.connect(lambda _t: self._back_to_input())
        w.failed.connect(lambda m: (self._back_to_input(), self.show_error(m)))
        w.finished.connect(self._worker_finished)
        self._worker = w
        w.start()

    def _on_text(self, full: str) -> None:
        self.live.setPlainText(full)
        bar = self.live.verticalScrollBar()
        bar.setValue(bar.maximum())

    def _on_done(self, full: str) -> None:
        self.cat.stop()
        if not full.strip():
            self._back_to_input()
            self.show_error("The AI returned nothing. Try rephrasing the instruction.")
            return
        self._result = build_edited_document(self._run_source, self._run_scope, full)
        rows = with_context(diff_lines(self._run_source, self._result), 2)
        self.apply_btn.setEnabled(has_changes(rows))
        self._show_diff(rows)
        self.stack.setCurrentIndex(PAGE_REVIEW)

    def _back_to_input(self) -> None:
        self.cat.stop()
        self.stack.setCurrentIndex(PAGE_INPUT)

    def _worker_finished(self) -> None:
        w, self._worker = self._worker, None
        if w is not None:
            w.deleteLater()

    def stop(self) -> None:
        if self._worker is not None:
            self._worker.cancel()

    def cancel_worker(self) -> None:
        w = self._worker
        if w is not None:
            w.cancel()
            w.wait(3000)

    # diff ---------------------------------------------------------------------------------------------
    def _show_diff(self, rows: list) -> None:
        self.diff_view.clear()
        self._rows, self._shown = rows, 0
        if not has_changes(rows):
            self.diff_view.setPlainText("The AI made no changes.")
            return
        if self.ai.animations():
            self._reveal.start()          # rows slide in one after another, like a GitHub diff loading
        else:
            while self._shown < len(self._rows):
                self._append_row(self._rows[self._shown])
                self._shown += 1

    def _reveal_step(self) -> None:
        if self._shown >= len(self._rows):
            self._reveal.stop()
            return
        # the first rows animate; a long diff then fills in quickly
        batch = 1 if self._shown < 20 else 25
        for _ in range(batch):
            if self._shown < len(self._rows):
                self._append_row(self._rows[self._shown])
                self._shown += 1

    def _append_row(self, row) -> None:
        cur = QTextCursor(self.diff_view.document())
        cur.movePosition(QTextCursor.MoveOperation.End)
        block, char = QTextBlockFormat(), QTextCharFormat()
        theme = load_theme(self.ai.theme_name())
        colors = _DIFF["light" if _is_light(theme) else "dark"]
        if row.kind == "add":
            sign, (bg, fg) = "+ ", map(QColor, colors["add"])
        elif row.kind == "del":
            sign, (bg, fg) = "\u2212 ", map(QColor, colors["del"])
        else:
            sign, bg, fg = "  ", QColor(0, 0, 0, 0), QColor(theme["muted"])
        if row.kind in ("add", "del"):
            block.setBackground(bg)
            char.setForeground(fg)
        else:
            char.setForeground(fg)
        text = (f"⋯ {row.hidden} unchanged line{'s' if row.hidden != 1 else ''}" if row.kind == "gap"
                else sign + row.text)
        if self.diff_view.document().characterCount() > 1:
            cur.insertBlock(block, char)
        else:
            cur.setBlockFormat(block)
            cur.setBlockCharFormat(char)
        cur.insertText(text, char)

    # outcome ---------------------------------------------------------------------------------------------
    def apply(self) -> None:
        if self._result is None:
            return
        if self._version != self._start_version:
            self.show_error("The file changed on disk while the AI was working, so this result was not applied. "
                            "Run the edit again.")
            return
        result, self._result = self._result, None
        self.applied.emit(result)

    def close_panel(self) -> None:
        self.cancel_worker()
        self._reveal.stop()
        self.closed.emit()
