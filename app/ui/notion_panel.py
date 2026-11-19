"""Notion panel of a note window: send the note to Notion, or (when it is linked to a page) refresh from or push to it."""
from __future__ import annotations

import os
import time

from PySide6.QtCore import Signal
from PySide6.QtWidgets import (QComboBox, QHBoxLayout, QLabel, QLineEdit, QPlainTextEdit, QPushButton, QVBoxLayout, QWidget)

from ..notion.note import prepare_for_notion
from ..notion.worker import run_async
from .fonts import use_body_font, use_pixel_font
from .notion_dialogs import PagePickerDialog


class NotionPanel(QWidget):
    closed = Signal()
    opened_url = Signal(str)             # the user asked to open a page in the browser

    def __init__(self, notion, parent=None):
        super().__init__(parent)
        use_body_font(self, 12)
        self.notion = notion
        self.file_key = self.path = self.text = ""
        self._parent_page = None          # a page chosen with "Choose page..."
        self._url = ""
        self._plan = None
        self._busy = False

        head = QLabel("NOTION")
        head.setObjectName("section")
        close = QPushButton("Close")
        close.clicked.connect(self.close_panel)
        top = QHBoxLayout()
        top.addWidget(head, 1)
        top.addWidget(close)
        self.info = QLabel("")
        self.info.setWordWrap(True)
        self.info.setObjectName("hint")
        self.msg = QLabel("")
        self.msg.setWordWrap(True)
        self.msg.setObjectName("status")

        # not linked: send ------------------------------------------------------------------------------
        self.send_box = QWidget()
        sb = QVBoxLayout(self.send_box)
        sb.setContentsMargins(0, 0, 0, 0)
        self.title_edit = QLineEdit()
        self.title_edit.setMaxLength(2000)
        self.dest = QComboBox()
        self.dest.currentIndexChanged.connect(self._dest_changed)
        self.dest_note = QLabel("")
        self.dest_note.setWordWrap(True)
        self.dest_note.setObjectName("hint")
        self.notes = QLabel("")
        self.notes.setWordWrap(True)
        self.notes.setObjectName("hint")
        self.send_btn = QPushButton("Send")
        self.send_btn.setObjectName("primary")
        self.send_btn.clicked.connect(self.send)
        lt = QLabel("TITLE")
        ld = QLabel("SAVE TO")
        use_pixel_font(lt, 10)
        use_pixel_font(ld, 10)
        for w in (lt, self.title_edit, ld, self.dest, self.dest_note, self.notes):
            sb.addWidget(w)
        sr = QHBoxLayout()
        sr.addWidget(self.send_btn)
        sr.addStretch(1)
        sb.addLayout(sr)

        # linked ---------------------------------------------------------------------------------------------
        self.linked_box = QWidget()
        lb = QVBoxLayout(self.linked_box)
        lb.setContentsMargins(0, 0, 0, 0)
        self.link_label = QLabel("")
        self.link_label.setWordWrap(True)
        self.push_btn = QPushButton("Push to Notion")
        self.push_btn.setObjectName("primary")
        self.push_btn.setToolTip("Review your edits, then send them to the Notion page")
        self.push_btn.clicked.connect(self.preview_push)
        self.refresh_btn = QPushButton("Refresh from Notion")
        self.refresh_btn.setToolTip("Bring Notion's changes into this note. Never overwrites your edits.")
        self.refresh_btn.clicked.connect(self.refresh)
        self.open_btn = QPushButton("Open in Notion")
        self.open_btn.clicked.connect(lambda: self.opened_url.emit(self._url))
        lb.addWidget(self.link_label)
        lr = QHBoxLayout()
        for b in (self.push_btn, self.refresh_btn, self.open_btn):
            lr.addWidget(b)
        lr.addStretch(1)
        lb.addLayout(lr)

        # push preview -------------------------------------------------------------------------------------------
        self.diff_box = QWidget()
        db = QVBoxLayout(self.diff_box)
        db.setContentsMargins(0, 0, 0, 0)
        hint = QLabel("Only this part of the Notion page will change. Only saved edits are included.")
        hint.setObjectName("hint")
        hint.setWordWrap(True)
        self.old_view, self.new_view = QPlainTextEdit(), QPlainTextEdit()
        for v in (self.old_view, self.new_view):
            v.setReadOnly(True)
            v.setMaximumHeight(110)
        lo, ln = QLabel("IN NOTION NOW"), QLabel("AFTER SENDING")
        use_pixel_font(lo, 10)
        use_pixel_font(ln, 10)
        self.confirm_btn = QPushButton("Send this change")
        self.confirm_btn.setObjectName("primary")
        self.confirm_btn.clicked.connect(self.push)
        self.cancel_btn = QPushButton("Cancel")
        self.cancel_btn.clicked.connect(self._cancel_preview)
        for w in (hint, lo, self.old_view, ln, self.new_view):
            db.addWidget(w)
        dr = QHBoxLayout()
        dr.addWidget(self.confirm_btn)
        dr.addWidget(self.cancel_btn)
        dr.addStretch(1)
        db.addLayout(dr)

        lay = QVBoxLayout(self)
        lay.setContentsMargins(8, 6, 8, 6)
        lay.addLayout(top)
        lay.addWidget(self.info)
        lay.addWidget(self.send_box)
        lay.addWidget(self.linked_box)
        lay.addWidget(self.diff_box)
        lay.addWidget(self.msg)
        lay.addStretch(1)
        self._show_only(None)

    # session ---------------------------------------------------------------------------------------------------
    @property
    def busy(self) -> bool:
        return self._busy

    def open_for(self, file_key: str, path: str, text: str) -> None:
        """Called each time the panel opens, with the text to send (the edit draft if editing, else the file)."""
        self.file_key, self.path, self.text = file_key, path, text
        self._parent_page, self._plan = None, None
        self.say("")
        if not self.notion.connected():
            self.info.setText("Notion is not connected. Add a token in Settings > Notion first.")
            self._show_only(None)
            return
        self.info.setText("")
        self._set_busy(True)
        run_async(self, lambda: self.notion.link_info(file_key, path), self._opened, self._fail)

    def _opened(self, link) -> None:
        self._set_busy(False)
        if link is not None:
            self._show_linked(link)
        else:
            self._show_send()

    def close_panel(self) -> None:
        self.closed.emit()

    def say(self, text: str) -> None:
        self.msg.setText(text)

    def _set_busy(self, on: bool) -> None:
        self._busy = on
        for b in (self.send_btn, self.push_btn, self.refresh_btn, self.confirm_btn, self.cancel_btn):
            b.setEnabled(not on)

    def _fail(self, message: str) -> None:
        self._set_busy(False)
        self.say(message)

    def _show_only(self, which) -> None:
        self.send_box.setVisible(which == "send")
        self.linked_box.setVisible(which == "linked")
        self.diff_box.setVisible(which == "diff")

    # sending ------------------------------------------------------------------------------------------------------
    def _show_send(self) -> None:
        self.draft = prepare_for_notion(self.text, os.path.splitext(os.path.basename(self.path))[0])
        self.title_edit.setText(self.draft.title)
        self.notes.setText("\n".join(self.draft.notes))
        self.notes.setVisible(bool(self.draft.notes))
        self.dest.blockSignals(True)
        self.dest.clear()
        home = self.notion.home()
        if home:
            self.dest.addItem(f"{home['title']} > Notes", ("section", "notes"))
        for key, label in (("meetings", "Meetings database"), ("practice", "Practice database")):
            d = self.notion.destination(key)
            if d:
                self.dest.addItem(f"{label}: {d['title']}", ("row", key))
        self.dest.addItem("Another page...", ("page", None))
        self.dest.blockSignals(False)
        self.dest.setCurrentIndex(0)
        self._dest_changed()
        self.send_btn.setEnabled(True)
        self._show_only("send")

    def _dest_changed(self) -> None:
        kind, key = self.dest.currentData() or (None, None)
        if kind == "page":
            self.dest_note.setText(f"Inside: {self._parent_page.title}" if self._parent_page else "Choose the page the new page goes inside.")
            if self._parent_page is None:
                self._choose_page()
        elif kind == "row":
            self.dest_note.setText(f"Adds a row to \"{self.notion.destination(key)['title']}\" with today's date, and the note as the row's page. "
                                   "The other columns stay empty.")
        else:
            self.dest_note.setText("A new page is created. Nothing already in Notion is changed.")

    def _choose_page(self) -> None:
        dlg = PagePickerDialog(self.notion, "page", self)
        if dlg.exec() and dlg.chosen:
            self._parent_page = dlg.chosen
        self._dest_changed_after_pick()

    def _dest_changed_after_pick(self) -> None:
        if self._parent_page is not None:
            self.dest_note.setText(f"Inside: {self._parent_page.title}")
        else:
            self.dest_note.setText("No page chosen. Choose one, or pick another place to save.")

    def send(self) -> None:
        title = self.title_edit.text().strip()
        kind, key = self.dest.currentData() or (None, None)
        if not title:
            self.say("Give the page a title.")
            return
        if kind == "page" and self._parent_page is None:
            self._choose_page()
            if self._parent_page is None:
                return
        md, key_, content, notion = self.draft.markdown, self.file_key, self.text, self.notion
        if kind == "row":
            from datetime import date
            values = {"date": date.today().isoformat(), "source": "Markdown Companion"}
            fn = lambda: notion.create_row(key, title, md, values, key_, content)
        elif kind == "section":
            fn = lambda: notion.create_page("", title, md, key_, content, section=key)
        else:
            parent = self._parent_page.id
            fn = lambda: notion.create_page(parent, title, md, key_, content)
        self._set_busy(True)
        self.say("Sending...")
        run_async(self, fn, self._sent, self._fail)

    def _sent(self, sent) -> None:
        self._set_busy(False)
        self._url = sent.url
        warn = f" Left empty: {' '.join(sent.warnings)}" if sent.warnings else ""
        self.say("Created in Notion" + (" and linked to this note." if sent.linked else ". It could not be linked to this note.") + warn)
        if sent.linked:
            self._set_busy(True)
            run_async(self, lambda: self.notion.link_info(self.file_key, self.path), lambda l: (self._set_busy(False), l and self._show_linked(l, keep_msg=True)), self._fail)
        else:
            self._show_only(None)

    # linked -------------------------------------------------------------------------------------------------------------
    def _show_linked(self, link, keep_msg: bool = False) -> None:
        self._url = link.url
        self.link_label.setText(f"Linked to the Notion page \"{link.title}\". "
                                + ("This note has edits that are not in Notion." if link.local_changed else "No unsent edits."))
        self.push_btn.setEnabled(link.local_changed and not self._busy)
        self.open_btn.setVisible(bool(link.url))
        if not keep_msg:
            self.say("")
        if self._plan is None:               # a late reload must not replace a push preview the user is reading
            self._show_only("linked")

    def refresh(self) -> None:
        self._set_busy(True)
        self.say("Checking Notion...")

        def ok(r):
            self._set_busy(False)
            self.say("Updated this note with the latest from Notion." if r.updated else
                     "Already up to date." if r.state == "in_sync" else
                     "You have edits here that are not in Notion. Nothing was changed." if r.state == "local_changed" else
                     "Both this note and the Notion page changed. Nothing was overwritten.")
            self._reload_link()
        run_async(self, lambda: self.notion.refresh(self.file_key, self.path), ok, self._fail)

    def _reload_link(self) -> None:
        run_async(self, lambda: self.notion.link_info(self.file_key, self.path), lambda l: l and self._show_linked(l, keep_msg=True), self._fail)

    def preview_push(self) -> None:
        self._set_busy(True)
        self.say("Comparing with Notion...")

        def ok(plan):
            self._set_busy(False)
            if plan.kind == "ready":
                self._plan = plan
                self.old_view.setPlainText(plan.old_str)
                self.new_view.setPlainText(plan.new_str)
                self.say("")
                self._show_only("diff")
            elif plan.kind == "nothing_to_send":
                self.say("Nothing to send: this note matches Notion.")
            else:
                self.say("The Notion page changed too, so sending would risk overwriting it. Nothing was sent. Copy your edits, refresh, then redo them."
                         if plan.both else "The Notion page has changes you don't have yet. Refresh first.")
        run_async(self, lambda: self.notion.plan_push(self.file_key, self.path), ok, self._fail)

    def _cancel_preview(self) -> None:
        self._plan = None
        self._show_only("linked")

    def push(self) -> None:
        plan = self._plan
        if plan is None:
            return
        self._set_busy(True)
        self.say("Sending...")

        def ok(warnings):
            self._set_busy(False)
            self._plan = None
            self.say(f"Sent. Notion adjusted something: {'; '.join(warnings)}" if warnings else
                     "Sent to Notion. The previous Notion text was saved as a backup on this computer.")
            self._show_only("linked")
            self._reload_link()
        run_async(self, lambda: self.notion.push(self.file_key, self.path, plan.local_hash), ok, lambda m: (self._fail(m), self._show_only("linked")))
