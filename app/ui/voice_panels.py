"""Panels used by the Voice window: meeting analyses and the Markdown export dialog."""
from __future__ import annotations

import os
import re
import time
from pathlib import Path

from PySide6.QtCore import QRectF, Qt, QTimer, Signal
from PySide6.QtGui import QColor, QGuiApplication, QPainter
from PySide6.QtWidgets import (QButtonGroup, QCheckBox, QComboBox, QDialog, QFileDialog, QHBoxLayout, QLabel, QLineEdit, QPlainTextEdit,
                               QPushButton, QRadioButton, QScrollArea, QTabWidget, QTextBrowser, QVBoxLayout, QWidget)

from ..ai.worker import StreamWorker
from ..markdown.theme import load_theme
from ..voice import analyses as an
from ..voice import analytics as ana
from ..voice import coach as co
from ..voice import followups as fu
from ..voice import export, session, templates as tpl
from ..services.synchronization import read_snapshot
from .flow_layout import FlowLayout
from ..voice.transcript import date_stamp, format_clock, suggest_file_name, transcript_text, word_count


def _compact_tabs(tabs: QTabWidget) -> None:
    """Tabs that shrink (eliding their titles) instead of forcing the window wide."""
    tabs.tabBar().setElideMode(Qt.TextElideMode.ElideRight)
    tabs.tabBar().setUsesScrollButtons(False)
    tabs.tabBar().setExpanding(False)


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
        row = FlowLayout(None, 0, 10, 4)
        for c in self.checks.values():
            row.addWidget(c)
        row2 = FlowLayout(None, 0, 6, 4)
        for b in (self.generate_btn, self.stop_btn, self.export_btn):
            row2.addWidget(b)
        self.hint = QLabel()
        self.hint.setObjectName("hint")
        self.hint.setWordWrap(True)
        self.error = QLabel()
        self.error.setObjectName("status")
        self.error.setWordWrap(True)
        self.error.hide()

        self.views: dict[str, QTextBrowser] = {}
        self.tabs = QTabWidget()
        _compact_tabs(self.tabs)
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
        self.followups = FollowUpPanel(svc, ai, db, sync)             # earlier meetings' action items vs. this meeting
        self.followups.file_created.connect(self.file_created)
        self.tabs.addTab(self.followups, "Follow-ups")

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
        self.followups.set_session(meta)
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
        self.followups.refresh()
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
        self.followups.stop()
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


class PaceBars(QWidget):
    """Words per minute for each half-minute; bars that are noticeably faster or slower than average are highlighted."""

    def __init__(self, theme: str):
        super().__init__()
        self._t = load_theme(theme)
        self._pace: ana.Pace | None = None
        self.setMinimumHeight(54)

    def set_pace(self, pace: "ana.Pace | None") -> None:
        self._pace = pace
        self.setVisible(pace is not None)
        self.setToolTip("" if pace is None else "; ".join(f"{format_clock(w.start_ms)}: {w.wpm} wpm" for w in pace.windows))
        self.update()

    def paintEvent(self, _e) -> None:  # noqa: N802
        p = QPainter(self)
        p.fillRect(self.rect(), QColor(self._t["code_bg"]))
        if self._pace:
            flagged = {w.start_ms for w, _k in self._pace.flagged}
            n = len(self._pace.windows)
            top = max(w.wpm for w in self._pace.windows) or 1
            bw = max(4.0, min(28.0, (self.width() - 8) / n - 3))
            for i, w in enumerate(self._pace.windows):
                h = max(6.0, (self.height() - 8) * w.wpm / top)
                color = self._t["accent"] if w.start_ms in flagged else self._t["muted"]
                p.fillRect(QRectF(4 + i * (bw + 3), self.height() - 4 - h, bw, h), QColor(color))
        p.end()


