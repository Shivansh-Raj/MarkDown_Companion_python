"""System tray icon and menu."""
from __future__ import annotations

import os

from PySide6.QtCore import QObject, Signal
from PySide6.QtGui import QAction
from PySide6.QtWidgets import QMenu, QSystemTrayIcon

from ..services.persistence import Database
from .pixel_icon import app_icon


class TrayIcon(QObject):
    open_file_requested = Signal()
    open_recent_requested = Signal(str)
    focus_window_requested = Signal(str)
    toggle_window_requested = Signal(str)
    settings_requested = Signal()
    restart_requested = Signal()
    exit_requested = Signal()

    def __init__(self, db: Database, window_manager, parent: QObject | None = None):
        super().__init__(parent)
        self._db, self._wm = db, window_manager
        self.tray = QSystemTrayIcon(app_icon(), self)
        self.tray.setToolTip("Markdown Companion V2")
        self.menu = QMenu()
        self.menu.addAction("Open Markdown file…", self.open_file_requested.emit)
        self.recent_menu = self.menu.addMenu("Open recent files")
        self.windows_menu = self.menu.addMenu("Manage open windows")
        self.menu.addSeparator()
        self.menu.addAction("Settings…", self.settings_requested.emit)
        self.menu.addAction("Restart application", self.restart_requested.emit)
        self.menu.addAction("Exit", self.exit_requested.emit)
        self.recent_menu.aboutToShow.connect(self._fill_recent)
        self.windows_menu.aboutToShow.connect(self._fill_windows)
        self.tray.setContextMenu(self.menu)
        self.tray.activated.connect(self._on_activated)

    def show(self) -> bool:
        self.tray.show()
        return self.tray.isVisible()

    def hide(self) -> None:
        self.tray.hide()

    def notify(self, title: str, text: str) -> None:
        if QSystemTrayIcon.supportsMessages():
            self.tray.showMessage(title, text, QSystemTrayIcon.MessageIcon.Warning, 6000)

    def _on_activated(self, reason) -> None:
        if reason == QSystemTrayIcon.ActivationReason.Trigger:
            self.open_file_requested.emit()

    def _fill_recent(self) -> None:
        self.recent_menu.clear()
        rows = self._db.recent_files(10)
        if not rows:
            self.recent_menu.addAction("(none)").setEnabled(False)
        for row in rows:
            path = row["path"]
            act = self.recent_menu.addAction(os.path.basename(path))
            act.setToolTip(path)
            act.triggered.connect(lambda _=False, p=path: self.open_recent_requested.emit(p))

    def _fill_windows(self) -> None:
        self.windows_menu.clear()
        wins = self._wm.windows()
        if not wins:
            self.windows_menu.addAction("(no open windows)").setEnabled(False)
        for w in wins:
            sub = self.windows_menu.addMenu(os.path.basename(w.path))
            hidden = not w.isVisible() or w.isMinimized()
            a: QAction = sub.addAction("Show / focus")
            a.triggered.connect(lambda _=False, k=w.key: self.focus_window_requested.emit(k))
            h = sub.addAction("Show" if hidden else "Hide")
            h.triggered.connect(lambda _=False, k=w.key: self.toggle_window_requested.emit(k))
            c = sub.addAction("Close")
            c.triggered.connect(lambda _=False, k=w.key: self._wm.close_window(k))
