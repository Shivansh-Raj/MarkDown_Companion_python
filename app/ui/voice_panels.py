"""Panels used by the Voice window: meeting analyses and the Markdown export dialog."""
from __future__ import annotations

import os
import time
from pathlib import Path

from PySide6.QtCore import Qt, Signal
from PySide6.QtGui import QGuiApplication
from PySide6.QtWidgets import (QButtonGroup, QCheckBox, QComboBox, QDialog, QFileDialog, QHBoxLayout, QLabel, QLineEdit, QPlainTextEdit,
                               QPushButton, QRadioButton, QTabWidget, QTextBrowser, QVBoxLayout, QWidget)

from ..ai.worker import StreamWorker
from ..voice import analyses as an
from ..voice import export, session, templates as tpl
from ..voice.transcript import suggest_file_name


class ExportDialog(QDialog):
    """Preview and save voice outputs as Markdown: new file(s), or appended to an existing file without touching it."""
    file_created = Signal(str)

    def __init__(self, meta, sections: list[tpl.Section], doc_title: str, ai, db, sync, parent=None):
        super().__init__(parent)
        self.meta, self.sections, self.doc_title, self.ai, self.db, self.sync = meta, sections, doc_title, ai, db, sync
        self._folder: str | None = None                    # None = the default save folder
        self.setWindowTitle("Export as Markdown")
        self.setWindowFlag(Qt.WindowType.WindowStaysOnTopHint, True)
        self.setMinimumSize(520, 560)

        self.checks: dict[str, QCheckBox] = {}
        sec_row = QHBoxLayout()
        for s in sections:
            c = QCheckBox(s.label)
            c.setChecked(True)
            c.toggled.connect(self._update)
            self.checks[s.id] = c
            sec_row.addWidget(c)
        sec_row.addStretch(1)

        self.new_radio = QRadioButton("New file")
        self.append_radio = QRadioButton("Add to an existing file")
        self.new_radio.setChecked(True)
        self.combined_radio = QRadioButton("One document")
        self.separate_radio = QRadioButton("One file per section")
        self.combined_radio.setChecked(True)
        self._dest_group, self._layout_group = QButtonGroup(self), QButtonGroup(self)   # two independent choices
        for r in (self.new_radio, self.append_radio):
            self._dest_group.addButton(r)
        for r in (self.combined_radio, self.separate_radio):
            self._layout_group.addButton(r)
        for r in (self.new_radio, self.append_radio, self.combined_radio, self.separate_radio):
            r.toggled.connect(self._update)
        dest_row = QHBoxLayout()
        for r in (self.new_radio, self.append_radio):
            dest_row.addWidget(r)
        dest_row.addStretch(1)
        layout_row = QHBoxLayout()
        for r in (self.combined_radio, self.separate_radio):
            layout_row.addWidget(r)
        layout_row.addStretch(1)

        self.name_edit = QLineEdit(suggest_file_name(meta.title, meta.created_at_ms, doc_title))
        self.folder_label = QLabel()
        self.folder_label.setObjectName("hint")
        self.folder_btn = QPushButton("Choose folder…")
        self.folder_btn.clicked.connect(self._pick_folder)
        self.folder_default_btn = QPushButton("Default")
        self.folder_default_btn.clicked.connect(self._default_folder)
        self.new_box = QWidget()
        nb = QHBoxLayout(self.new_box)
        nb.setContentsMargins(0, 0, 0, 0)
        for wdg in (self.name_edit, self.folder_btn, self.folder_default_btn):
            nb.addWidget(wdg)

        self.target_box = QComboBox()
        for row in db.recent_files(500):
            if os.path.exists(row["path"]):
                self.target_box.addItem(os.path.basename(row["path"]), row["path"])
        self.target_hint = QLabel("Added at the end; nothing already in the file is changed.")
        self.target_hint.setObjectName("hint")
        self.append_box = QWidget()
        ab = QHBoxLayout(self.append_box)
        ab.setContentsMargins(0, 0, 0, 0)
        ab.addWidget(self.target_box, 1)
        ab.addWidget(self.target_hint)

        self.preview = QPlainTextEdit()
        self.preview.setReadOnly(True)
        self.preview.setObjectName("editor")
        self.message = QLabel()
        self.message.setObjectName("status")
        self.message.setWordWrap(True)
        self.message.hide()
        self.save_btn = QPushButton("Save && open")
        self.save_btn.setObjectName("primary")
        self.save_btn.clicked.connect(self.save)
        close_btn = QPushButton("Close")
        close_btn.clicked.connect(self.close)
        btns = QHBoxLayout()
        btns.addWidget(self.save_btn)
        btns.addWidget(close_btn)
        btns.addStretch(1)

        lay = QVBoxLayout(self)
        title = QLabel("EXPORT AS MARKDOWN")
        title.setObjectName("section")
        lay.addWidget(title)
        lay.addLayout(sec_row)
        lay.addLayout(dest_row)
        lay.addLayout(layout_row)
        lay.addWidget(self.new_box)
        lay.addWidget(self.folder_label)
        lay.addWidget(self.append_box)
        lay.addWidget(self.preview, 1)
        lay.addWidget(self.message)
        lay.addLayout(btns)
        self._update()

    # state -------------------------------------------------------------------
    def chosen(self) -> list[tpl.Section]:
        return [s for s in self.sections if self.checks[s.id].isChecked()]

    def directory(self) -> Path:
        return Path(self._folder) if self._folder else self.ai.save_dir()

    def preview_text(self) -> str:
        chosen = self.chosen()
        if not chosen:
            return ""
        if self.append_radio.isChecked():
            return tpl.append_from_sections(self.meta, chosen)
        if self.combined_radio.isChecked():
            return tpl.combined_from_sections(self.meta, self.doc_title, chosen)
        return "\n\n".join(f"<!-- {suggest_file_name(self.meta.title, self.meta.created_at_ms, s.suffix)}.md -->\n"
                           f"{tpl.standalone_from_section(self.meta, s)}" for s in chosen)

    def _update(self, *_a) -> None:
        appending = self.append_radio.isChecked()
        self.new_box.setVisible(not appending)
        self.folder_label.setVisible(not appending)
        self.append_box.setVisible(appending)
        self.combined_radio.setVisible(not appending)
        self.separate_radio.setVisible(not appending)
        self.name_edit.setVisible(self.combined_radio.isChecked())
        self.folder_label.setText(f"Folder: {self.directory()}")
        self.folder_default_btn.setVisible(self._folder is not None)
        self.preview.setPlainText(self.preview_text())
        self.save_btn.setText("Add to file" if appending else "Save && open")
        self.save_btn.setEnabled(bool(self.chosen()) and (not appending or self.target_box.count() > 0))

    def _pick_folder(self) -> None:
        d = QFileDialog.getExistingDirectory(self, "Choose a folder", str(self.directory()))
        if d:
            self._folder = d
            self._update()

    def _default_folder(self) -> None:
        self._folder = None
        self._update()

    # save --------------------------------------------------------------------
    def show_message(self, text: str, kind: str = "error") -> None:
        self.message.setText(text)
        self.message.setProperty("kind", kind)
        self.message.style().unpolish(self.message)
        self.message.style().polish(self.message)
        self.message.setVisible(bool(text))

    def save(self) -> None:
        chosen = self.chosen()
        if not chosen:
            return
        try:
            if self.append_radio.isChecked():
                path = self.target_box.currentData()
                if not path:
                    raise RuntimeError("Choose a file to add to.")
                export.append_to_file(self.sync, path, self.meta, chosen)
                paths = [Path(path)]
                self.show_message(f"Added to {paths[0].name}.", "info")
            else:
                paths = export.write_new(self.directory(), self.meta, self.doc_title, chosen, self.name_edit.text().strip(),
                                         self.separate_radio.isChecked())
                self.show_message("Saved " + ", ".join(p.name for p in paths) + ".", "info")
        except (RuntimeError, OSError) as e:
            self.show_message(str(e))
            return
        for p in paths:
            self.file_created.emit(str(p))