class PracticePanel(QWidget):
    """Practice mode: objective speaking numbers (computed locally) plus AI coaching grounded in what was said."""
    file_created = Signal(str)

    def __init__(self, svc, ai, db, sync, parent=None):
        super().__init__(parent)
        self.svc, self.ai, self.db, self.sync = svc, ai, db, sync
        self.analytics: ana.Analytics | None = None
        self.results: dict[str, co.CoachResult] = {}
        self._styles: dict[int, str] = {}              # style shown on each sentence card
        self._picked: dict[int, str] = {}              # improvements chosen for export (index -> style)
        self._session_id: str | None = None
        self._todo: list[str] = []
        self._worker: StreamWorker | None = None
        self._current: str | None = None
        self._plain = ""
        self._truncated = False
        self._dialog: ExportDialog | None = None
        theme = ai.theme_name()
        self._t = load_theme(theme)

        def section(text: str) -> QLabel:
            lbl = QLabel(text)
            lbl.setObjectName("section")
            return lbl

        # ---- speaking analysis
        self.tiles: dict[str, QLabel] = {}
        tiles = QHBoxLayout()
        for key, caption in (("wpm", "words / min overall"), ("articulation", "while speaking"), ("fillers", "filler words"), ("pauses", "pauses ≥ 1.5 s")):
            box = QVBoxLayout()
            value = QLabel("–")
            value.setStyleSheet(f"font-size: 20px; font-weight: bold; color: {self._t['accent']};")
            cap = QLabel(caption)
            cap.setObjectName("hint")
            box.addWidget(value)
            box.addWidget(cap)
            self.tiles[key] = value
            tiles.addLayout(box)
        self.bars = PaceBars(theme)
        self.bars_note = QLabel()
        self.bars_note.setObjectName("hint")
        self.bars_note.setWordWrap(True)
        self.lists = QLabel()
        self.lists.setWordWrap(True)
        self.lists.setTextFormat(Qt.TextFormat.RichText)
        self.notes = QLabel()
        self.notes.setWordWrap(True)
        self.notes.setTextFormat(Qt.TextFormat.RichText)
        for lbl in (self.lists, self.notes):               # rich-text labels do not inherit the themed text colour
            lbl.setStyleSheet(f"color: {self._t['fg']};")
        self.caveat = QLabel(ana.DELIVERY_CAVEAT)
        self.caveat.setObjectName("hint")
        self.caveat.setWordWrap(True)
        self.hint = QLabel()
        self.hint.setObjectName("hint")
        self.hint.setWordWrap(True)

        # ---- coach
        self.checks = {t: QCheckBox(co.COACH_LABELS[t]) for t in co.COACH_TYPES}
        for c in self.checks.values():
            c.setChecked(True)
        self.generate_btn = QPushButton("Get feedback")
        self.generate_btn.setObjectName("primary")
        self.generate_btn.clicked.connect(self.generate)
        self.stop_btn = QPushButton("■  Stop")
        self.stop_btn.clicked.connect(self.stop)
        self.export_btn = QPushButton("Export as Markdown…")
        self.export_btn.clicked.connect(self.export)
        self.error = QLabel()
        self.error.setObjectName("status")
        self.error.setWordWrap(True)
        self.error.hide()
        self.tabs = QTabWidget()
        _compact_tabs(self.tabs)
        self.sentence_box = QVBoxLayout()
        holder = QWidget()
        holder.setLayout(self.sentence_box)
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setObjectName("aiscroll")
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        scroll.setWidget(holder)
        self.sentence_scroll = scroll
        self.views: dict[str, QTextBrowser] = {}
        self.tabs.addTab(scroll, co.COACH_LABELS["sentences"])
        for k in ("clarity", "structure", "vocabulary"):
            v = QTextBrowser()
            v.setObjectName("preview")
            v.setOpenLinks(False)
            self.views[k] = v
            self.tabs.addTab(v, co.COACH_LABELS[k])
        self.tabs.setMinimumHeight(220)

        lay = QVBoxLayout(self)
        lay.addWidget(section("SPEAKING ANALYSIS"))
        lay.addLayout(tiles)
        lay.addWidget(self.bars)
        lay.addWidget(self.bars_note)
        lay.addWidget(self.lists)
        lay.addWidget(self.notes)
        lay.addWidget(self.caveat)
        lay.addWidget(self.hint)
        lay.addWidget(section("COACH"))
        row = FlowLayout(None, 0, 10, 4)
        for c in self.checks.values():
            row.addWidget(c)
        row2 = FlowLayout(None, 0, 6, 4)
        for b in (self.generate_btn, self.stop_btn, self.export_btn):
            row2.addWidget(b)
        lay.addLayout(row)
        lay.addLayout(row2)
        lay.addWidget(self.error)
        lay.addWidget(self.tabs, 1)
        self.refresh()

    # session -----------------------------------------------------------------------
    @property
    def ready(self) -> bool:
        return bool(self.svc.chunks) and self.svc.pending == 0 and bool(an.timed_transcript(self.svc.chunks))

    def set_session(self, meta) -> None:
        if meta is None or meta.id == self._session_id:
            return
        self.stop()
        self._session_id = meta.id
        latest: dict[str, co.CoachResult] = {}
        for d in session.list_analyses(self.svc.root, meta.id):
            if d.get("type") not in co.COACH_TYPES:
                continue
            try:
                r = co.CoachResult.from_json(d)
            except (KeyError, TypeError):
                continue                                # a damaged or foreign file
            if r.type not in latest or r.created_at_ms >= latest[r.type].created_at_ms:
                latest[r.type] = r
        self.results, self._styles, self._picked = latest, {}, {}
        self.show_error()
        self._render_results()
        self.refresh()

    def show_error(self, text: str = "") -> None:
        self.error.setText(text)
        self.error.setVisible(bool(text))

    # speaking analysis -----------------------------------------------------------------
    def refresh(self) -> None:
        meta = self.svc.meta
        if meta is not None and self.ready:
            a = ana.compute_analytics(self.svc.chunks, meta.duration_ms)
        else:
            a = None
        self.analytics = a
        running = self._worker is not None or bool(self._todo)
        if a is None or a.word_count == 0:
            for lbl in self.tiles.values():
                lbl.setText("–")
            self.bars.set_pace(None)
            self.bars_note.setText("")
            self.lists.setText("")
            self.notes.setText("")
            self.hint.setText("Wait for the transcript to finish." if not self.ready else "No speech to analyse.")
        else:
            self.tiles["wpm"].setText(str(a.wpm if a.wpm is not None else "–"))
            self.tiles["articulation"].setText(str(a.articulation_wpm if a.articulation_wpm is not None else "–"))
            self.tiles["fillers"].setText(f"{a.filler_total}")
            self.tiles["pauses"].setText(str(a.pauses.long) if a.pauses else "–")
            self.bars.set_pace(a.pace)
            self.bars_note.setText(("Pace per half-minute. Highlighted bars are noticeably faster or slower than your average. " + ana.PACE_REFERENCE)
                                   if a.pace else "")
            self.lists.setText(self._lists_html(a))
            self.notes.setText("".join(f"• {n}<br>" for n in a.notes))
            self.hint.setText("")
        self.generate_btn.setEnabled(a is not None and a.word_count > 0 and not running)
        self.generate_btn.setVisible(not running)
        self.stop_btn.setVisible(running)
        self.export_btn.setEnabled(a is not None and a.word_count > 0 and not running)

    def _lists_html(self, a: "ana.Analytics") -> str:
        def ul(title: str, items: list[str], empty: str) -> str:
            body = "".join(f"<li>{x}</li>" for x in items) or f"<li>{empty}</li>"
            return f"<b>{title}</b><ul style='margin-top:2px'>{body}</ul>"
        fillers = [f"“{f.term}” × {f.count}" for f in a.fillers]
        rep = ([f"Word “{w.term}” × {w.count}" for w in a.repeated_words] + [f"Phrase “{p.term}” × {p.count}" for p in a.repeated_phrases]
               + [f"“{p}”" + (f" at {format_clock(at)}" if at is not None else "") for p, at in a.stutters[:3]])
        pauses = ([f"{format_clock(p.start_ms)} · {p.duration_ms / 1000:.1f} s" for p in a.pauses.longest[:4]] if a.pauses else [])
        return (ul("FILLERS", fillers, "None found") + "<span style='font-size:11px'>“like” and “actually” are sometimes used on purpose.</span><br>"
                + ul("REPETITION", rep, "Nothing notable") + ul("PAUSES", pauses, "No long pauses" if a.has_timings else "No timing data"))

    # coach -------------------------------------------------------------------------------
    def generate(self) -> None:
        self._run([t for t in co.COACH_TYPES if self.checks[t].isChecked()])

    def _run(self, kinds: list[str]) -> None:
        if not kinds or self._worker is not None or not self.ready:
            return
        text, self._truncated = an.limit_transcript(an.timed_transcript(self.svc.chunks))
        self._plain = re.sub(r"(?m)^\[\d+(?::\d+)+\]\s*", "", text)         # quotes are checked against the spoken words only
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
        w = StreamWorker(key, co.build_system_prompt(kind), co.build_user_prompt(self.svc.meta.title, self._plain), self)
        w.done.connect(self._on_done)
        w.stopped.connect(lambda _p: self._todo.clear())
        w.failed.connect(self._on_failed)
        w.finished.connect(self._worker_finished)
        self._worker = w
        self.refresh()
        w.start()

    def _on_done(self, raw: str) -> None:
        kind = self._current
        try:
            result = co.to_result(kind, raw, self._plain, self._truncated, int(time.time() * 1000))
            self.results[kind] = result
            if self.svc.meta is not None:
                session.save_analysis(self.svc.root, self.svc.meta.id, kind, result.to_json(), result.created_at_ms)
            if kind == "sentences":
                self._styles, self._picked = {}, {}
        except ValueError as e:
            self.show_error(f"{co.COACH_LABELS[kind]}: {e}")
        except OSError as e:
            self.show_error(f"The result could not be saved to the session ({e}).")
        self._render_results()

    def _on_failed(self, message: str) -> None:
        self._todo.clear()
        self.show_error(message)

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

    # results ------------------------------------------------------------------------------
    def _render_results(self) -> None:
        for kind, view in self.views.items():
            r = self.results.get(kind)
            view.setMarkdown(co.coach_markdown(r) if r else "")
            self.tabs.setTabText(co.COACH_TYPES.index(kind), co.COACH_LABELS[kind] + (" ✓" if r else ""))
        self.tabs.setTabText(0, co.COACH_LABELS["sentences"] + (" ✓" if "sentences" in self.results else ""))
        while self.sentence_box.count():
            item = self.sentence_box.takeAt(0)
            if item.widget():
                item.widget().deleteLater()
        r = self.results.get("sentences")
        fixes = r.sentences if r and r.sentences is not None else []
        if r and not fixes:
            self.sentence_box.addWidget(QLabel("No sentences needed improving."))
        for i, f in enumerate(fixes):
            self.sentence_box.addWidget(self._card(i, f))
        if r and r.dropped:
            note = QLabel(f"{r.dropped} suggestion(s) were left out because they did not match what you said.")
            note.setObjectName("hint")
            self.sentence_box.addWidget(note)
        self.sentence_box.addStretch(1)

    def _card(self, i: int, f: "co.SentenceFix") -> QWidget:
        style = self._styles.get(i, "natural")
        card = QWidget()
        lay = QVBoxLayout(card)
        said = QLabel(f"<b>YOU SAID</b>  “{f.original}”")
        said.setWordWrap(True)
        better = QLabel(f"<b>BETTER</b>  {getattr(f, style)}")
        better.setWordWrap(True)
        for lbl in (said, better):                         # rich-text labels do not inherit the themed text colour
            lbl.setStyleSheet(f"color: {self._t['fg']};")
        row = QHBoxLayout()
        for s in co.STYLES:
            b = QPushButton(co.STYLE_LABELS[s])
            b.setCheckable(True)
            b.setChecked(s == style)
            b.clicked.connect(lambda _c=False, idx=i, st=s: self._set_style(idx, st))
            row.addWidget(b)
        row.addStretch(1)
        why = QLabel(f"<b>Why:</b> {f.why}" if f.why else "")
        why.setWordWrap(True)
        why.setObjectName("hint")
        include = QCheckBox("Include in Markdown")
        include.setChecked(i in self._picked)
        include.toggled.connect(lambda on, idx=i, st=style: self._pick(idx, st, on))
        copy = QPushButton("Copy")
        copy.clicked.connect(lambda _c=False, fix=f, st=style: QGuiApplication.clipboard().setText(co.sentence_block(fix, st)))
        row2 = QHBoxLayout()
        row2.addWidget(include)
        row2.addWidget(copy)
        row2.addStretch(1)
        for item in (said, ):
            lay.addWidget(item)
        lay.addLayout(row)
        lay.addWidget(better)
        lay.addWidget(why)
        lay.addLayout(row2)
        card.setStyleSheet(f"QWidget#card {{ border: 2px solid {self._t['border']}; }}")
        card.setObjectName("card")
        return card

    def _set_style(self, i: int, style: str) -> None:
        self._styles[i] = style
        if i in self._picked:
            self._picked[i] = style                      # an included improvement follows the style being shown
        self._render_results()

    def _pick(self, i: int, style: str, on: bool) -> None:
        if on:
            self._picked[i] = self._styles.get(i, style)
        else:
            self._picked.pop(i, None)

    # export ---------------------------------------------------------------------------------
    def sections(self) -> list[tpl.Section]:
        out = [tpl.Section("speaking", "Speaking analysis", "speaking analysis", co.speaking_analysis_markdown(self.analytics))] if self.analytics else []
        s = self.results.get("sentences")
        if s and s.sentences:
            md = co.selected_sentences_markdown(s.sentences, sorted(self._picked.items())) if self._picked else co.coach_markdown(s)
            out.append(tpl.Section("sentences", "Sentence improvements", "sentence improvements", md))
        for k in ("clarity", "structure", "vocabulary"):
            r = self.results.get(k)
            if r:
                out.append(tpl.Section(k, co.COACH_LABELS[k], co.COACH_LABELS[k].lower(), co.coach_markdown(r)))
        return out

    def export(self) -> None:
        if self.analytics is None or self.svc.meta is None:
            return
        self._dialog = ExportDialog(self.svc.meta, self.sections(), "presentation feedback", self.ai, self.db, self.sync, self)
        self._dialog.file_created.connect(self.file_created)
        self._dialog.show()


