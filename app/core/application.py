"""Application orchestrator: wires services, tray, shortcuts and windows together."""
from __future__ import annotations

import os
import sys

from PySide6.QtCore import QLockFile, QObject, QProcess, Qt
from PySide6.QtWidgets import QApplication, QMessageBox

from ..markdown.theme import chrome_qss
from ..ui.button_fx import ButtonFx
from ..ui import fonts, rich, scale
from ..ui.fonts import load_fonts
from ..utils.macos import set_dock_icon_visible
from ..services.persistence import Database
from ..ui.ai_create_window import AiCreateWindow
from ..ui.voice_window import VoiceWindow
from ..voice.service import VoiceService
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

        load_fonts()                                # the Silkscreen heading font, for Qt text as well as the Markdown pages
        self.db = Database(app_data_dir() / "companion.db")
        self.settings = SettingsManager(self.db, self)
        scale.set_percent(self.settings.get("font_scale"))          # the Text size setting drives the whole interface
        qapp.setFont(scale.scaled_app_font())
        self.files = FileManager(poll=poll, parent=self)
        self.windows = WindowManager(self.files, self.db, self.settings, self)
        self.shortcuts = ShortcutManager(backend, self)
        self.tray = TrayIcon(self.db, self.windows, self) if with_tray else None
        self._settings_win: SettingsWindow | None = None
        self._ai_win: AiCreateWindow | None = None
        self._voice_win: VoiceWindow | None = None
        self.voice = VoiceService(app_data_dir() / "voice", self.windows.ai, self)
        self.voice.indicator.connect(self._on_voice_indicator)
        self.home = HomeWindow(self.db, self.windows, self.settings)
        self.home.open_dialog_requested.connect(self.open_via_picker)
        self.home.open_path_requested.connect(self.open_path)
        self.home.focus_requested.connect(self.windows.focus)
        self.home.close_window_requested.connect(self.windows.close_window)
        self.home.forget_requested.connect(self.db.forget_file)
        self.home.settings_requested.connect(self.show_settings)
        self.home.ai_create_requested.connect(self.show_ai_create)
        self.home.voice_requested.connect(self.show_voice)
        self.home.notion_requested.connect(self.show_notion_open)
        self.home.notion_tasks_requested.connect(self.show_notion_tasks)
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
            self.tray.voice_requested.connect(self.show_voice)
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
        set_dock_icon_visible(not self.settings.get("hide_dock_icon"))     # no-op outside macOS
        v1 = bool(self.settings.get("button_shadow"))
        self.qapp.setStyleSheet(chrome_qss(self.settings.get("theme"), v1))
        self.button_fx = ButtonFx(self.settings.get("theme"), self.qapp.style().name(), v1)  # V1-style button outline
        self.qapp.setStyle(self.button_fx)
        if self.tray and not self.tray.show():
            log.warning("system tray not available")
        self.voice.recover()                        # repair recordings interrupted by a crash
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
        self.voice.shutdown()                       # finalise an in-progress recording
        if self._voice_win is not None:             # closing only hides it, so destroy it explicitly on quit
            self._voice_win.analysis.stop()
            self._voice_win.practice.stop()
            self._voice_win.questions.stop()
            self._voice_win.hide()
            self._voice_win.deleteLater()
            self._voice_win = None
        self.home.hide()
        if getattr(self, "_notion_tasks_win", None) is not None:
            self._notion_tasks_win.close()
            self._notion_tasks_win.deleteLater()
            self._notion_tasks_win = None
        if self._ai_win is not None:
            self._ai_win.close()
        self.windows.shutdown()
        self.files.shutdown()
        if self.tray:
            self.tray.hide()
        self.db.close()
        if self._lock:
            self._lock.unlock()
        log.info("shutdown complete")

    def _confirm_discard_unsaved(self) -> bool:
        if not self.windows.has_unsaved() or os.environ.get("QT_QPA_PLATFORM") == "offscreen":
            return True
        box = QMessageBox(QMessageBox.Icon.Warning, "Unsaved changes",
                          "Some windows have unsaved edits. Quit and discard them?",
                          QMessageBox.StandardButton.Discard | QMessageBox.StandardButton.Cancel)
        box.setWindowFlag(Qt.WindowType.WindowStaysOnTopHint, True)
        return box.exec() == QMessageBox.StandardButton.Discard

    def exit(self) -> None:
        if self._confirm_discard_unsaved():
            self.qapp.quit()

    def restart(self) -> None:
        if not self._confirm_discard_unsaved():
            return
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

    def apply_ui_scale(self) -> None:
        """Text size changed: re-apply it everywhere, live - application font, style sheet, hand-set fonts, fixed-size widgets."""
        pct = int(self.settings.get("font_scale"))
        scale.set_percent(pct)
        self.qapp.setFont(scale.scaled_app_font())
        self.qapp.setStyleSheet(chrome_qss(self.settings.get("theme"), bool(self.settings.get("button_shadow"))))
        fonts.rescale_all()
        rich.rescale_browsers()
        self.windows.apply_font_scale(pct)                          # the Markdown views zoom with the same percentage
        for win in self.windows.windows():
            win.rescale()
            if win.ask_panel is not None:
                win.ask_panel.refresh_text()
        self.home.apply_scale()
        if self._voice_win is not None:
            self._voice_win.refresh_text()
        if self._settings_win is not None:                      # grows so larger text is never cut off (it never shrinks on its own)
            self._settings_win.rescale()

    def _on_setting_changed(self, key: str) -> None:
        if key in ("shortcut_primary", "shortcut_picker", "shortcut_home"):
            self._register_shortcuts()
        elif key == "hide_dock_icon":
            set_dock_icon_visible(not self.settings.get("hide_dock_icon"))
        elif key == "font_scale":
            self.apply_ui_scale()
        elif key in ("theme", "button_shadow"):
            theme, v1 = self.settings.get("theme"), bool(self.settings.get("button_shadow"))
            self.qapp.setStyleSheet(chrome_qss(theme, v1))
            self.home.apply_theme()
            if getattr(self, "button_fx", None):
                self.button_fx.set_theme(theme)
                self.button_fx.set_enabled(v1)
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

    def show_notion_tasks(self) -> None:
        """The Notion tasks window: open and overdue tasks from the Tasks database."""
        from ..ui.notion_tasks_window import NotionTasksWindow
        notion = self.windows.notion
        if not notion.connected() or notion.destination("tasks") is None:
            self.windows.message.emit("Notion tasks", "Connect Notion and set up a Tasks database in Settings > Notion first.")
            return
        if getattr(self, "_notion_tasks_win", None) is None:
            self._notion_tasks_win = NotionTasksWindow(notion)
        self._notion_tasks_win.present()

    def show_notion_open(self) -> None:
        """Open a Notion page as a local, linked note: choose it, save it into the AI files folder, open it."""
        from ..notion.worker import run_async
        from ..ui.notion_dialogs import PagePickerDialog
        notion = self.windows.notion
        if not notion.connected():
            self.windows.message.emit("Notion is not connected", "Add a connection token in Settings > Notion first.")
            return
        dlg = PagePickerDialog(notion, "page", self.home)
        if not (dlg.exec() and dlg.chosen):
            return
        page, folder = dlg.chosen.id, self.windows.ai.save_dir()

        def opened(pulled) -> None:
            self.open_path(pulled.path)
            if pulled.unknown_blocks:
                self.windows.message.emit("Opened from Notion", f"{pulled.unknown_blocks} block(s) could not be shown as Markdown and appear as "
                                          "<unknown> tags. Leave them as they are.")
        run_async(self, lambda: notion.pull(page, folder), opened, lambda m: self.windows.message.emit("Could not open the Notion page", m))

    def show_settings(self) -> None:
        if self._settings_win is None:
            self._settings_win = SettingsWindow(self.settings, self.shortcut_status or "Shortcuts registered.",
                                                self.windows.ai, self.windows.notion)
            self._settings_win.applied.connect(lambda: None)
        self._settings_win.show()
        self._settings_win.raise_()
        self._settings_win.activateWindow()

    def show_voice(self) -> None:
        if self._voice_win is None:
            self._voice_win = VoiceWindow(self.voice, self.windows.ai, self.db, self.files.sync)
            self._voice_win.file_created.connect(self.open_path)
        self._voice_win.present()

    def _on_voice_indicator(self, text: str) -> None:
        if self.tray:
            self.tray.set_recording(text)

    def show_ai_create(self) -> None:
        if self._ai_win is None:
            self._ai_win = AiCreateWindow(self.windows.ai)
            self._ai_win.created.connect(self.open_path)
            self._ai_win.finished.connect(lambda _r: self._drop_ai_win())
        self._ai_win.show_start()
        self._ai_win.show()
        self._ai_win.raise_()
        self._ai_win.activateWindow()

    def _drop_ai_win(self) -> None:
        w, self._ai_win = self._ai_win, None
        if w is not None:
            w.deleteLater()

    def _show_problem(self, title: str, text: str, modal: bool = True) -> None:
        log.warning("user message title=%s text=%s", title, text.replace("\n", " | "))
        if self.tray and self.tray.tray.isVisible() and not modal:
            self.tray.notify(title, text)
        elif modal and os.environ.get("QT_QPA_PLATFORM") != "offscreen":
            box = QMessageBox(QMessageBox.Icon.Warning, title, text)
            box.setWindowFlag(Qt.WindowType.WindowStaysOnTopHint, True)
            box.exec()
