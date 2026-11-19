"""Small dialogs for Notion: find a page or database, and map MC's fields onto a database's properties."""
from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (QComboBox, QDialog, QFormLayout, QHBoxLayout, QLabel, QLineEdit, QListWidget, QListWidgetItem,
                               QPlainTextEdit, QPushButton, QVBoxLayout)

import json

from ..notion.client import PageHit, parse_page_id
from ..notion.db import fields_for
from ..notion.mapping import fits, sample_values, suggest_mapping
from ..notion.worker import run_async
from .fonts import use_body_font, use_pixel_font


class PagePickerDialog(QDialog):
    """Find a page (or a database) by title, or paste a page link. Reading never changes anything in Notion."""

    def __init__(self, notion, what: str = "page", parent=None):
        super().__init__(parent)
        use_body_font(self, 12)
        self.notion, self.what = notion, what
        self.chosen: PageHit | None = None
        self.action = "open"                # "open" | "ask" | "askdb" (page pickers offer asking too)
        self.setWindowTitle("Choose a Notion " + what)
        self.setWindowFlag(Qt.WindowType.WindowStaysOnTopHint, True)
        self.setMinimumWidth(420)
        self.query = QLineEdit()
        self.query.setPlaceholderText("Search by title, or paste a page link" if what == "page" else "Search databases by title")
        self.query.returnPressed.connect(self.find)
        self.find_btn = QPushButton("List all")
        self.find_btn.clicked.connect(self.find)
        self.query.textChanged.connect(lambda t: self.find_btn.setText("Find" if t.strip() else "List all"))
        self.results = QListWidget()
        self.results.setMinimumHeight(140)
        self.results.itemDoubleClicked.connect(lambda _i: self.choose())
        self.results.currentItemChanged.connect(lambda *_: self.choose_btn.setEnabled(self.results.currentItem() is not None))
        self.status = QLabel("")
        self.status.setObjectName("hint")
        self.status.setWordWrap(True)
        self.choose_btn = QPushButton("Choose")
        self.choose_btn.setObjectName("primary")
        self.choose_btn.setEnabled(False)
        self.choose_btn.clicked.connect(self.choose)
        cancel = QPushButton("Cancel")
        cancel.clicked.connect(self.reject)
        self.ask_btn = QPushButton("Ask")
        self.ask_btn.setToolTip("Ask a question about this page without saving it")
        self.ask_btn.setEnabled(False)
        self.ask_btn.clicked.connect(lambda: self._choose_as("ask"))
        self.askdb_btn = QPushButton("Ask a database…")
        self.askdb_btn.setToolTip("Ask a question across one of your databases")
        self.askdb_btn.clicked.connect(lambda: (setattr(self, "action", "askdb"), self.accept()))
        for b in (self.ask_btn, self.askdb_btn):
            b.setVisible(False)
        self.results.currentItemChanged.connect(lambda *_: self.ask_btn.setEnabled(self.results.currentItem() is not None))
        top = QHBoxLayout()
        top.addWidget(self.query, 1)
        top.addWidget(self.find_btn)
        bottom = QHBoxLayout()
        bottom.addWidget(self.askdb_btn)
        bottom.addStretch(1)
        bottom.addWidget(self.ask_btn)
        bottom.addWidget(self.choose_btn)
        bottom.addWidget(cancel)
        lay = QVBoxLayout(self)
        lay.addLayout(top)
        lay.addWidget(self.results, 1)
        lay.addWidget(self.status)
        lay.addLayout(bottom)

    def find(self) -> None:
        q = self.query.text().strip()
        if what_is_link(q) and self.what == "page":
            self.chosen = PageHit(q, "Page from link")
            self.accept()
            return
        self.find_btn.setEnabled(False)
        self.status.setText("Searching...")
        fn = (lambda: self.notion.search_pages(q)) if self.what == "page" else (lambda: self.notion.search_databases(q))
        run_async(self, fn, self._show, self._fail)

    def _show(self, hits) -> None:
        self.find_btn.setEnabled(True)
        self.results.clear()
        for h in hits:
            item = QListWidgetItem(h.title)
            item.setData(Qt.ItemDataRole.UserRole, h)
            self.results.addItem(item)
        self.status.setText("" if hits else "Nothing found. With a connection token, share the " + self.what
                            + " with it first (... > Add connections). Search matches titles only.")
        if hits:
            self.results.setCurrentRow(0)

    def _fail(self, message: str) -> None:
        self.find_btn.setEnabled(True)
        self.status.setText(message)

    def offer_ask(self) -> None:
        """Also offer Ask (this page) and Ask a database (used by the home screen's Notion button)."""
        self.ask_btn.setVisible(True)
        self.askdb_btn.setVisible(True)
        self.choose_btn.setText("Open as note")

    def _choose_as(self, action: str) -> None:
        self.action = action
        self.choose()

    def choose(self) -> None:
        item = self.results.currentItem()
        if item is not None:
            self.chosen = item.data(Qt.ItemDataRole.UserRole)
            self.accept()