class FollowUpPanel(QWidget):
    """Compares earlier meetings' action items with this meeting. Checkbox state comes from the user's real Markdown files;
    nothing is ticked or edited, and a repeated mention never counts as "unfinished" by itself."""
    file_created = Signal(str)
    MAX_MEETINGS, MAX_ITEMS, MAX_FILES = 8, 25, 80

    def __init__(self, svc, ai, db, sync, parent=None):
        super().__init__(parent)
        self.svc, self.ai, self.db, self.sync = svc, ai, db, sync
        self.report: fu.FollowUpReport | None = None
        self._session_id: str | None = None
        self._worker: StreamWorker | None = None
        self._steps: list[str] = []
        self._step: str | None = None
        self._busy = False
        self._dialog: ExportDialog | None = None

        note = QLabel("Compares earlier action items with this meeting. It reads your Markdown files for each item's real checkbox. "
                      "Nothing is ticked or changed.")
        note.setObjectName("hint")
        note.setWordWrap(True)
        self.check_btn = QPushButton("Check previous meetings")
        self.check_btn.clicked.connect(lambda: self.run(False))
        self.rethink_btn = QPushButton("↻ Rethink")
        self.rethink_btn.setToolTip("Not satisfied? Ask the AI to reconsider more strictly")
        self.rethink_btn.clicked.connect(lambda: self.run(True))
        self.stop_btn = QPushButton("■  Stop")
        self.stop_btn.clicked.connect(self.stop)
        self.export_btn = QPushButton("Export as Markdown…")
        self.export_btn.clicked.connect(self.export)
        self.status = QLabel()
        self.status.setObjectName("hint")
        self.error = QLabel()
        self.error.setObjectName("status")
        self.error.setWordWrap(True)
        self.error.hide()
        self.view = QTextBrowser()
        self.view.setObjectName("preview")
        self.view.setOpenLinks(False)
        row = FlowLayout(None, 0, 6, 4)
        for b in (self.check_btn, self.rethink_btn, self.stop_btn, self.export_btn, self.status):
            row.addWidget(b)
        lay = QVBoxLayout(self)
        lay.addWidget(note)
        lay.addLayout(row)
        lay.addWidget(self.error)
        lay.addWidget(self.view, 1)
        self.refresh()

    @property
    def ready(self) -> bool:
        return bool(an.timed_transcript(self.svc.chunks)) and self.svc.pending == 0

    def set_session(self, meta) -> None:
        if meta is None or meta.id == self._session_id:
            return
        self.stop()
        self._session_id = meta.id
        self.report = None
        saved = [d for d in session.list_analyses(self.svc.root, meta.id) if d.get("type") == "followups"]
        if saved:
            try:
                self.report = fu.FollowUpReport.from_json(max(saved, key=lambda d: d["createdAtMs"]))
            except (KeyError, TypeError):
                self.report = None
        self.show_error()
        self._show()
        self.refresh()

    def _show(self) -> None:
        self.view.setMarkdown(fu.follow_up_markdown(self.report) if self.report else "")

    def show_error(self, text: str = "") -> None:
        self.error.setText(text)
        self.error.setVisible(bool(text))

    def refresh(self) -> None:
        self.check_btn.setText("Check again" if self.report else "Check previous meetings")
        self.check_btn.setVisible(not self._busy)
        self.rethink_btn.setVisible(self.report is not None and not self._busy)
        self.stop_btn.setVisible(self._busy)
        self.export_btn.setVisible(self.report is not None and not self._busy)
        self.check_btn.setEnabled(self.ready)

    # data ---------------------------------------------------------------------------------
    def _gather(self) -> tuple[list[fu.PriorItem], list[tuple[str, str, str]]]:
        meta = self.svc.meta
        prior = [m for m in self.svc.sessions() if m.mode == "meeting" and m.id != meta.id and m.created_at_ms < meta.created_at_ms][:self.MAX_MEETINGS]
        items: list[fu.PriorItem] = []
        summaries: list[tuple[str, str, str]] = []
        for m in prior:
            saved = session.list_analyses(self.svc.root, m.id)
            newest = lambda kind: max((d for d in saved if d.get("type") == kind), key=lambda d: d.get("createdAtMs", 0), default=None)  # noqa: E731
            if (sm := newest("summary")) and sm.get("markdown"):
                summaries.append((m.title, date_stamp(m.created_at_ms), sm["markdown"]))
            for n, a in enumerate((newest("actions") or {}).get("items") or []):
                items.append(fu.PriorItem(f"{m.id}-{n}", a["task"], a.get("owner"), a.get("deadline"), m.title, date_stamp(m.created_at_ms)))
        if not items:
            raise RuntimeError("No earlier action items found. Generate ACTION ITEMS for an earlier meeting first, then check again.")
        files: list[tuple[str, str]] = []
        for row in self.db.recent_files(self.MAX_FILES):
            try:
                files.append((os.path.basename(row["path"]), read_snapshot(row["path"]).text))
            except OSError:
                continue                                   # unreadable or missing file: skip
        return fu.match_checkbox_state(items, files)[:self.MAX_ITEMS], summaries

    # run ----------------------------------------------------------------------------------
    def run(self, rethink: bool = False) -> None:
        if self._busy or not self.ready or self.svc.meta is None:
            return
        self.show_error()
        key = self.ai.api_key()
        if not key:
            self.show_error("No Groq API key. Add one in Settings or under New with AI.")
            return
        try:
            items, summaries = self._gather()
        except RuntimeError as e:
            self.show_error(str(e))
            return
        text, _t = an.limit_transcript(an.timed_transcript(self.svc.chunks))
        self._plain = re.sub(r"(?m)^\[\d+(?::\d+)+\]\s*", "", text)
        self._rethink, self._key = rethink, key
        self._items, self._summaries = items, summaries
        self._open = [i for i in items if i.state != "done"]
        self._results = [fu.FollowUp(i.id, "not_mentioned", "", "") for i in items]
        self._topics: list[fu.RecurringTopic] = []
        self._dropped = 0
        self._steps = (["followups"] if self._open else []) + (["topics"] if summaries else [])
        self._busy = True
        self.refresh()
        self._next()

    def _next(self) -> None:
        if not self._steps:
            self._finish()
            return
        self._step = self._steps.pop(0)
        if self._step == "followups":
            system, user = fu.follow_up_system_prompt(), fu.follow_up_user_prompt(self._open, self._plain, self._rethink)
            self.status.setText("Comparing with this meeting…")
        else:
            system, user = fu.topics_system_prompt(), fu.topics_user_prompt(self._summaries, self._plain, self._rethink)
            self.status.setText("Looking for topics that keep coming up…")
        w = StreamWorker(self._key, system, user, self)
        w.done.connect(self._on_done)
        w.stopped.connect(self._on_stopped)
        w.failed.connect(self._on_failed)
        w.finished.connect(self._worker_finished)
        self._worker = w
        w.start()

    def _on_done(self, raw: str) -> None:
        try:
            if self._step == "followups":
                parsed, dropped = fu.to_follow_ups(raw, self._open, self._plain)
                by_id = {p.id: p for p in parsed}
                self._results = [by_id.get(r.id, r) for r in self._results]
            else:
                self._topics, dropped = fu.to_topics(raw, self._plain)
            self._dropped += dropped
        except ValueError as e:
            if self._step == "followups":
                self._abort(str(e))
            # topics are a bonus: a bad reply there must not lose the follow-up results

    def _on_stopped(self, _partial: str) -> None:
        self._steps.clear()
        self._cancelled = True

    def _on_failed(self, message: str) -> None:
        if self._step == "followups":
            self._abort(message)
        # a failure in the topics step is ignored for the same reason

    def _abort(self, message: str) -> None:
        self._steps.clear()
        self._cancelled = True
        self.show_error(message)

    def _worker_finished(self) -> None:
        w, self._worker = self._worker, None
        if w is not None:
            w.deleteLater()
        self._next()

    def _finish(self) -> None:
        cancelled = getattr(self, "_cancelled", False)
        self._cancelled = False
        self._busy = False
        self.status.setText("")
        if not cancelled:
            self.report = fu.FollowUpReport(int(time.time() * 1000), self._items, self._results, self._topics, self._dropped)
            try:
                session.save_analysis(self.svc.root, self.svc.meta.id, "followups", self.report.to_json(), self.report.created_at_ms)
            except OSError as e:
                self.show_error(f"The report could not be saved to the session ({e}).")
            self._show()
        self.refresh()

    def stop(self) -> None:
        self._steps.clear()
        if self._worker is not None:
            self._cancelled = True
            self._worker.cancel()
            self._worker.wait(3000)

    def export(self) -> None:
        if self.report is None or self.svc.meta is None:
            return
        sec = tpl.Section("followups", "Follow-ups from earlier meetings", "follow-ups", fu.follow_up_markdown(self.report))
        self._dialog = ExportDialog(self.svc.meta, [sec], "follow-ups", self.ai, self.db, self.sync, self)
        self._dialog.file_created.connect(self.file_created)
        self._dialog.show()


