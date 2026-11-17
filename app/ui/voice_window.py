"""Voice Intelligence workspace: Meeting / Practice / Quick voice note, live transcript, recent sessions."""
from __future__ import annotations

import datetime
import html

from PySide6.QtCore import QRectF, Qt, Signal
from PySide6.QtGui import QColor, QPainter
from PySide6.QtWidgets import (QComboBox, QHBoxLayout, QLabel, QLineEdit, QListWidget, QListWidgetItem, QPushButton,
                               QScrollArea, QStackedWidget, QTextBrowser, QVBoxLayout, QWidget)

from ..ai.create import title_from_markdown, write_new_file
from ..ai.worker import StreamWorker
from ..markdown.theme import load_theme
from ..voice import capture
from ..voice.service import VoiceService
from ..voice.transcript import (MODE_LABELS, format_clock, note_system_prompt, note_user_prompt, suggest_file_name,
                                transcript_markdown, transcript_text, word_count)
from .flow_layout import FlowLayout
from .fonts import body_font
from .rich import set_markdown, style_browser
from .pixel_icon import app_icon
from .voice_panels import AnalysisPanel, PracticePanel, QuestionStrip

PAGE_HOME, PAGE_SETUP, PAGE_SESSION = range(3)
_CARDS = (("meeting", "● Meeting", "Capture a meeting or call, then get a summary, takeaways and action items."),
          ("practice", "● Practice", "Rehearse a talk and get pace, filler and pause stats plus coaching."),
          ("note", "● Quick note", "Say it out loud; get a tidy Markdown note."))
_HINTS = {m: h for m, _t, h in _CARDS}
_ID_ROLE = Qt.ItemDataRole.UserRole


class LevelMeter(QWidget):
    BARS = 16

    def __init__(self, theme: str):
        super().__init__()
        self._t = load_theme(theme)
        self._level = 0.0
        self.setFixedSize(16 * 7 + 2, 16)

    def set_level(self, level: float) -> None:
        self._level = level
        self.update()

    def paintEvent(self, _e) -> None:  # noqa: N802
        p = QPainter(self)
        on = round(min(1.0, self._level * 9) * self.BARS)
        for i in range(self.BARS):
            p.fillRect(QRectF(1 + i * 7, 1, 5, 14), QColor(self._t["accent"] if i < on else self._t["bg_alt"]))
        p.end()


