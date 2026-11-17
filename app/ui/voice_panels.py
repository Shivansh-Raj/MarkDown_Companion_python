"""Panels used by the Voice window: meeting analyses and the Markdown export dialog."""
from __future__ import annotations

import os
import re
import time
from pathlib import Path

from PySide6.QtCore import QRectF, Qt, Signal
from PySide6.QtGui import QColor, QGuiApplication, QPainter
from PySide6.QtWidgets import (QButtonGroup, QCheckBox, QComboBox, QDialog, QFileDialog, QHBoxLayout, QLabel, QLineEdit, QPlainTextEdit,
                               QPushButton, QRadioButton, QScrollArea, QTabWidget, QTextBrowser, QVBoxLayout, QWidget)

from ..ai.worker import StreamWorker
from ..markdown.theme import load_theme
from ..voice import analyses as an
from ..voice import analytics as ana
from ..voice import coach as co
from ..voice import export, session, templates as tpl
from ..voice.transcript import format_clock, suggest_file_name


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
        row = QHBoxLayout()
        for c in self.checks.values():
            row.addWidget(c)
        row.addStretch(1)
        row2 = QHBoxLayout()
        for b in (self.generate_btn, self.stop_btn, self.export_btn):
            row2.addWidget(b)
        row2.addStretch(1)
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
