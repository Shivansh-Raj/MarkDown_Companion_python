"""Open tasks from the Notion Tasks database, in a small always-on-top window (V1's NOTION tab, as its own window).

Ticking a task sets its Status to the database's Done option after checking the task did not change; there is a short undo.
"""
from __future__ import annotations

from datetime import date

from PySide6.QtCore import Qt, QTimer, QUrl
from PySide6.QtGui import QDesktopServices
from PySide6.QtWidgets import (QCheckBox, QDialog, QHBoxLayout, QLabel, QLineEdit, QPushButton, QScrollArea, QVBoxLayout, QWidget)

from ..notion.tasks import GROUP_LABELS, TasksView, group_tasks
from ..notion.worker import run_async
from .fonts import use_body_font, use_pixel_font

REFRESH_MS = 60_000      # Notion limits database queries: refresh on open, on the button, and once a minute while visible
MONTHS = ("Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec")


def short_date(due: str) -> str:
    try:
        return f"{MONTHS[int(due[5:7]) - 1]} {int(due[8:10])}"
    except (ValueError, IndexError):
        return due


class NotionTasksWindow(QDialog):
    def __init__(self, notion, parent=None):
        super().__init__(parent)
        self.setWindowFlag(Qt.WindowType.WindowStaysOnTopHint, True)
        self.setModal(False)
        use_body_font(self, 12)
        self.notion = notion
        self.view: TasksView | None = None
        self._busy = False
        self._undo: tuple | None = None
        self.setWindowTitle("Markdown Companion - Notion tasks")
        self.setMinimumSize(340, 300)
        self.resize(380, 520)

        self.head = QLabel("NOTION TASKS")
        use_pixel_font(self.head, 11)
        refresh = QPushButton("↻")
        refresh.setToolTip("Refresh")
        refresh.clicked.connect(self.reload)
        top = QHBoxLayout()
        top.addWidget(self.head, 1)
        top.addWidget(refresh)
        self.filter = QLineEdit()
        self.filter.setPlaceholderText("Filter tasks, owners or status")
        self.filter.textChanged.connect(self._render)
        self.overdue = QLabel("")
        self.overdue.setObjectName("status")
        self.message = QLabel("")
        self.message.setWordWrap(True)
        self.message.setObjectName("status")
        self.reason = QLabel("")
        self.reason.setWordWrap(True)
        self.reason.setObjectName("hint")
        self.body = QWidget()
        self.rows = QVBoxLayout(self.body)
        self.rows.setContentsMargins(4, 4, 4, 4)
        self.rows.setAlignment(Qt.AlignmentFlag.AlignTop)
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setWidget(self.body)
        scroll.setFrameShape(QScrollArea.Shape.NoFrame)
        scroll.viewport().setAutoFillBackground(False)         # show the theme's background, not the default light grey
        self.body.setAutoFillBackground(False)
        scroll.setStyleSheet("QScrollArea { background: transparent; border: none; }")
        self.body.setStyleSheet("QWidget#notionbody { background: transparent; }")
        self.body.setObjectName("notionbody")
        self.undo_label = QLabel("")
        self.undo_btn = QPushButton("Undo")
        self.undo_btn.clicked.connect(self._do_undo)
        self.undo_bar = QWidget()
        ub = QHBoxLayout(self.undo_bar)
        ub.setContentsMargins(0, 0, 0, 0)
        ub.addWidget(self.undo_label, 1)
        ub.addWidget(self.undo_btn)
        self.undo_bar.hide()
        self._undo_timer = QTimer(self, singleShot=True, interval=8000)
        self._undo_timer.timeout.connect(self._clear_undo)
        self._timer = QTimer(self, interval=REFRESH_MS)
        self._timer.timeout.connect(lambda: self.isVisible() and self.reload())

        lay = QVBoxLayout(self)
        for w in (self.filter, self.overdue, self.message, self.reason):
            lay.insertWidget(lay.count(), w)
        lay.insertLayout(0, top)
        lay.addWidget(scroll, 1)
        lay.addWidget(self.undo_bar)
        self.message.hide()
        self.reason.hide()
        self.overdue.hide()

    # loading ---------------------------------------------------------------------------------------------
    def present(self) -> None:
        self.show()
        self.raise_()
        self.activateWindow()
        self.reload()
        self._timer.start()

    def hideEvent(self, e) -> None:  # noqa: N802
        self._timer.stop()
        super().hideEvent(e)

    def say(self, text: str) -> None:
        self.message.setText(text)
        self.message.setVisible(bool(text))

    def reload(self, _checked: bool = False, keep_message: bool = False) -> None:
        if self._busy:
            return
        if not keep_message:
            self.say("")                  # a message about the last tick stays until the next manual or timed refresh
        run_async(self, self.notion.open_tasks, self._loaded, lambda m: self.say(m))

    def _loaded(self, view: TasksView) -> None:
        self.view = view
        self._render()

    # view ------------------------------------------------------------------------------------------------
    def _render(self, *_a) -> None:
        while self.rows.count():
            w = self.rows.takeAt(0).widget()
            if w is not None:
                w.deleteLater()
        v = self.view
        if v is None:
            return
        today = date.today().isoformat()
        n_over = sum(1 for r in v.rows if r.due and r.due < today)
        self.overdue.setText(f"{n_over} overdue")
        self.overdue.setVisible(n_over > 0)
        self.reason.setText(v.read_only_reason or "")
        self.reason.setVisible(bool(v.read_only_reason))
        groups = group_tasks(v.rows, today, self.filter.text())
        if not v.rows:
            self.rows.addWidget(QLabel(f"Nothing left to do in {v.database}."))
        elif not groups:
            self.rows.addWidget(QLabel(f'No task matches "{self.filter.text()}".'))
        for gid, rs in groups:
            h = QLabel(f"{GROUP_LABELS[gid]} ({len(rs)})")
            use_pixel_font(h, 10)
            self.rows.addWidget(h)
            for r in rs:
                cb = QCheckBox(r.title)
                cb.setEnabled(bool(v.done_status) and not self._busy)
                cb.clicked.connect(lambda _c=False, t=r: self._tick(t))
                meta = [x for x in (r.owner, r.status, short_date(r.due) if r.due else None) if x]
                info = QLabel("  ".join(meta))
                info.setObjectName("hint")
                if r.due and r.due < today:
                    info.setStyleSheet("color: #d9534f;")
                open_btn = QPushButton("Notion")
                open_btn.setToolTip("Open in Notion")
                open_btn.clicked.connect(lambda _c=False, u=r.url: self._open(u))
                open_btn.setVisible(bool(r.url))
                row = QHBoxLayout()
                row.addWidget(cb, 1)
                row.addWidget(info)
                row.addWidget(open_btn)
                holder = QWidget()
                holder.setLayout(row)
                self.rows.addWidget(holder)
        if v.truncated:
            self.rows.addWidget(QLabel("More tasks exist than are shown. Use the filter to narrow the list."))

    @staticmethod
    def _open(url: str) -> None:
        if url.startswith(("https://", "http://")):
            QDesktopServices.openUrl(QUrl(url))

    # ticking ---------------------------------------------------------------------------------------------
    def _tick(self, task) -> None:
        done = self.view.done_status if self.view else None
        if not done:
            return
        self._set(task, task.status, done, offer_undo=True)

    def _set(self, task, expect, to: str, offer_undo: bool) -> None:
        self._busy = True
        self.say("")

        def ok(outcome: str) -> None:
            self._busy = False
            if outcome == "stale":
                self.say(f'"{task.title}" changed in Notion, so nothing was written. The list is refreshed.')
                self._clear_undo()
            elif offer_undo:
                self._undo = (task, to, expect or "Not started")
                self.undo_label.setText(f"Done: {task.title}")
                self.undo_bar.show()
                self._undo_timer.start()
            else:
                self._clear_undo()
            self.reload(keep_message=True)

        def fail(message: str) -> None:
            self._busy = False
            self.say(message)
            self.reload(keep_message=True)
        run_async(self, lambda: self.notion.set_task_status(task.id, expect, to), ok, fail)

    def _do_undo(self) -> None:
        if self._undo:
            task, done, previous = self._undo
            self._set(task, done, previous, offer_undo=False)

    def _clear_undo(self) -> None:
        self._undo = None
        self.undo_bar.hide()
        self._undo_timer.stop()