class VoiceWindow(QWidget):
    file_created = Signal(str)          # absolute path of a Markdown file written from voice output

    def __init__(self, service: VoiceService, ai, db=None, sync=None):
        super().__init__(None, Qt.WindowType.Window | Qt.WindowType.WindowStaysOnTopHint)
        self.svc, self.ai, self.db, self.sync = service, ai, db, sync
        self.setFont(body_font(12))                # same text look as the Markdown view
        self.setObjectName("home")                 # themed background like the home screen
        self.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        self.setWindowTitle("Voice Intelligence")
        self.setWindowIcon(app_icon())
        self.setMinimumSize(380, 300)                      # compact is fine: the recording page scrolls when space runs out
        self.resize(640, 680)
        self._mode = "meeting"
        self._armed: str | None = None            # two-click delete confirmation
        self._note_worker: StreamWorker | None = None
        self._note = ""

        self.stack = QStackedWidget()
        self.stack.addWidget(self._home_page())
        self.stack.addWidget(self._setup_page())
        self.stack.addWidget(self._scrolled(self._session_page()))
        self.status = QLabel()
        self.status.setObjectName("status")
        self.status.setWordWrap(True)
        self.status.hide()
        root = QVBoxLayout(self)
        root.addWidget(self.stack, 1)
        root.addWidget(self.status)

        service.changed.connect(self._refresh)
        service.level.connect(self._on_level)
        service.notice.connect(lambda kind, msg: self.show_status(msg, "error" if kind == "error" else "info"))
        self._refresh()

    @staticmethod
    def _scrolled(page: QWidget) -> QScrollArea:
        """Wrap a page so a short window scrolls instead of forcing a large minimum size."""
        sc = QScrollArea()
        sc.setWidgetResizable(True)
        sc.setObjectName("aiscroll")
        sc.setFrameShape(QScrollArea.Shape.NoFrame)
        sc.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAsNeeded)
        sc.setWidget(page)
        return sc

    # ------------------------------------------------------------------ pages ----
    def _home_page(self) -> QWidget:
        w = QWidget()
        lay = QVBoxLayout(w)
        title = QLabel("VOICE INTELLIGENCE")
        title.setObjectName("hero")
        lay.addWidget(title)
        row = QHBoxLayout()
        self.cards: dict[str, QPushButton] = {}
        for mode, label, hint in _CARDS:
            b = QPushButton()
            b.setMinimumHeight(110)
            b.setCursor(Qt.CursorShape.PointingHandCursor)
            inner = QVBoxLayout(b)                 # a button cannot wrap its text, so the card holds two labels
            inner.setContentsMargins(8, 6, 8, 6)
            head, body = QLabel(label), QLabel(hint)
            t = load_theme(self.ai.theme_name())
            head.setStyleSheet(f"font-weight: bold; font-size: 13px; background: transparent; color: {t['fg']};")
            body.setStyleSheet(f"font-size: 11px; background: transparent; color: {t['muted']};")
            body.setWordWrap(True)
            for lbl in (head, body):
                lbl.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents, True)
                lbl.setAlignment(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignTop)
            inner.addWidget(head)
            inner.addWidget(body, 1)
            b.clicked.connect(lambda _c=False, m=mode: self.open_setup(m))
            self.cards[mode] = b
            row.addWidget(b, 1)
        lay.addLayout(row)
        sec = QLabel("RECENT SESSIONS")
        sec.setObjectName("section")
        lay.addWidget(sec)
        filt = QHBoxLayout()
        self.search = QLineEdit()
        self.search.setPlaceholderText("Search sessions…")
        self.search.textChanged.connect(self._fill_sessions)
        self.type_filter = QComboBox()
        self.type_filter.addItem("All types", "all")
        for m, label in MODE_LABELS.items():
            self.type_filter.addItem(label, m)
        self.type_filter.currentIndexChanged.connect(self._fill_sessions)
        filt.addWidget(self.search, 1)
        filt.addWidget(self.type_filter)
        lay.addLayout(filt)
        self.sessions = QListWidget()
        self.sessions.itemActivated.connect(lambda it: self.open_session(it.data(_ID_ROLE)))
        lay.addWidget(self.sessions, 1)
        row2 = QHBoxLayout()
        self.open_btn = QPushButton("Open")
        self.open_btn.clicked.connect(lambda: self._open_selected())
        self.del_row_btn = QPushButton("Delete")
        self.del_row_btn.clicked.connect(self._delete_selected)
        row2.addWidget(self.open_btn)
        row2.addWidget(self.del_row_btn)
        row2.addStretch(1)
        lay.addLayout(row2)
        return w

    def _setup_page(self) -> QWidget:
        w = QWidget()
        lay = QVBoxLayout(w)
        self.setup_title = QLabel()
        self.setup_title.setObjectName("hero")
        self.setup_hint = QLabel()
        self.setup_hint.setObjectName("hint")
        self.setup_hint.setWordWrap(True)
        self.consent_label = QLabel("Speech is turned into text by Groq (api.groq.com): short audio chunks are sent there while "
                                    "you record, and the text is sent there when you ask for analysis. Nothing is recorded until "
                                    "you press Start, and a REC indicator stays visible the whole time.")
        self.consent_label.setWordWrap(True)
        self.consent_btn = QPushButton("I understand")
        self.consent_btn.setObjectName("primary")
        self.consent_btn.clicked.connect(self._give_consent)
        self.title_edit = QLineEdit()
        self.title_edit.setPlaceholderText("Title (optional)")
        self.mic_label = QLabel("MICROPHONE")
        self.mic_label.setObjectName("section")
        self.mic_box = QComboBox()
        self.start_btn = QPushButton("●  Start recording")
        self.start_btn.setObjectName("primary")
        self.start_btn.clicked.connect(self._start)
        self.setup_back = QPushButton("Back")
        self.setup_back.clicked.connect(lambda: self.stack.setCurrentIndex(PAGE_HOME))
        row = QHBoxLayout()
        row.addWidget(self.start_btn)
        row.addWidget(self.setup_back)
        row.addStretch(1)
        for wdg in (self.setup_title, self.setup_hint, self.consent_label, self.consent_btn, self.title_edit, self.mic_label, self.mic_box):
            lay.addWidget(wdg)
        lay.addLayout(row)
        lay.addStretch(1)
        return w

    def _session_page(self) -> QWidget:
        w = QWidget()
        lay = QVBoxLayout(w)
        top = QHBoxLayout()
        self.s_title = QLabel()
        self.s_title.setObjectName("section")
        self.rec_pill = QLabel()
        self.rec_pill.setObjectName("recpill")
        self.rec_pill.setStyleSheet(f"font-weight: bold; padding: 1px 6px; color: {load_theme(self.ai.theme_name())['accent']};")
        self.meter = LevelMeter(self.ai.theme_name())
        top.addWidget(self.s_title, 1)
        top.addWidget(self.meter)
        top.addWidget(self.rec_pill)
        lay.addLayout(top)
        self.pause_btn = QPushButton("❚❚  Pause")
        self.pause_btn.clicked.connect(self._pause_resume)
        self.stop_btn = QPushButton("■  Stop")
        self.stop_btn.clicked.connect(self._stop)
        ctl = QHBoxLayout()
        ctl.addWidget(self.pause_btn)
        ctl.addWidget(self.stop_btn)
        ctl.addStretch(1)
        self.controls = QWidget()
        self.controls.setLayout(ctl)
        ctl.setContentsMargins(0, 0, 0, 0)
        lay.addWidget(self.controls)
        self.transcript = QTextBrowser()
        self.transcript.setObjectName("preview")
        self.transcript.setOpenLinks(False)
        style_browser(self.transcript, self.ai.theme_name())
        lay.addWidget(self.transcript, 1)
        self.note_view = QTextBrowser()
        self.note_view.setObjectName("preview")
        self.note_view.setOpenLinks(False)
        style_browser(self.note_view, self.ai.theme_name())
        self.note_view.hide()
        lay.addWidget(self.note_view, 1)
        self.questions = QuestionStrip(self.svc, self.ai)
        self.questions.hide()
        lay.addWidget(self.questions)
        self.analysis = AnalysisPanel(self.svc, self.ai, self.db, self.sync)
        self.analysis.file_created.connect(self.file_created)
        self.analysis.hide()
        lay.addWidget(self.analysis, 1)
        self.practice = PracticePanel(self.svc, self.ai, self.db, self.sync)
        self.practice.file_created.connect(self.file_created)
        self.practice.hide()
        lay.addWidget(self.practice, 2)
        self.info = QLabel()
        self.info.setObjectName("hint")
        self.info.setWordWrap(True)
        lay.addWidget(self.info)
        # actions after the recording has stopped
        self.save_tr_btn = QPushButton("Save transcript .md")
        self.save_tr_btn.setObjectName("primary")
        self.save_tr_btn.clicked.connect(self._save_transcript)
        self.note_btn = QPushButton("✎  Make note")
        self.note_btn.clicked.connect(self._make_note)
        self.save_note_btn = QPushButton("Save note .md")
        self.save_note_btn.setObjectName("primary")
        self.save_note_btn.clicked.connect(self._save_note)
        self.retry_btn = QPushButton("Retry failed")
        self.retry_btn.clicked.connect(self._retry_failed)
        self.retr_btn = QPushButton("Transcribe recording")
        self.retr_btn.clicked.connect(self._retranscribe)
        self.del_audio_btn = QPushButton("Delete audio")
        self.del_audio_btn.clicked.connect(lambda: self._confirm("audio"))
        self.del_sess_btn = QPushButton("Delete session")
        self.del_sess_btn.clicked.connect(lambda: self._confirm("session"))
        self.back_btn = QPushButton("← Back")
        self.back_btn.clicked.connect(self._back)
        self.actions = QWidget()
        arow = FlowLayout(self.actions, 0, 6, 6)
        for b in (self.save_tr_btn, self.note_btn, self.save_note_btn, self.retry_btn, self.retr_btn, self.del_audio_btn, self.del_sess_btn, self.back_btn):
            arow.addWidget(b)
        lay.addWidget(self.actions)
        return w

    # --------------------------------------------------------------- navigation ----
    def show_status(self, text: str = "", kind: str = "error") -> None:
        self.status.setText(text)
        self.status.setProperty("kind", kind)
        self.status.style().unpolish(self.status)
        self.status.style().polish(self.status)
        self.status.setVisible(bool(text))

    def present(self) -> None:
        self._fill_sessions()
        self.show()
        if self.isMinimized():
            self.showNormal()
        self.raise_()
        self.activateWindow()

    def closeEvent(self, e) -> None:  # noqa: N802
        """Closing only hides the window: a recording keeps running (the tray shows REC)."""
        e.ignore()
        self.hide()

    def open_setup(self, mode: str) -> None:
        self._mode = mode
        self.show_status()
        consent = bool(self.ai.settings.get("voice_consent"))
        self.setup_title.setText(MODE_LABELS[mode].upper())
        self.setup_hint.setText(_HINTS[mode])
        for wdg in (self.consent_label, self.consent_btn):
            wdg.setVisible(not consent)
        for wdg in (self.title_edit, self.mic_label, self.mic_box, self.start_btn):
            wdg.setVisible(consent)
        self.mic_box.clear()
        devices = capture.list_devices()
        for name, is_default in devices:
            self.mic_box.addItem(name + (" (default)" if is_default else ""), name)
        if not devices:
            self.mic_box.addItem("No microphone found", None)
        self.title_edit.clear()
        self.stack.setCurrentIndex(PAGE_SETUP)

    def _give_consent(self) -> None:
        self.ai.settings.set("voice_consent", True)
        self.open_setup(self._mode)

    def _start(self) -> None:
        self.show_status()
        try:
            self.svc.start(self.title_edit.text() or MODE_LABELS[self._mode], self._mode, self.mic_box.currentData())
        except (RuntimeError, ValueError, OSError) as e:
            self.show_status(str(e))

    def open_session(self, session_id: str) -> None:
        meta = next((m for m in self.svc.sessions() if m.id == session_id), None)
        if meta:
            self.svc.open_session(meta)

    def _open_selected(self) -> None:
        it = self.sessions.currentItem()
        if it is not None and it.data(_ID_ROLE):
            self.open_session(it.data(_ID_ROLE))

    def _delete_selected(self) -> None:
        it = self.sessions.currentItem()
        if it is None or not it.data(_ID_ROLE):
            return
        sid = it.data(_ID_ROLE)
        if self._armed != f"row:{sid}":
            self._armed = f"row:{sid}"
            self.del_row_btn.setText("Sure? Delete")
            return
        self._armed = None
        self.del_row_btn.setText("Delete")
        try:
            self.svc.delete_session(sid)
        except (RuntimeError, OSError) as e:
            self.show_status(str(e))
        self._fill_sessions()

    def _back(self) -> None:
        self._cancel_note()
        self._armed = None
        self.note_view.hide()
        self._note = ""
        self.svc.reset()

    # ------------------------------------------------------------------ actions ----
    def _pause_resume(self) -> None:
        (self.svc.resume if self.svc.phase == "paused" else self.svc.pause)()

    def _stop(self) -> None:
        self.svc.stop()

    def _retry_failed(self) -> None:
        for c in [c for c in self.svc.chunks if c.status == "failed"]:
            self.svc.retry(c.index)

    def _retranscribe(self) -> None:
        try:
            self.svc.retranscribe()
        except RuntimeError as e:
            self.show_status(str(e))

    def _confirm(self, kind: str) -> None:
        if self._armed != kind:
            self._armed = kind
            self._refresh()
            return
        self._armed = None
        try:
            if kind == "audio":
                self.svc.delete_audio()
            else:
                self.svc.delete_session()
        except (RuntimeError, OSError) as e:
            self.show_status(str(e))
        self._refresh()

    def _write(self, name: str, body: str) -> None:
        try:
            path = write_new_file(self.ai.save_dir(), name, body if body.endswith("\n") else body + "\n")
        except OSError as e:
            self.show_status(f"Could not save: {e}")
            return
        self.show_status(f"Saved {path.name}.", "info")
        self.file_created.emit(str(path))

    def _save_transcript(self) -> None:
        m = self.svc.meta
        if m:
            self._write(suggest_file_name(m.title, m.created_at_ms, "transcript"), transcript_markdown(m, self.svc.chunks))

    def _make_note(self) -> None:
        text = transcript_text(self.svc.chunks)
        key = self.ai.api_key()
        if not text or self._note_worker is not None:
            return
        if not key:
            self.show_status("No Groq API key. Add one in Settings or under New with AI.")
            return
        self.show_status()
        self._note = ""
        self.note_view.show()
        self.note_view.setPlainText("Writing the note…")
        w = StreamWorker(key, note_system_prompt(), note_user_prompt(text), self)
        w.text.connect(self._on_note_text)
        w.done.connect(self._on_note_done)
        w.stopped.connect(self._on_note_done)
        w.failed.connect(self._on_note_failed)
        w.finished.connect(self._note_finished)
        self._note_worker = w
        self.note_btn.setText("■  Stop")
        self.note_btn.clicked.disconnect()
        self.note_btn.clicked.connect(self._cancel_note)
        w.start()

    def _on_note_text(self, full: str) -> None:
        self._note = full
        set_markdown(self.note_view, full)

    def _on_note_done(self, full: str) -> None:
        self._note = full or self._note
        if self._note.strip():
            set_markdown(self.note_view, self._note)
        self._refresh()

    def _on_note_failed(self, message: str) -> None:
        self.note_view.hide()
        self._note = ""
        self.show_status(message)

    def _note_finished(self) -> None:
        w, self._note_worker = self._note_worker, None
        if w is not None:
            w.deleteLater()
        self.note_btn.setText("✎  Make note")
        self.note_btn.clicked.disconnect()
        self.note_btn.clicked.connect(self._make_note)
        self._refresh()

    def _cancel_note(self) -> None:
        if self._note_worker is not None:
            self._note_worker.cancel()
            self._note_worker.wait(3000)

    def _save_note(self) -> None:
        if self._note.strip():
            self._write(title_from_markdown(self._note, suggest_file_name(self.svc.meta.title, self.svc.meta.created_at_ms, "note")), self._note)

    # ------------------------------------------------------------------ updates ----
    def _on_level(self, level: float, active_ms: int, paused: bool) -> None:
        self.meter.set_level(level)
        self.rec_pill.setText(self.svc.recorder.indicator())

    def _fill_sessions(self, *_a) -> None:
        q = self.search.text().strip().lower()
        mode = self.type_filter.currentData()
        self.sessions.clear()
        every = self.svc.sessions()
        for m in every:
            if (mode != "all" and m.mode != mode) or q not in m.title.lower():
                continue
            when = datetime.datetime.fromtimestamp(m.created_at_ms / 1000).strftime("%Y-%m-%d %H:%M")
            tag = " · RECOVERED" if m.status == "interrupted" else ""
            it = QListWidgetItem(f"{m.title}   [{MODE_LABELS.get(m.mode, m.mode)}{tag}]   {when} · {format_clock(m.duration_ms)}")
            it.setData(_ID_ROLE, m.id)
            self.sessions.addItem(it)
        if self.sessions.count() == 0:
            it = QListWidgetItem("No sessions match." if every else "No recordings yet.")
            it.setFlags(Qt.ItemFlag.NoItemFlags)
            self.sessions.addItem(it)

    def _render_transcript(self) -> str:
        muted = load_theme(self.ai.theme_name())["muted"]
        parts = []
        for c in self.svc.chunks:
            clock = f'<span style="color:{muted}">{format_clock(c.start_ms)}</span>'
            if c.status == "pending":
                parts.append(f'<p style="color:{muted}"><i>{clock} …transcribing</i></p>')
            elif c.status == "failed":
                parts.append(f'<p style="color:#d9534f">{clock} Could not transcribe this part ({html.escape(c.error or "error")})</p>')
            elif c.text.strip():
                parts.append(f"<p>{clock} {html.escape(c.text.strip())}</p>")
        if not parts:
            return f'<p style="color:{muted}">{"Listening… text appears a few seconds after you speak." if self.svc.recording else "No speech was captured."}</p>'
        return "".join(parts)

    def _refresh(self) -> None:
        phase, meta = self.svc.phase, self.svc.meta
        if phase == "idle":
            self.stack.setCurrentIndex(PAGE_HOME if self.stack.currentIndex() != PAGE_SETUP else PAGE_SETUP)
            self._fill_sessions()
            return
        self.stack.setCurrentIndex(PAGE_SESSION)
        recording = phase in ("recording", "paused")
        stopped = phase == "stopped"
        bar = self.transcript.verticalScrollBar()
        follow = bar.value() >= bar.maximum() - 4
        self.transcript.setHtml(self._render_transcript())
        if follow:
            bar.setValue(bar.maximum())
        self.s_title.setText(f"{meta.title}" if meta else "")
        self.rec_pill.setText(self.svc.recorder.indicator() if recording else "")
        self.meter.setVisible(recording)
        self.controls.setVisible(recording)
        self.pause_btn.setText("▶  Resume" if phase == "paused" else "❚❚  Pause")
        self.actions.setVisible(stopped)
        text = transcript_text(self.svc.chunks)
        failed = sum(1 for c in self.svc.chunks if c.status == "failed")
        busy = self.svc.pending > 0
        noting = self._note_worker is not None
        self.save_tr_btn.setEnabled(bool(text) and not busy)
        self.note_btn.setVisible(bool(meta and meta.mode == "note"))
        self.note_btn.setEnabled((bool(text) and not busy) or noting)
        self.save_note_btn.setVisible(bool(self._note.strip()) and not noting)
        self.retry_btn.setVisible(failed > 0 and not busy)
        self.retr_btn.setVisible(not self.svc.chunks and bool(meta and meta.chunks) and not busy)
        self.del_audio_btn.setText("Sure? Delete audio" if self._armed == "audio" else "Delete audio")
        self.del_sess_btn.setText("Sure? Delete all" if self._armed == "session" else "Delete session")
        if not self._note.strip() and not noting:
            self.note_view.hide()
        live_meeting = recording and bool(meta and meta.mode == "meeting")
        if live_meeting and not self.questions.isVisibleTo(self) and self.svc.chunks == []:
            self.questions.reset()                           # a new meeting recording starts with a clean slate
        self.questions.setVisible(live_meeting)
        is_practice = stopped and bool(meta and meta.mode == "practice")
        self.practice.setVisible(is_practice)
        if is_practice:
            self.practice.set_session(meta)
            self.practice.refresh()
        is_meeting = stopped and bool(meta and meta.mode == "meeting")
        self.transcript.setMaximumHeight(120 if (is_practice or is_meeting) else 16777215)   # leave room for the panels
        self.analysis.setVisible(is_meeting)
        if is_meeting:
            self.analysis.set_session(meta)
            self.analysis.refresh()
        if stopped and meta:
            extra = "Transcribing the last part…" if busy else (f"{failed} part(s) failed - use Retry failed." if failed else "")
            self.info.setText(f"{MODE_LABELS.get(meta.mode, meta.mode)} · {format_clock(meta.duration_ms)} · {word_count(text)} words  {extra}".strip())
        else:
            self.info.setText("")
