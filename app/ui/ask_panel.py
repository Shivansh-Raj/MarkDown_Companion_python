"""Ask AI about the open file: answers come only from the file, with quotes you can check."""
from __future__ import annotations

from PySide6.QtCore import QEvent, QObject, Qt, Signal
from PySide6.QtWidgets import QHBoxLayout, QLabel, QPlainTextEdit, QPushButton, QTextBrowser, QVBoxLayout, QWidget

import html

from ..ai import ask
from ..ai.services import AiServices
from ..ai.worker import StreamWorker
from ..markdown.renderer import render_markdown
from ..markdown.theme import load_theme
from . import scale
from .fonts import PIXEL, use_body_font
from .rich import style_browser

EXAMPLES = ("What is still left to do?", "Summarise this file in three bullets.", "What is due soonest?")


def conversation_html(turns: list[ask.Turn], theme_id: str, asked: str | None = None, live: str = "") -> str:
    """V1's ask view: a small pixel tag (YOU / FILE SAYS) before each part, the answer as Markdown, checked quotes in a bar."""
    t = load_theme(theme_id)

    def tag(text: str) -> str:
        return (f'<span style="background-color:{t["bg_alt"]}; color:{t["fg"]}; font-family:{PIXEL}; font-size:{scale.px(9)}px;">'
                f"&nbsp;{text}&nbsp;</span>")

    def question(q: str) -> str:
        return f'<p style="color:{t["muted"]}; margin-bottom:2px;">{tag("YOU")}&nbsp; {html.escape(q)}</p>'

    def answer(label: str, body_html: str) -> str:
        return f'<p style="margin-top:6px; margin-bottom:0;">{tag(label)}</p>{body_html}'

    out: list[str] = []
    for turn in turns:
        out.append(question(turn.question))
        if turn.error:
            out.append(answer("PROBLEM", f'<p style="color:#d9534f;">{html.escape(turn.answer)}</p>'))
            continue
        out.append(answer("FILE SAYS", render_markdown(turn.answer)))
        quotes = ask.visible_quotes(turn)
        if quotes:
            body = "<br>".join(f"<i>“{html.escape(q)}”</i>" for q in quotes)
            out.append(f'<p style="margin:2px 0 0 0; color:{t["muted"]}; font-family:{PIXEL}; font-size:{scale.px(9)}px;">Quotes from the file</p>'
                       f'<table width="100%" cellspacing="0" cellpadding="0"><tr><td width="3" bgcolor="{t["accent"]}"></td>'
                       f'<td style="padding-left:8px; color:{t["muted"]};">{body}</td></tr></table>')
        elif ask.needs_quote_warning(turn):
            out.append(f'<p style="color:{t["muted"]};"><i>No exact quote could be matched in the file, so double-check this answer.</i></p>')
        out.append('<hr style="margin:8px 0;">')
    if asked is not None:
        out.append(question(asked))
        out.append(answer("FILE SAYS", render_markdown(live) if live.strip() else f'<p style="color:{t["muted"]};"><i>Reading the file…</i></p>'))
    return "".join(out)


class _EnterToSend(QObject):
    """Enter sends the question, Shift+Enter adds a new line."""

    def __init__(self, panel: "AskPanel"):
        super().__init__(panel)
        self._panel = panel

    def eventFilter(self, obj, e) -> bool:  # noqa: N802
        if e.type() == QEvent.Type.KeyPress and e.key() in (Qt.Key.Key_Return, Qt.Key.Key_Enter) \
                and not (e.modifiers() & Qt.KeyboardModifier.ShiftModifier):
            self._panel.send()
            return True
        return False


