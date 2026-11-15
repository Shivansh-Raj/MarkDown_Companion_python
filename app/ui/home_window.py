"""Home screen: launchpad for recent files and open windows."""
from __future__ import annotations

import os

from PySide6.QtCore import Qt, Signal
from PySide6.QtGui import QDragEnterEvent, QDropEvent
from PySide6.QtWidgets import (QHBoxLayout, QLabel, QListWidget, QListWidgetItem, QMenu, QPushButton,
                               QVBoxLayout, QWidget)

from .pixel_icon import app_icon

MD_EXT = (".md", ".markdown", ".mdown", ".txt")
_KEY = Qt.ItemDataRole.UserRole


class HomeWindow(QWidget):
    open_dialog_requested = Signal()
    open_path_requested = Signal(str)
    focus_requested = Signal(str)       # window key
    close_window_requested = Signal(str)
    forget_requested = Signal(str)      # file key
    settings_requested = Signal()
    exit_requested = Signal()

    def __init__(self, db, window_manager, settings):
        super().__init__()
        self._db, self._wm, self._settings = db, window_manager, settings
        self.shortcut_status = ""
        self.setObjectName("home")
        self.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        self.setWindowTitle("Markdown Companion V2")
        self.setWindowIcon(app_icon())
        self.setAcceptDrops(True)
        self.resize(480, 600)

        root = QVBoxLayout(self)
        root.setContentsMargins(18, 16, 18, 14)
        root.setSpacing(8)

        hero = QLabel("MARKDOWN\nCOMPANION")
        hero.setObjectName("hero")
        self.hint = QLabel()
        self.hint.setObjectName("hint")
        self.hint.setWordWrap(True)
        root.addWidget(hero)
        root.addWidget(self.hint)

        row = QHBoxLayout()
        self.open_btn = QPushButton("\u25b6  Open file\u2026")
        self.open_btn.setObjectName("primary")
        self.open_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self.open_btn.clicked.connect(self.open_dialog_requested)
        settings_btn = QPushButton("Settings")
        settings_btn.clicked.connect(self.settings_requested)
        row.addWidget(self.open_btn, 1)
        row.addWidget(settings_btn)
        root.addLayout(row)

        self.drop_hint = QLabel("or drag & drop .md files anywhere on this window")
        self.drop_hint.setObjectName("hint")
        root.addWidget(self.drop_hint)

        root.addWidget(self._section("OPEN WINDOWS"))
        self.windows_list = QListWidget()
        self.windows_list.itemActivated.connect(lambda it: self.focus_requested.emit(it.data(_KEY)))
        self.windows_list.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self.windows_list.customContextMenuRequested.connect(self._windows_menu)
        root.addWidget(self.windows_list, 2)

        root.addWidget(self._section("RECENT FILES"))
        self.recent_list = QListWidget()
        self.recent_list.itemActivated.connect(lambda it: self.open_path_requested.emit(it.data(_KEY)))
        self.recent_list.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self.recent_list.customContextMenuRequested.connect(self._recent_menu)
        root.addWidget(self.recent_list, 3)

        foot = QHBoxLayout()
        self.footer = QLabel("Closing this window keeps the app running in the tray.")
        self.footer.setObjectName("hint")
        self.footer.setWordWrap(True)
        quit_btn = QPushButton("Exit app")
        quit_btn.clicked.connect(self.exit_requested)
        foot.addWidget(self.footer, 1)
        foot.addWidget(quit_btn)
        root.addLayout(foot)

        wm_signal = getattr(window_manager, "windows_changed", None)
        if wm_signal is not None:
            wm_signal.connect(self.refresh)
        self.refresh()

    @staticmethod
    def _section(text: str) -> QLabel:
        lbl = QLabel(text)
        lbl.setObjectName("section")
        return lbl

    # content --------------------------------------------------------------------
    def set_shortcut_status(self, text: str) -> None:
        self.shortcut_status = text
        self._update_hint()

    def _update_hint(self) -> None:
        seq = self._settings.get("shortcut_primary")
        if self.shortcut_status:
            self.hint.setText(f"\u26a0 {self.shortcut_status}")
            self.hint.setProperty("warn", True)
        else:
            self.hint.setText(f"Press {seq} anywhere to open or focus your main file." if seq
                              else "No global shortcut set (see Settings).")
            self.hint.setProperty("warn", False)
        self.hint.style().unpolish(self.hint)
        self.hint.style().polish(self.hint)

    def refresh(self) -> None:
        self._update_hint()
        self.windows_list.clear()
        for w in self._wm.windows():
            state = "hidden" if not w.isVisible() else ("minimized" if w.isMinimized() else "open")
            it = QListWidgetItem(f"{os.path.basename(w.path)}   [{state}]")
            it.setToolTip(w.path)
            it.setData(_KEY, w.key)
            self.windows_list.addItem(it)
        if self.windows_list.count() == 0:
            self._placeholder(self.windows_list, "No windows open yet.")
        open_keys = {w.key for w in self._wm.windows()}
        self.recent_list.clear()
        for row in self._db.recent_files(15):
            exists = os.path.exists(row["path"])
            name = os.path.basename(row["path"]) + ("" if exists else "   [missing]")
            it = QListWidgetItem(("\u25cf " if row["key"] in open_keys else "   ") + name)
            it.setToolTip(row["path"])
            it.setData(_KEY, row["path"])
            it.setData(_KEY + 1, row["key"])
            if not exists:
                it.setForeground(Qt.GlobalColor.gray)
            self.recent_list.addItem(it)
        if self.recent_list.count() == 0:
            self._placeholder(self.recent_list, "Nothing yet \u2014 open a file to get started.")

    @staticmethod
    def _placeholder(lst: QListWidget, text: str) -> None:
        it = QListWidgetItem(text)
        it.setFlags(Qt.ItemFlag.NoItemFlags)
        lst.addItem(it)

    def showEvent(self, e) -> None:  # noqa: N802
        self.refresh()
        super().showEvent(e)

    def present(self) -> None:
        self.show()
        if self.isMinimized():
            self.showNormal()
        self.raise_()
        self.activateWindow()

    # menus ----------------------------------------------------------------------
    def _windows_menu(self, pos) -> None:
        it = self.windows_list.itemAt(pos)
        if it is None or it.data(_KEY) is None:
            return
        m = QMenu(self)
        m.addAction("Show / focus", lambda: self.focus_requested.emit(it.data(_KEY)))
        m.addAction("Close window", lambda: self.close_window_requested.emit(it.data(_KEY)))
        m.exec(self.windows_list.viewport().mapToGlobal(pos))

    def _recent_menu(self, pos) -> None:
        it = self.recent_list.itemAt(pos)
        if it is None or it.data(_KEY) is None:
            return
        m = QMenu(self)
        m.addAction("Open", lambda: self.open_path_requested.emit(it.data(_KEY)))
        m.addAction("Remove from list", lambda: (self.forget_requested.emit(it.data(_KEY + 1)), self.refresh()))
        m.exec(self.recent_list.viewport().mapToGlobal(pos))

    # drag & drop ------------------------------------------------------------------
    def dragEnterEvent(self, e: QDragEnterEvent) -> None:  # noqa: N802
        if any(u.isLocalFile() and u.toLocalFile().lower().endswith(MD_EXT) for u in e.mimeData().urls()):
            e.acceptProposedAction()

    def dropEvent(self, e: QDropEvent) -> None:  # noqa: N802
        for u in e.mimeData().urls():
            p = u.toLocalFile()
            if u.isLocalFile() and p.lower().endswith(MD_EXT):
                self.open_path_requested.emit(p)
        e.acceptProposedAction()
