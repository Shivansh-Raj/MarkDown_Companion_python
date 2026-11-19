"""Before a meeting: earlier matching meetings, the decisions written in them and the open tasks linked to them, from Notion.
Read-only; no AI is involved and nothing is sent anywhere."""
from __future__ import annotations

from PySide6.QtCore import QUrl
from PySide6.QtGui import QDesktopServices
from PySide6.QtWidgets import QHBoxLayout, QLabel, QLineEdit, QPushButton, QTextBrowser, QVBoxLayout, QWidget

import html

from ..notion.worker import run_async
from .fonts import use_body_font


class BriefingBox(QWidget):
    def __init__(self, notion, parent=None):
        super().__init__(parent)
        use_body_font(self, 12)
        self.notion = notion
        self.btn = QPushButton("Briefing from Notion")
        self.btn.setToolTip("Earlier meetings, their decisions and open tasks, from Notion")
        self.btn.clicked.connect(self.open_card)
        self.topic = QLineEdit()
        self.topic.setPlaceholderText("Topic or project (empty: the latest meetings)")
        self.topic.returnPressed.connect(self.load)
        self.update_btn = QPushButton("Update")
        self.update_btn.clicked.connect(self.load)
        self.close_btn = QPushButton("Close")
        self.close_btn.clicked.connect(self.close_card)
        self.view = QTextBrowser()
        self.view.setOpenLinks(False)
        self.view.anchorClicked.connect(lambda u: QDesktopServices.openUrl(u) if u.scheme() in ("http", "https") else None)
        self.view.setMaximumHeight(240)
        self.card = QWidget()
        row = QHBoxLayout()
        row.setContentsMargins(0, 0, 0, 0)
        row.addWidget(self.topic, 1)
        row.addWidget(self.update_btn)
        row.addWidget(self.close_btn)
        cl = QVBoxLayout(self.card)
        cl.setContentsMargins(0, 0, 0, 0)
        cl.addLayout(row)
        cl.addWidget(self.view)
        lay = QVBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.addWidget(self.btn)
        lay.addWidget(self.card)
        self.card.hide()
        self.hide()

    def setup(self, mode: str, title: str) -> None:
        """Shown only for meetings, when Notion is connected and a Meetings database is set up."""
        ok = mode == "meeting" and bool(self.notion and self.notion.connected() and self.notion.destination("meetings"))
        self.setVisible(ok)
        self.card.hide()
        self.btn.show()
        self.topic.setText("" if title == "Meeting" else title)

    def open_card(self) -> None:
        self.btn.hide()
        self.card.show()
        self.load()

    def close_card(self) -> None:
        self.card.hide()
        self.btn.show()

    def load(self) -> None:
        self.update_btn.setEnabled(False)
        self.view.setHtml("<p>Reading Notion…</p>")
        topic = self.topic.text().strip()
        run_async(self, lambda: self.notion.briefing(topic), self._show,
                  lambda m: (self.update_btn.setEnabled(True), self.view.setHtml(f"<p style='color:#d9534f'>{html.escape(m)}</p>")))

    def _show(self, b) -> None:
        self.update_btn.setEnabled(True)
        link = lambda url, text: f'<a href="{html.escape(url)}">{html.escape(text)}</a>' if url else html.escape(text)   # noqa: E731
        out = []
        if b.latest_instead:
            out.append(f"<p><i>No earlier meeting matched “{html.escape(self.topic.text())}”, so the latest ones are shown.</i></p>")
        if not b.meetings:
            out.append(f"<p>No earlier meetings in {html.escape(b.database)} yet.</p>")
        for m in b.meetings:
            out.append(f"<p><b>{link(m.url, m.title)}</b> <i>{html.escape(m.date)}</i></p>")
            out.append("<ul>" + "".join(f"<li>{html.escape(d)}</li>" for d in m.decisions) + "</ul>" if m.decisions
                       else "<p><i>No decisions or takeaways written in this one.</i></p>")
        out.append(f"<p><b>OPEN TASKS ({len(b.tasks)})</b></p>")
        if b.tasks_note:
            out.append(f"<p><i>{html.escape(b.tasks_note)}</i></p>")
        elif not b.tasks:
            out.append("<p><i>No open tasks linked to these meetings.</i></p>")
        out.append("<ul>" + "".join(f"<li>{link(t.url, t.title)} <i>{html.escape(' · '.join(x for x in (t.owner, t.due) if x))}</i></li>" for t in b.tasks) + "</ul>")
        self.view.setHtml("".join(out))
