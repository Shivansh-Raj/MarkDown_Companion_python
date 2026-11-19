"""The Notion block of the Settings window: connect, home page and database destinations."""
from __future__ import annotations

from PySide6.QtWidgets import QHBoxLayout, QLabel, QLineEdit, QMessageBox, QPushButton, QVBoxLayout, QWidget

from ..notion.db import DESTINATIONS, LABELS
from ..notion.worker import run_async
from .fonts import use_body_font, use_pixel_font
from .notion_dialogs import MappingDialog, PagePickerDialog

HELP = {"meetings": "Where meeting summaries and action items are saved.",
        "practice": "Where speaking-practice results (pace, fillers, pauses) are saved.",
        "tasks": "Where approved action items become task rows."}


class NotionSettings(QWidget):
    def __init__(self, notion, parent=None):
        super().__init__(parent)
        use_body_font(self, 12)
        self.notion = notion
        sp = self.sizePolicy()
        sp.setHeightForWidth(True)                     # wrapped help text needs its full height inside the settings form
        self.setSizePolicy(sp)
        lay = QVBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)

        head = QLabel("NOTION")
        use_pixel_font(head, 11)
        lay.addWidget(head)
        self.note = QLabel("Paste a token from Notion's Developer portal. A connection token only reaches pages you share with it "
                           "(... > Add connections). A personal access token acts as you and reaches every page you can see, so use it only "
                           "if you want that. The token is kept in your system credential store and sent only to api.notion.com.")
        self.note.setWordWrap(True)
        self.note.setObjectName("hint")
        lay.addWidget(self.note)
        self.token = QLineEdit()
        self.token.setEchoMode(QLineEdit.EchoMode.Password)
        self.token.setPlaceholderText("Connection token")
        self.token.returnPressed.connect(self.connect_token)
        self.connect_btn = QPushButton("Connect")
        self.connect_btn.setObjectName("primary")
        self.connect_btn.clicked.connect(self.connect_token)
        self.test_btn = QPushButton("Test")
        self.test_btn.clicked.connect(self.test)
        self.disconnect_btn = QPushButton("Disconnect")
        self.disconnect_btn.clicked.connect(self.disconnect)
        self.state = QLabel("")
        row = QHBoxLayout()
        row.addWidget(self.token, 1)
        for b in (self.connect_btn, self.test_btn, self.disconnect_btn):
            row.addWidget(b)
        lay.addLayout(row)
        lay.addWidget(self.state)

        self.lower = QWidget()
        low = QVBoxLayout(self.lower)
        low.setContentsMargins(0, 0, 0, 0)
        hh = QLabel("NOTION HOME PAGE")
        use_pixel_font(hh, 10)
        low.addWidget(hh)
        hl = QLabel("Everything MC creates goes inside this page, in Notes, Meetings, Practice sessions and AI answers. Create an empty "
                    "page in Notion first (for example \"Markdown Companion\"), then choose it here.")
        hl.setWordWrap(True)
        hl.setObjectName("hint")
        low.addWidget(hl)
        self.home_label = QLabel("")
        self.home_choose = QPushButton("Choose...")
        self.home_choose.clicked.connect(self.choose_home)
        self.home_clear = QPushButton("Clear")
        self.home_clear.clicked.connect(self.clear_home)
        hr = QHBoxLayout()
        hr.addWidget(self.home_label, 1)
        hr.addWidget(self.home_choose)
        hr.addWidget(self.home_clear)
        low.addLayout(hr)

        dh = QLabel("NOTION DATABASES")
        use_pixel_font(dh, 10)
        low.addWidget(dh)
        dl = QLabel("Optional. Choose a database for each kind of export and say which of its properties each piece of information goes into. "
                    "Nothing is sent until an export uses it.")
        dl.setWordWrap(True)
        dl.setObjectName("hint")
        low.addWidget(dl)
        self.rows: dict[str, dict] = {}
        for key in DESTINATIONS:
            title = QLabel(LABELS[key].upper())
            use_pixel_font(title, 10)
            label = QLabel("")
            label.setWordWrap(True)
            create = QPushButton("Create for me")
            create.setObjectName("primary")
            create.clicked.connect(lambda _c=False, k=key: self.create_for_me(k))
            existing = QPushButton("Choose existing...")
            existing.clicked.connect(lambda _c=False, k=key: self.choose_existing(k))
            edit = QPushButton("Edit")
            edit.clicked.connect(lambda _c=False, k=key: self.edit_mapping(k))
            clear = QPushButton("Clear")
            clear.clicked.connect(lambda _c=False, k=key: self.clear_destination(k))
            r = QHBoxLayout()
            r.addWidget(label, 1)
            for b in (create, existing, edit, clear):
                r.addWidget(b)
            low.addWidget(title)
            help_ = QLabel(HELP[key])
            help_.setObjectName("hint")
            low.addWidget(help_)
            low.addLayout(r)
            self.rows[key] = {"label": label, "create": create, "existing": existing, "edit": edit, "clear": clear}
        lay.addWidget(self.lower)
        self.msg = QLabel("")
        self.msg.setWordWrap(True)
        self.msg.setObjectName("status")
        lay.addWidget(self.msg)
        self.refresh()

    def hasHeightForWidth(self) -> bool:  # noqa: N802
        return True

    def heightForWidth(self, w: int) -> int:  # noqa: N802
        return self.layout().totalHeightForWidth(w)

    def minimumSizeHint(self):  # noqa: N802
        hint = super().minimumSizeHint()
        hint.setHeight(self.layout().totalHeightForWidth(max(hint.width(), 480)))
        return hint

    # state ------------------------------------------------------------------------------------------
    def refresh(self) -> None:
        on = self.notion.connected()
        self.token.setVisible(not on)
        self.connect_btn.setVisible(not on)
        self.test_btn.setVisible(on)
        self.disconnect_btn.setVisible(on)
        self.note.setVisible(not on)
        self.state.setText("Connected." if on else "")
        self.lower.setVisible(on)
        home = self.notion.home()
        self.home_label.setText(home["title"] if home else "No home page chosen")
        self.home_choose.setText("Change..." if home else "Choose...")
        self.home_clear.setVisible(bool(home))
        for key, w in self.rows.items():
            d = self.notion.destination(key)
            w["label"].setText(f"{d['title']} · {len(d['mapping'])} field{'' if len(d['mapping']) == 1 else 's'} mapped" if d else "Not set up")
            w["create"].setVisible(d is None)
            w["create"].setEnabled(home is not None)
            w["create"].setToolTip("Makes a new database with the right columns" if home else "Choose a home page first")
            w["existing"].setVisible(d is None)
            w["edit"].setVisible(d is not None)
            w["clear"].setVisible(d is not None)

    def say(self, text: str) -> None:
        self.msg.setText(text)

    def _busy(self, on: bool) -> None:
        self.setEnabled(not on)

    def _call(self, fn, ok) -> None:
        self._busy(True)
        run_async(self, fn, lambda r: (self._busy(False), ok(r)), lambda m: (self._busy(False), self.say(m)))

    # connection ---------------------------------------------------------------------------------------
    def connect_token(self) -> None:
        raw = self.token.text()
        if not raw.strip():
            return
        self.say("Checking...")

        def ok(me):
            self.token.clear()
            self.say(f"Connected{' to ' + me[1] if me[1] else ''}.")
            self.refresh()
        self._call(lambda: self.notion.connect(raw), ok)

    def test(self) -> None:
        self.say("Testing...")
        self._call(self.notion.test, lambda me: self.say(f"Working{': ' + me[1] if me[1] else ''}."))

    def disconnect(self) -> None:
        self.notion.disconnect()
        self.say("Disconnected. The token was removed from this computer.")
        self.refresh()

    # home page ----------------------------------------------------------------------------------------------
    def choose_home(self) -> None:
        dlg = PagePickerDialog(self.notion, "page", self)
        if dlg.exec() and dlg.chosen:
            hit = dlg.chosen

            def ok(home):
                self.say(f"Home page set to \"{home['title']}\". Notes, Meetings and the other pages are created inside it when first used.")
                self.refresh()
            self._call(lambda: self.notion.set_home(hit.id), ok)

    def clear_home(self) -> None:
        self.notion.clear_home()
        self.refresh()

    # databases -------------------------------------------------------------------------------------------------
    def create_for_me(self, key: str) -> None:
        t = self.notion.template(key)
        cols = "\n".join(f"  {n}  ({k.replace('_', ' ')})" for n, k in t.columns)
        if QMessageBox.question(self, "Create database",
                                f"This creates a new database called \"{t.title}\" inside your home page, with these columns:\n\n{cols}\n\n"
                                "Nothing existing is changed. If a database with that name is already there, nothing is created.") \
                != QMessageBox.StandardButton.Yes:
            return
        self.say("Creating...")
        self._call(lambda: self.notion.create_destination(key), lambda d: (self.say(f"Created \"{d['title']}\" and mapped its columns."), self.refresh()))

    def choose_existing(self, key: str) -> None:
        dlg = PagePickerDialog(self.notion, "database", self)
        if dlg.exec() and dlg.chosen:
            self._map(key, dlg.chosen.id, None)

    def edit_mapping(self, key: str) -> None:
        d = self.notion.destination(key)
        if d:
            self._map(key, d["data_source_id"], d["mapping"])

    def _map(self, key: str, data_source: str, mapping) -> None:
        self.say("Reading the database...")

        def open_dialog(schema):
            self.say("")
            dlg = MappingDialog(self.notion, key, schema, mapping, self)
            dlg.exec()
            self.refresh()
        self._call(lambda: self.notion.schema(data_source), open_dialog)

    def clear_destination(self, key: str) -> None:
        self.notion.clear_destination(key)
        self.refresh()