class QuestionStrip(QWidget):
    """Live "questions you could ask" during meetings. Opt-in (it sends the recent transcript to Groq), throttled, runs beside
    the recording and never interrupts it: any failure is swallowed."""
    TICK_MS = 5000

    def __init__(self, svc, ai, parent=None):
        super().__init__(parent)
        self.svc, self.ai = svc, ai
        self.ideas: list[tuple[int, fu.Suggestion]] = []
        self._asked: list[str] = []
        self._seq = 0
        self._last_at = 0.0
        self._last_words = 0
        self._worker: StreamWorker | None = None
        self._rethink = False
        self.toggle = QCheckBox("Suggest questions I could ask")
        self.toggle.setToolTip("Sends the most recent part of the transcript to Groq about once a minute")
        self.toggle.setChecked(bool(ai.settings.get("voice_questions")))
        self.toggle.toggled.connect(self._toggled)
        self.rethink_btn = QPushButton("↻ Rethink")
        self.rethink_btn.setToolTip("Not what you wanted? Get different suggestions")
        self.rethink_btn.clicked.connect(self.rethink)
        self.thinking = QLabel()
        self.thinking.setObjectName("hint")
        self.box = QVBoxLayout()
        self.note = QLabel("Only if you want them. Nothing here interrupts the recording.")
        self.note.setObjectName("hint")
        head = QHBoxLayout()
        head.addWidget(self.toggle)
        head.addWidget(self.rethink_btn)
        head.addWidget(self.thinking, 1)
        lay = QVBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.addLayout(head)
        lay.addLayout(self.box)
        lay.addWidget(self.note)
        self._timer = QTimer(self, interval=self.TICK_MS)
        self._timer.timeout.connect(self._tick)
        self._timer.start()
        self._sync_controls()

    @property
    def enabled(self) -> bool:
        return self.toggle.isChecked()

    @property
    def active(self) -> bool:
        return self.svc.recording and self.svc.meta is not None and self.svc.meta.mode == "meeting"

    def reset(self) -> None:
        """A new recording, or none: forget everything suggested so far."""
        self.ideas, self._asked = [], []
        self._last_at, self._last_words = 0.0, 0
        self._render()

    def _toggled(self, on: bool) -> None:
        self.ai.settings.set("voice_questions", on)
        if on:
            self._last_at = time.monotonic() - 30            # first suggestions shortly after enabling
        self._sync_controls()
        self._render()

    def _sync_controls(self) -> None:
        self.rethink_btn.setVisible(self.enabled)
        self.note.setVisible(self.enabled)

    def _tick(self) -> None:
        if not self.active:
            return
        now = time.monotonic()
        if not self._last_at:
            self._last_at = now
        words = word_count(transcript_text(self.svc.chunks))
        if fu.should_suggest(self.enabled, self.svc.phase == "paused", self._worker is not None, self.svc.pending,
                             int((now - self._last_at) * 1000), words - self._last_words):
            self._generate(False)

    def rethink(self) -> None:
        if self.active and self.enabled:
            self._generate(True)

    def _generate(self, rethink: bool) -> None:
        text = transcript_text(self.svc.chunks)
        key = self.ai.api_key()
        if self._worker is not None or not key or not word_count(text):
            return
        self._rethink = rethink
        self._last_at, self._last_words = time.monotonic(), word_count(text)
        w = StreamWorker(key, fu.question_system_prompt(), fu.question_user_prompt(fu.recent_context(text), self._asked, rethink), self)
        w.done.connect(self._on_done)
        w.failed.connect(lambda _m: None)                   # suggestions are a bonus: never disturb the recording
        w.finished.connect(self._finished)
        self._worker = w
        self.thinking.setText("thinking…")
        w.start()

    def _on_done(self, raw: str) -> None:
        fresh = fu.parse_questions(raw, self._asked)
        if fresh:
            self._asked = (self._asked + [f.question for f in fresh])[-30:]
            new = [(self._next_id(), f) for f in fresh]
            self.ideas = (new if self._rethink else new + self.ideas)[:6]
        elif self._rethink:
            self.ideas = []
        self._render()

    def _next_id(self) -> int:
        self._seq += 1
        return self._seq

    def _finished(self) -> None:
        w, self._worker = self._worker, None
        if w is not None:
            w.deleteLater()
        self.thinking.setText("")

    def _dismiss(self, idea_id: int) -> None:
        self.ideas = [i for i in self.ideas if i[0] != idea_id]
        self._render()

    def _render(self) -> None:
        while self.box.count():
            item = self.box.takeAt(0)
            if item.widget():
                item.widget().deleteLater()
        if not self.enabled:
            return
        for idea_id, s in self.ideas:
            row = QWidget()
            h = QHBoxLayout(row)
            h.setContentsMargins(0, 0, 0, 0)
            lbl = QLabel(f"<b>{s.kind.upper()}</b>  {s.question}" + (f" <i>— {s.reason}</i>" if s.reason else ""))
            lbl.setWordWrap(True)
            lbl.setStyleSheet(f"color: {load_theme(self.ai.theme_name())['fg']};")
            copy = QPushButton("Copy")
            copy.clicked.connect(lambda _c=False, q=s.question: QGuiApplication.clipboard().setText(q))
            dismiss = QPushButton("✕")
            dismiss.setToolTip("Dismiss")
            dismiss.clicked.connect(lambda _c=False, i=idea_id: self._dismiss(i))
            h.addWidget(lbl, 1)
            h.addWidget(copy)
            h.addWidget(dismiss)
            self.box.addWidget(row)

    def stop(self) -> None:
        if self._worker is not None:
            self._worker.cancel()
            self._worker.wait(3000)