class AnalysisPanel(QWidget):
    """Meeting analyses (summary, takeaways, action items) with Markdown export. Regenerable from the transcript."""
    file_created = Signal(str)

    def __init__(self, svc, ai, db, sync, parent=None):
        super().__init__(parent)
        self.svc, self.ai, self.db, self.sync = svc, ai, db, sync
        self.results: dict[str, an.AnalysisResult] = {}
        self._session_id: str | None = None
        self._todo: list[str] = []
        self._worker: StreamWorker | None = None
        self._current: str | None = None
        self._truncated = False
        self._dialog: ExportDialog | None = None

        head = QLabel("ANALYSE THIS MEETING")
        head.setObjectName("section")
        self.checks = {t: QCheckBox(an.ANALYSIS_LABELS[t]) for t in an.ANALYSIS_TYPES}
        for c in self.checks.values():
            c.setChecked(True)
        self.generate_btn = QPushButton("Generate")
        self.generate_btn.setObjectName("primary")
        self.generate_btn.clicked.connect(self.generate)
        self.stop_btn = QPushButton("■  Stop")
        self.stop_btn.clicked.connect(self.stop)
        self.export_btn = QPushButton("Export as Markdown…")
        self.export_btn.clicked.connect(self.export)
        row = QHBoxLayout()
        for c in self.checks.values():
            row.addWidget(c)
        row.addStretch(1)
        row2 = QHBoxLayout()
        for b in (self.generate_btn, self.stop_btn, self.export_btn):
            row2.addWidget(b)
        row2.addStretch(1)
        self.hint = QLabel()
        self.hint.setObjectName("hint")
        self.hint.setWordWrap(True)
        self.error = QLabel()
        self.error.setObjectName("status")
        self.error.setWordWrap(True)
        self.error.hide()

        self.views: dict[str, QTextBrowser] = {}
        self.tabs = QTabWidget()
        for t in an.ANALYSIS_TYPES:
            tab = QWidget()
            tl = QVBoxLayout(tab)
            view = QTextBrowser()
            view.setObjectName("preview")
            view.setOpenLinks(False)
            copy = QPushButton("Copy")
            copy.clicked.connect(lambda _c=False, k=t: self._copy(k))
            regen = QPushButton("Regenerate")
            regen.clicked.connect(lambda _c=False, k=t: self._run([k]))
            r = QHBoxLayout()
            r.addWidget(copy)
            r.addWidget(regen)
            r.addStretch(1)
            tl.addWidget(view, 1)
            tl.addLayout(r)
            self.views[t] = view
            setattr(self, f"{t}_copy", copy)
            setattr(self, f"{t}_regen", regen)
            self.tabs.addTab(tab, an.ANALYSIS_LABELS[t])

        lay = QVBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        for item in (head,):
            lay.addWidget(item)
        lay.addLayout(row)
        lay.addLayout(row2)
        lay.addWidget(self.hint)
        lay.addWidget(self.error)
        lay.addWidget(self.tabs, 1)
        self.setMinimumHeight(230)
        self.refresh()

    # session -------------------------------------------------------------------
    def set_session(self, meta) -> None:
        """Load earlier analyses of this session so nothing needs to be generated twice."""
        if meta is None or meta.id == self._session_id:
            return
        self.stop()
        self._session_id = meta.id
        loaded = []
        for d in session.list_analyses(self.svc.root, meta.id):
            try:
                loaded.append(an.AnalysisResult.from_json(d))
            except (KeyError, TypeError):
                continue                                   # a damaged or foreign file
        self.results = an.latest_by_type(loaded)
        for t in an.ANALYSIS_TYPES:
            self._show(t)
        self.show_error()
        self.refresh()

    def _show(self, kind: str) -> None:
        r = self.results.get(kind)
        self.views[kind].setMarkdown(r.markdown if r else "")
        self.tabs.setTabText(an.ANALYSIS_TYPES.index(kind), an.ANALYSIS_LABELS[kind] + (" ✓" if r else ""))

    def show_error(self, text: str = "") -> None:
        self.error.setText(text)
        self.error.setVisible(bool(text))

    @property
    def ready(self) -> bool:
        return bool(an.timed_transcript(self.svc.chunks)) and self.svc.pending == 0

    def refresh(self) -> None:
        running = self._worker is not None or bool(self._todo)
        self.generate_btn.setEnabled(self.ready and not running)
        self.generate_btn.setVisible(not running)
        self.stop_btn.setVisible(running)
        self.export_btn.setEnabled(bool(self.results) and not running)
        self.hint.setText("" if self.ready else "Wait for the transcript to finish before analysing.")
        for t in an.ANALYSIS_TYPES:
            has = t in self.results
            getattr(self, f"{t}_copy").setEnabled(has and not running)
            getattr(self, f"{t}_regen").setEnabled(self.ready and not running)

    # generation ----------------------------------------------------------------------
    def generate(self) -> None:
        self._run([t for t in an.ANALYSIS_TYPES if self.checks[t].isChecked()])

    def _run(self, kinds: list[str]) -> None:
        if not kinds or self._worker is not None or not self.ready:
            return
        text, self._truncated = an.limit_transcript(an.timed_transcript(self.svc.chunks))
        self._text = text
        self._todo = list(kinds)
        self.show_error()
        self._next()

    def _next(self) -> None:
        if not self._todo:
            self._current = None
            self.refresh()
            return
        kind = self._todo.pop(0)
        key = self.ai.api_key()
        if not key:
            self._todo.clear()
            self.show_error("No Groq API key. Add one in Settings or under New with AI.")
            self.refresh()
            return
        self._current = kind
        self.tabs.setCurrentIndex(an.ANALYSIS_TYPES.index(kind))
        self.views[kind].setPlainText("Working…")
        w = StreamWorker(key, an.build_system_prompt(kind), an.build_user_prompt(self.svc.meta.title, self._text), self)
        w.text.connect(lambda full, k=kind: self.views[k].setPlainText(full) if kind == self._current else None)
        w.done.connect(self._on_done)
        w.stopped.connect(self._on_stopped)
        w.failed.connect(self._on_failed)
        w.finished.connect(self._worker_finished)
        self._worker = w
        self.refresh()
        w.start()

    def _on_done(self, raw: str) -> None:
        kind = self._current
        try:
            result = an.to_result(kind, raw, self._truncated, int(time.time() * 1000))
            if self.svc.meta is not None:
                session.save_analysis(self.svc.root, self.svc.meta.id, kind, result.to_json(), result.created_at_ms)
            self.results[kind] = result
        except ValueError as e:
            self.show_error(f"{an.ANALYSIS_LABELS[kind]}: {e}")
        except OSError as e:
            self.results[kind] = result
            self.show_error(f"The result could not be saved to the session ({e}).")
        self._show(kind)

    def _on_stopped(self, _partial: str) -> None:
        self._todo.clear()
        self._show(self._current)

    def _on_failed(self, message: str) -> None:
        self._todo.clear()
        self.show_error(message)
        self._show(self._current)

    def _worker_finished(self) -> None:
        w, self._worker = self._worker, None
        if w is not None:
            w.deleteLater()
        self._next()

    def stop(self) -> None:
        self._todo.clear()
        if self._worker is not None:
            self._worker.cancel()
            self._worker.wait(3000)

    def _copy(self, kind: str) -> None:
        r = self.results.get(kind)
        if r:
            QGuiApplication.clipboard().setText(r.markdown)

    def export(self) -> None:
        if not self.results or self.svc.meta is None:
            return
        self._dialog = ExportDialog(self.svc.meta, tpl.meeting_sections(list(self.results.values())), "meeting notes",
                                    self.ai, self.db, self.sync, self)
        self._dialog.file_created.connect(self.file_created)
        self._dialog.show()