class AskPanel(QWidget):
    closed = Signal()

    def __init__(self, ai: AiServices, parent=None):
        super().__init__(parent)
        self.ai = ai
        self.turns: list[ask.Turn] = []
        self._doc = ""
        self._truncated = False
        self._worker: StreamWorker | None = None
        self._asked: str | None = None
        self._live = ""

        self.head = QLabel("ASK ABOUT THIS FILE")
        self.head.setObjectName("section")
        self.clear_btn = QPushButton("Clear")
        self.clear_btn.clicked.connect(self.clear)
        self.close_btn = QPushButton("Close")
        self.close_btn.clicked.connect(self.close_panel)
        top = QHBoxLayout()
        top.addWidget(self.head, 1)
        top.addWidget(self.clear_btn)
        top.addWidget(self.close_btn)
        self.note = QLabel("Answers come only from this file. Quotes below each answer are checked against the file.")
        self.note.setObjectName("hint")
        self.note.setWordWrap(True)
        self.cut = QLabel("This file is very long, so only the first part was searched.")
        self.cut.setObjectName("status")
        self.cut.hide()
        self.error = QLabel()
        self.error.setObjectName("status")
        self.error.setWordWrap(True)
        self.error.hide()

        use_body_font(self, 12)                                  # same text look as the Markdown view (and follows the Text size setting)
        self.log = QTextBrowser()
        self.log.setObjectName("preview")
        self.log.setOpenLinks(False)
        style_browser(self.log, ai.theme_name())
        self.examples = QWidget()
        ex = QHBoxLayout(self.examples)
        ex.setContentsMargins(0, 0, 0, 0)
        self.example_btns = []
        for text in EXAMPLES:
            b = QPushButton(text)
            b.clicked.connect(lambda _c=False, t=text: self.ask(t))
            self.example_btns.append(b)
            ex.addWidget(b)
        ex.addStretch(1)

        self.input = QPlainTextEdit()
        self.input.setObjectName("editor")
        self.input.setPlaceholderText("Ask a question about this file…")
        self.input.setFixedHeight(56)
        self.input.installEventFilter(_EnterToSend(self))
        self.ask_btn = QPushButton("Ask")
        self.ask_btn.setObjectName("primary")
        self.ask_btn.clicked.connect(self.send)
        self.stop_btn = QPushButton("■  Stop")
        self.stop_btn.clicked.connect(self.stop)
        row = QHBoxLayout()
        row.addWidget(self.input, 1)
        col = QVBoxLayout()
        col.addWidget(self.ask_btn)
        col.addWidget(self.stop_btn)
        row.addLayout(col)

        lay = QVBoxLayout(self)
        lay.setContentsMargins(8, 6, 8, 6)
        lay.addLayout(top)
        lay.addWidget(self.note)
        lay.addWidget(self.cut)
        lay.addWidget(self.examples)
        lay.addWidget(self.log, 1)
        lay.addWidget(self.error)
        lay.addLayout(row)
        self._refresh()

    # session ---------------------------------------------------------------------------
    @property
    def busy(self) -> bool:
        return self._worker is not None

    def open_for(self, source: str) -> None:
        """Called each time the panel opens, with the text to search (the edit draft if editing, else the file)."""
        self._doc, self._truncated = ask.limit_doc(source)
        self.cut.setVisible(self._truncated)
        self.show_error()
        self._render()
        self._refresh()
        self.input.setFocus()

    def set_source(self, source: str) -> None:
        """The file changed on disk while the panel is open: later questions search the new text (the conversation stays)."""
        self._doc, self._truncated = ask.limit_doc(source)
        self.cut.setVisible(self._truncated)

    def show_error(self, text: str = "") -> None:
        self.error.setText(text)
        self.error.setVisible(bool(text))

    def clear(self) -> None:
        if not self.busy:
            self.turns = []
            self._render()
            self._refresh()

    def close_panel(self) -> None:
        self.cancel_worker()
        self.closed.emit()

    def cancel_worker(self) -> None:
        if self._worker is not None:
            self._worker.cancel()
            self._worker.wait(3000)

    # asking ----------------------------------------------------------------------------
    def send(self) -> None:
        self.ask(self.input.toPlainText())

    def ask(self, question: str) -> None:
        q = question.strip()
        if not q or self.busy:
            return
        self.show_error()
        key = self.ai.api_key()
        if not key:
            self.show_error("No Groq API key yet. Open New with AI from the home screen and set it with the Key button.")
            return
        self.input.clear()
        self._asked, self._live = q, ""
        w = StreamWorker(key, ask.build_system_prompt(), ask.build_user_prompt(q, [t for t in self.turns if not t.error], self._doc), self)
        w.text.connect(self._on_text)
        w.done.connect(self._on_done)
        w.stopped.connect(self._on_stopped)
        w.failed.connect(self._on_failed)
        w.finished.connect(self._finished)
        self._worker = w
        self._render()
        self._refresh()
        w.start()

    def _on_text(self, full: str) -> None:
        self._live = ask.answer_part(full)
        self._render()

    def _on_done(self, raw: str) -> None:
        answer, sources = ask.parse_answer(raw, self._doc)
        if not answer:
            self._on_failed("The AI returned nothing. Try rephrasing the question.")
            return
        self.turns.append(ask.Turn(self._asked or "", answer, sources))

    def _on_stopped(self, _partial: str) -> None:
        self.input.setPlainText(self._asked or "")            # stopped: put the question back so it can be edited

    def _on_failed(self, message: str) -> None:
        self.turns.append(ask.Turn(self._asked or "", message, error=True))

    def _finished(self) -> None:
        w, self._worker = self._worker, None
        if w is not None:
            w.deleteLater()
        self._asked = None
        self._render()
        self._refresh()
        self.input.setFocus()

    def stop(self) -> None:
        if self._worker is not None:
            self._worker.cancel()

    # view ------------------------------------------------------------------------------
    def refresh_text(self) -> None:
        """Text size changed: rebuild the conversation so the sizes inside it follow."""
        self._render()

    def _render(self) -> None:
        bar = self.log.verticalScrollBar()
        self.log.document().setDefaultStyleSheet(self.log.document().defaultStyleSheet())
        self.log.setHtml(conversation_html(self.turns, self.ai.theme_name(), self._asked, self._live))
        bar.setValue(bar.maximum())

    def _refresh(self) -> None:
        busy = self.busy
        self.examples.setVisible(not self.turns and not busy)
        self.clear_btn.setVisible(bool(self.turns) and not busy)
        self.ask_btn.setVisible(not busy)
        self.stop_btn.setVisible(busy)
        self.input.setReadOnly(busy)
