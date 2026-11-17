"""Create a new Markdown file from a prompt with Groq."""
from __future__ import annotations

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (QComboBox, QDialog, QHBoxLayout, QLabel, QLineEdit, QPlainTextEdit, QPushButton,
                               QStackedWidget, QTextBrowser, QVBoxLayout, QWidget)

from ..ai.create import (DOC_TYPE_LABELS, build_system_prompt, split_type_line, strip_fence, title_from_markdown,
                         write_new_file)
from ..ai.services import AiServices
from ..ai.worker import StreamWorker
from .fonts import body_font
from .rich import set_markdown, style_browser
from .typing_cat import TypingCat

PAGE_KEY, PAGE_PROMPT, PAGE_RUN = range(3)


class AiCreateWindow(QDialog):
    created = Signal(str)       # absolute path of the new file

    def __init__(self, ai: AiServices):
        super().__init__()
        self.ai = ai
        self._worker: StreamWorker | None = None
        self._raw = ""
        self.setWindowTitle("Markdown Companion V2 - New file with AI")
        self.setWindowFlag(Qt.WindowType.WindowStaysOnTopHint, True)
        self.setFont(body_font(12))                 # same text look as the Markdown view
        self.setMinimumSize(460, 520)

        self.stack = QStackedWidget()
        self.stack.addWidget(self._key_page())
        self.stack.addWidget(self._prompt_page())
        self.stack.addWidget(self._run_page())
        self.error = QLabel()
        self.error.setObjectName("status")
        self.error.setWordWrap(True)
        self.error.hide()
        root = QVBoxLayout(self)
        root.addWidget(self.stack, 1)
        root.addWidget(self.error)
        self.show_start()

    # pages -----------------------------------------------------------------------------
    def _key_page(self) -> QWidget:
        w = QWidget()
        lay = QVBoxLayout(w)
        title = QLabel("GROQ API KEY")
        title.setObjectName("section")
        note = QLabel("Paste a free key from console.groq.com. It is stored only on this computer "
                      "(OS keychain when available) and sent only to api.groq.com.")
        note.setWordWrap(True)
        note.setObjectName("hint")
        self.key_edit = QLineEdit()
        self.key_edit.setEchoMode(QLineEdit.EchoMode.Password)
        self.key_edit.setPlaceholderText("gsk_...")
        self.key_edit.returnPressed.connect(self._save_key)
        self.key_save = QPushButton("Save key")
        self.key_save.setObjectName("primary")
        self.key_save.clicked.connect(self._save_key)
        self.key_cancel = QPushButton("Cancel")
        self.key_cancel.clicked.connect(self._after_key_cancel)
        row = QHBoxLayout()
        row.addWidget(self.key_save)
        row.addWidget(self.key_cancel)
        row.addStretch(1)
        lay.addWidget(title)
        lay.addWidget(note)
        lay.addWidget(self.key_edit)
        lay.addLayout(row)
        lay.addStretch(1)
        return w

    def _prompt_page(self) -> QWidget:
        w = QWidget()
        lay = QVBoxLayout(w)
        title = QLabel("WHAT SHOULD I WRITE?")
        title.setObjectName("section")
        self.prompt = QPlainTextEdit()
        self.prompt.setPlaceholderText("e.g. Plan my product launch next week")
        self.prompt.setObjectName("editor")
        self.type_box = QComboBox()
        for tid, label in DOC_TYPE_LABELS.items():
            self.type_box.addItem(label, tid)
        self.gen_btn = QPushButton("✎  Generate")
        self.gen_btn.setObjectName("primary")
        self.gen_btn.clicked.connect(self.generate)
        key_btn = QPushButton("Key")
        key_btn.clicked.connect(lambda: self._show_key(editing=True))
        row = QHBoxLayout()
        row.addWidget(QLabel("Type"))
        row.addWidget(self.type_box)
        row.addStretch(1)
        row.addWidget(key_btn)
        row.addWidget(self.gen_btn)
        lay.addWidget(title)
        lay.addWidget(self.prompt, 1)
        lay.addLayout(row)
        return w

    def _run_page(self) -> QWidget:
        w = QWidget()
        lay = QVBoxLayout(w)
        top = QHBoxLayout()
        self.cat = TypingCat(self.ai.theme_name(), self.ai.animations())
        self.run_label = QLabel("Thinking")
        self.run_label.setObjectName("section")
        top.addWidget(self.cat)
        top.addWidget(self.run_label, 1)
        self.preview = QTextBrowser()
        self.preview.setObjectName("preview")
        self.preview.setOpenLinks(False)
        style_browser(self.preview, self.ai.theme_name())
        self.stop_btn = QPushButton("■  Stop")
        self.stop_btn.clicked.connect(self.stop)
        self.save_btn = QPushButton("Save && open")
        self.save_btn.setObjectName("primary")
        self.save_btn.clicked.connect(self.save)
        self.discard_btn = QPushButton("Discard")
        self.discard_btn.clicked.connect(self.show_start)
        row = QHBoxLayout()
        row.addWidget(self.stop_btn)
        row.addWidget(self.save_btn)
        row.addWidget(self.discard_btn)
        row.addStretch(1)
        lay.addLayout(top)
        lay.addWidget(self.preview, 1)
        lay.addLayout(row)
        return w

    # navigation ---------------------------------------------------------------------------
    def show_error(self, text: str = "") -> None:
        self.error.setText(text)
        self.error.setVisible(bool(text))

    def show_start(self) -> None:
        self._cancel_worker()
        self.show_error()
        self.cat.stop()
        if self.ai.api_key():
            self.stack.setCurrentIndex(PAGE_PROMPT)
            self.prompt.setFocus()
        else:
            self._show_key(editing=False)

    def _show_key(self, editing: bool) -> None:
        self._key_editing = editing
        self.key_cancel.setVisible(editing)
        self.key_edit.clear()
        self.stack.setCurrentIndex(PAGE_KEY)
        self.key_edit.setFocus()

    def _after_key_cancel(self) -> None:
        self.stack.setCurrentIndex(PAGE_PROMPT)

    def _save_key(self) -> None:
        value = self.key_edit.text().strip()
        if not value:
            return
        self.ai.set_api_key(value)
        self.key_edit.clear()
        self.show_error()
        self.stack.setCurrentIndex(PAGE_PROMPT)
        self.prompt.setFocus()

    # generation -----------------------------------------------------------------------------
    def _doc_type(self) -> str:
        return self.type_box.currentData()

    def generate(self) -> None:
        prompt = self.prompt.toPlainText().strip()
        key = self.ai.api_key()
        if not prompt or not key or self._worker is not None:
            return
        self.show_error()
        self._raw = ""
        self.preview.clear()
        self.run_label.setText("Thinking")
        self.stop_btn.show()
        self.save_btn.hide()
        self.discard_btn.hide()
        self.stack.setCurrentIndex(PAGE_RUN)
        self.cat.set_theme(self.ai.theme_name())
        self.cat.set_animated(self.ai.animations())
        self.cat.set_mood("typing")
        self.cat.show()
        self.cat.start()
        w = StreamWorker(key, build_system_prompt(self._doc_type()), prompt, self)
        w.text.connect(self._on_text)
        w.done.connect(self._on_done)
        w.stopped.connect(self._on_done)
        w.failed.connect(self._on_failed)
        w.finished.connect(self._worker_finished)
        self._worker = w
        w.start()

    def _split(self) -> tuple[str | None, str, bool]:
        if self._doc_type() == "auto":
            t, body, pending = split_type_line(self._raw)
        else:
            t, body, pending = self._doc_type(), self._raw, False
        return t, strip_fence(body), pending

    def _on_text(self, full: str) -> None:
        self._raw = full
        doc_type, body, pending = self._split()
        label = DOC_TYPE_LABELS.get(doc_type or "", "")
        self.run_label.setText("Thinking" if pending or not body else f"Writing {label.lower()}".strip())
        if body:
            bar = self.preview.verticalScrollBar()
            set_markdown(self.preview, body)
            bar.setValue(bar.maximum())

    def _on_done(self, full: str) -> None:
        self._raw = full or self._raw
        doc_type, body, _p = self._split()
        if not body.strip():
            self._on_failed("The AI returned nothing. Try rephrasing your prompt.")
            return
        label = DOC_TYPE_LABELS.get(doc_type or "", "")
        # the cat stays and asks whether to keep the draft, until the user decides
        self.cat.set_mood("waiting")
        self.cat.start()
        self.run_label.setText(f"Draft ready ({label.lower()}). Keep it?" if label else "Draft ready. Keep it?")
        set_markdown(self.preview, body)
        self.stop_btn.hide()
        self.save_btn.show()
        self.discard_btn.show()

    def _on_failed(self, message: str) -> None:
        self.cat.stop()
        self.stack.setCurrentIndex(PAGE_PROMPT)
        self.show_error(message)

    def _worker_finished(self) -> None:
        w, self._worker = self._worker, None
        if w is not None:
            w.deleteLater()

    def stop(self) -> None:
        if self._worker is not None:
            self._worker.cancel()

    def _cancel_worker(self) -> None:
        w = self._worker
        if w is not None:
            w.cancel()
            w.wait(3000)

    # save -------------------------------------------------------------------------------------
    def save(self) -> None:
        _t, body, _p = self._split()
        if not body.strip():
            return
        content = body if body.endswith("\n") else body + "\n"
        try:
            path = write_new_file(self.ai.save_dir(), title_from_markdown(body), content)
        except OSError as e:
            self.show_error(f"Could not save: {e}")
            return
        self.created.emit(str(path))
        self.close()

    def done(self, result: int) -> None:  # noqa: D401 - closing the dialog stops any running request
        self._cancel_worker()
        super().done(result)