def what_is_link(s: str) -> bool:
    import re
    return bool(re.search(r"notion\.(so|site)/", s, re.I)) or (parse_page_id(s) is not None and len(s.replace("-", "")) == 32)


class MappingDialog(QDialog):
    """Say which property of the database each MC field goes into. The title property is always filled with the title."""

    def __init__(self, notion, key: str, schema, mapping: list[dict] | None = None, parent=None):
        super().__init__(parent)
        use_body_font(self, 12)
        self.notion, self.key, self.schema = notion, key, schema
        self.setWindowTitle(f"Map fields: {schema.title}")
        self.setWindowFlag(Qt.WindowType.WindowStaysOnTopHint, True)
        self.setMinimumWidth(460)
        fields = fields_for(key)
        start = {m["field"]: m["property"] for m in (mapping if mapping is not None else suggest_mapping(fields, schema.properties))}
        title_prop = next((p.name for p in schema.properties if p.type == "title"), "the title property")
        intro = QLabel(f"Database: {schema.title}. The title always goes into \"{title_prop}\". Choose where each of the others goes, or leave it unfilled.")
        intro.setWordWrap(True)
        intro.setObjectName("hint")
        form = QFormLayout()
        self.combos: dict[str, QComboBox] = {}
        for f in fields:
            box = QComboBox()
            box.addItem("Don't fill", "")
            for p in schema.properties:
                if fits(f, p):
                    box.addItem(f"{p.name} ({p.type.replace('_', ' ')})", p.name)
            box.setCurrentIndex(max(0, box.findData(start.get(f.key, ""))))
            self.combos[f.key] = box
            form.addRow(f.label + ":", box)
        for i in range(form.rowCount()):
            w = form.itemAt(i, QFormLayout.ItemRole.LabelRole)
            if w is not None and w.widget() is not None:
                use_pixel_font(w.widget(), 10)
        self.preview_box = QPlainTextEdit()
        self.preview_box.setReadOnly(True)
        self.preview_box.setMaximumHeight(140)
        self.preview_box.hide()
        self.status = QLabel("")
        self.status.setWordWrap(True)
        self.status.setObjectName("status")
        self.save_btn = QPushButton("Save")
        self.save_btn.setObjectName("primary")
        self.save_btn.clicked.connect(self.save)
        self.preview_btn = QPushButton("Preview")
        self.preview_btn.setToolTip("Shows what a row would contain, using example values. Nothing is created in Notion.")
        self.preview_btn.clicked.connect(self.preview)
        cancel = QPushButton("Cancel")
        cancel.clicked.connect(self.reject)
        row = QHBoxLayout()
        row.addStretch(1)
        for b in (self.preview_btn, self.save_btn, cancel):
            row.addWidget(b)
        lay = QVBoxLayout(self)
        lay.addWidget(intro)
        lay.addLayout(form)
        lay.addWidget(self.preview_box)
        lay.addWidget(self.status)
        lay.addLayout(row)
        self.saved: dict | None = None

    def mapping(self) -> list[dict]:
        return [{"field": k, "property": b.currentData()} for k, b in self.combos.items() if b.currentData()]

    def _busy(self, on: bool) -> None:
        for b in (self.save_btn, self.preview_btn):
            b.setEnabled(not on)

    def save(self) -> None:
        self._busy(True)
        self.status.setText("Saving...")
        run_async(self, lambda: self.notion.set_destination(self.key, self.schema.id, self.mapping()), self._saved, self._fail)

    def _saved(self, dest: dict) -> None:
        self.saved = dest
        self.accept()

    def preview(self) -> None:
        self._busy(True)
        self.status.setText("Checking...")

        def go():
            self.notion.set_destination(self.key, self.schema.id, self.mapping())      # the preview uses the saved mapping
            return self.notion.preview_row(self.key, "Sample row", sample_values(self.key))
        run_async(self, go, self._previewed, self._fail)

    def _previewed(self, p) -> None:
        self._busy(False)
        self.preview_box.setPlainText(json.dumps(p.properties, indent=2))
        self.preview_box.show()
        self.status.setText(f"A sample row in \"{p.database}\" would contain this. Nothing was created."
                            + ("\n" + "\n".join(p.warnings) if p.warnings else ""))

    def _fail(self, message: str) -> None:
        self._busy(False)
        self.status.setText(message)
