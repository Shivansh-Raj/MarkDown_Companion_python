"""Application orchestrator: wires services, tray, shortcuts and windows together."""
from __future__ import annotations

import os
import sys

from PySide6.QtCore import QLockFile, QObject, QProcess, Qt
from PySide6.QtWidgets import QApplication, QMessageBox

from ..markdown.theme import chrome_qss
from ..services.persistence import Database
from ..ui.file_picker import pick_markdown_file
from ..ui.home_window import HomeWindow
from ..ui.pixel_icon import app_icon
from ..ui.settings_window import SettingsWindow
from ..ui.tray_icon import TrayIcon
from ..utils.logging_config import get_logger
from ..utils.paths import app_data_dir
from .file_manager import FileManager
from .settings_manager import SettingsManager
from .shortcut_manager import HotkeyBackend, ShortcutManager
from .window_manager import WindowManager

log = get_logger("app")
ACTION_PRIMARY, ACTION_PICKER, ACTION_HOME = "primary", "picker", "home"


class CompanionApp(QObject):
    def __init__(self, qapp: QApplication, *, backend: HotkeyBackend | None = None,
                 with_tray: bool = True, poll: bool = True):
        super().__init__(qapp)
        self.qapp = qapp
        qapp.setQuitOnLastWindowClosed(False)  # closing windows must never end the background app
        qapp.setWindowIcon(app_icon())
        self._lock: QLockFile | None = None
        self._restarting = False
        self._shutdown_done = False
        self.shortcut_status = ""

        self.db = Database(app_data_dir() / "companion.db")
        self.settings = SettingsManager(self.db, self)
        self.files = FileManager(poll=poll, parent=self)
        self.windows = WindowManager(self.files, self.db, self.settings, self)
        self.shortcuts = ShortcutManager(backend, self)
        self.tray = TrayIcon(self.db, self.windows, self) if with_tray else None
        self._settings_win: SettingsWindow | None = None
        self.home = HomeWindow(self.db, self.windows, self.settings)
        self.home.open_dialog_requested.connect(self.open_via_picker)
        self.home.open_path_requested.connect(self.open_path)
        self.home.focus_requested.connect(self.windows.focus)
        self.home.close_window_requested.connect(self.windows.close_window)
        self.home.forget_requested.connect(self.db.forget_file)
        self.home.settings_requested.connect(self.show_settings)
        self.home.exit_requested.connect(self.exit)

        self.windows.home_requested.connect(self.home.present)
        self.windows.message.connect(self._show_problem)
        self.files.watcher_error.connect(lambda m: self._show_problem("File watching", m, modal=False))
        self.shortcuts.activated.connect(self._on_shortcut)
        self.settings.changed.connect(self._on_setting_changed)
        qapp.aboutToQuit.connect(self.shutdown)
        if self.tray:
            self.tray.home_requested.connect(self.home.present)
            self.tray.open_file_requested.connect(self.open_via_picker)
            self.tray.open_recent_requested.connect(self.open_path)
            self.tray.focus_window_requested.connect(self.windows.focus)
            self.tray.toggle_window_requested.connect(self.windows.toggle_visibility)
            self.tray.settings_requested.connect(self.show_settings)
            self.tray.restart_requested.connect(self.restart)
            self.tray.exit_requested.connect(self.exit)

    # lifecycle ---------------------------------------------------------------
    def acquire_single_instance(self) -> bool:
        self._lock = QLockFile(str(app_data_dir() / "companion.lock"))
        self._lock.setStaleLockTime(0)  # a lock whose process died is stale at once
        if self._lock.tryLock(100):
            return True
        self._lock = None
        return False

    def start(self, show_home: bool = True) -> None:
        self.qapp.setStyleSheet(chrome_qss(self.settings.get("theme")))
        if self.tray and not self.tray.show():
            log.warning("system tray not available")
        self._register_shortcuts()
        if self.settings.get("restore_windows"):
            self.windows.restore_open_windows()
        if show_home and self.settings.get("show_home"):
            self.home.present()
        log.info("started")

    def shutdown(self) -> None:
        if self._shutdown_done:
            return
        self._shutdown_done = True
        self.shortcuts.unregister_all()
        self.home.hide()
        self.windows.shutdown()
        self.files.shutdown()
        if self.tray:
            self.tray.hide()
        self.db.close()
        if self._lock:
            self._lock.unlock()
        log.info("shutdown complete")

    def exit(self) -> None:
        self.qapp.quit()

    def restart(self) -> None:
        self._restarting = True
        self.shutdown()  # releases hotkeys + lock so the new instance can take them
        args = sys.argv[1:] if getattr(sys, "frozen", False) else sys.argv[:]
        if not QProcess.startDetached(sys.executable, args):
            log.error("restart failed to spawn a new process")
        self.qapp.quit()

    # shortcuts ---------------------------------------------------------------
    def _register_shortcuts(self) -> None:
        problems = []
        for name, key in ((ACTION_PRIMARY, "shortcut_primary"), (ACTION_HOME, "shortcut_home"),
                           (ACTION_PICKER, "shortcut_picker")):
            res = self.shortcuts.register(name, self.settings.get(key))
            if not res.ok:
                problems.append(res.message)
        self.shortcut_status = "\n".join(problems)
        self.home.set_shortcut_status(self.shortcut_status.split("\n")[0] if problems else "")
        if problems:
            self._show_problem("Shortcut problem", self.shortcut_status + "\n\nChange it in Settings.", modal=False)
        if self._settings_win:
            self._settings_win.set_shortcut_status(self.shortcut_status or "Shortcuts registered.")

    def _on_setting_changed(self, key: str) -> None:
        if key in ("shortcut_primary", "shortcut_picker", "shortcut_home"):
            self._register_shortcuts()
        elif key == "theme":
            theme = self.settings.get("theme")
            self.qapp.setStyleSheet(chrome_qss(theme))
            self.windows.apply_theme(theme)

    def _on_shortcut(self, action: str) -> None:
        if action == ACTION_HOME:
            self.home.present()
            return
        if action == ACTION_PICKER:
            self.open_via_picker()
            return
        assoc = self.settings.get("associated_file")
        if assoc and os.path.exists(assoc):
            self.open_path(assoc)
        else:
            path = self._pick()
            if path:
                self.settings.set("associated_file", path)
                self.open_path(path)

    # actions -----------------------------------------------------------------
    def _pick(self) -> str | None:
        path = pick_markdown_file(self.settings.get("last_dir"))
        if path:
            self.settings.set("last_dir", os.path.dirname(path))
        return path

    def open_via_picker(self) -> None:
        path = self._pick()
        if path:
            self.open_path(path)

    def open_path(self, path: str):
        return self.windows.open_file(path)

    def show_settings(self) -> None:
        if self._settings_win is None:
            self._settings_win = SettingsWindow(self.settings, self.shortcut_status or "Shortcuts registered.")
            self._settings_win.applied.connect(lambda: None)
        self._settings_win.show()
        self._settings_win.raise_()
        self._settings_win.activateWindow()

    def _show_problem(self, title: str, text: str, modal: bool = True) -> None:
        log.warning("user message title=%s text=%s", title, text.replace("\n", " | "))
        if self.tray and self.tray.tray.isVisible() and not modal:
            self.tray.notify(title, text)
        elif modal and os.environ.get("QT_QPA_PLATFORM") != "offscreen":
            box = QMessageBox(QMessageBox.Icon.Warning, title, text)
            box.setWindowFlag(Qt.WindowType.WindowStaysOnTopHint, True)
            box.exec()
