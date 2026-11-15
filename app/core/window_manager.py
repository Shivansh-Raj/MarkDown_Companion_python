"""Owns every MarkdownWindow: one per file, restored geometry, multi-monitor safe."""
from __future__ import annotations

import os

from PySide6.QtCore import QObject, QRect, QTimer, Signal
from PySide6.QtGui import QGuiApplication

from ..services.persistence import Database
from ..ui.markdown_window import DEFAULT_SIZE, MarkdownWindow
from ..utils.geometry import clamp_to_screens
from ..utils.logging_config import get_logger
from .file_manager import FileAccessError, FileManager
from .settings_manager import SettingsManager

log = get_logger("windows")
SAVE_DEBOUNCE_MS = 400


class WindowManager(QObject):
    message = Signal(str, str)        # title, text  (user-facing problems)
    windows_changed = Signal()

    def __init__(self, files: FileManager, db: Database, settings: SettingsManager,
                 parent: QObject | None = None):
        super().__init__(parent)
        self._files, self._db, self._settings = files, db, settings
        self._windows: dict[str, MarkdownWindow] = {}
        self._quitting = False
        self._save_timer = QTimer(self, singleShot=True, interval=SAVE_DEBOUNCE_MS)
        self._save_timer.timeout.connect(self.save_all)
        self._dirty: set[str] = set()
        files.content_changed.connect(self._on_content)
        files.file_state.connect(self._on_file_state)
        files.toggle_failed.connect(self._on_toggle_failed)
        app = QGuiApplication.instance()
        if app is not None:
            app.screenAdded.connect(self._screens_changed)
            app.screenRemoved.connect(self._screens_changed)
            app.primaryScreenChanged.connect(self._screens_changed)
            for s in app.screens():
                s.availableGeometryChanged.connect(self._screens_changed)

    # queries -------------------------------------------------------------------
    def windows(self) -> list[MarkdownWindow]:
        return list(self._windows.values())

    def get(self, path_or_key: str) -> MarkdownWindow | None:
        return self._windows.get(path_or_key) or self._windows.get(FileManager.key_for(path_or_key))

    # open / focus ---------------------------------------------------------------
    def open_file(self, path: str) -> MarkdownWindow | None:
        """Open `path` in its own window, or focus the existing one (never a duplicate)."""
        key = FileManager.key_for(path)
        existing = self._windows.get(key)
        if existing is not None:
            existing.bring_to_front()
            return existing
        try:
            key, text = self._files.open(path)
        except FileAccessError as exc:
            self.message.emit("Cannot open file", str(exc))
            return None

        self._db.touch_file(key, path)
        saved = self._db.get_file(key) or {}
        theme = self._settings.get("theme")
        opacity = saved.get("opacity") or self._settings.get("opacity")
        compact = bool(saved.get("compact", self._settings.get("compact_default")))
        win = MarkdownWindow(key, path, theme, opacity, False)
        self._apply_geometry(win, saved)
        if compact:
            win.set_compact(True)
        win.load_markdown(text)
        win.toggle_requested.connect(self._files.toggle_checkbox)
        win.refresh_requested.connect(self._files.refresh)
        win.close_requested.connect(self.close_window)
        win.state_changed.connect(self._mark_dirty)
        win.link_requested.connect(self.open_file)
        self._windows[key] = win
        self._db.set_open(key, True)
        win.show()
        win.bring_to_front()
        self.windows_changed.emit()
        return win

    def focus(self, key: str) -> None:
        win = self._windows.get(key)
        if win:
            win.bring_to_front()

    def toggle_visibility(self, key: str) -> None:
        win = self._windows.get(key)
        if win is None:
            return
        if win.isVisible() and not win.isMinimized():
            win.hide()
        else:
            win.bring_to_front()
        self.windows_changed.emit()

    def close_window(self, key: str) -> None:
        win = self._windows.pop(key, None)
        if win is None:
            return
        self._save_one(key, win)
        if not self._quitting:
            self._db.set_open(key, False)
        self._files.close(key)
        win.force_close()
        win.deleteLater()
        self.windows_changed.emit()

    def restore_open_windows(self) -> None:
        for row in self._db.open_files():
            if os.path.exists(row["path"]):
                self.open_file(row["path"])
            else:
                self._db.set_open(row["key"], False)

    def apply_theme(self, theme: str) -> None:
        for key, win in self._windows.items():
            snap = self._files.sync.known(self._files._paths.get(key, ""))
            win.set_theme(theme, snap.text if snap else "")

    def shutdown(self) -> None:
        """App is exiting: persist everything but keep windows flagged open for restore."""
        self._quitting = True
        self.save_all()
        for key in list(self._windows):
            self.close_window(key)

    # persistence ----------------------------------------------------------------
    def _mark_dirty(self, key: str) -> None:
        self._dirty.add(key)
        self._save_timer.start()

    def save_all(self) -> None:
        for key in list(self._dirty):
            win = self._windows.get(key)
            if win:
                self._save_one(key, win)
        self._dirty.clear()

    def _save_one(self, key: str, win: MarkdownWindow) -> None:
        try:
            self._db.save_window_state(key, **win.state())
        except Exception:
            log.exception("could not save window state key=%s", key)

    # geometry ---------------------------------------------------------------------
    @staticmethod
    def _screen_rects() -> list[tuple[int, int, int, int]]:
        out = []
        primary = QGuiApplication.primaryScreen()
        screens = sorted(QGuiApplication.screens(), key=lambda s: s is not primary)  # primary first
        for s in screens:
            g: QRect = s.availableGeometry()
            out.append((g.x(), g.y(), g.width(), g.height()))
        return out

    def _apply_geometry(self, win: MarkdownWindow, saved: dict) -> None:
        screens = self._screen_rects()
        if saved.get("x") is not None:
            rect = (saved["x"], saved["y"], saved.get("w") or DEFAULT_SIZE[0], saved.get("h") or DEFAULT_SIZE[1])
        else:  # cascade new windows so they don't stack exactly
            n = len(self._windows)
            sx, sy, sw, sh = screens[0] if screens else (0, 0, 1280, 800)
            rect = (sx + sw - DEFAULT_SIZE[0] - 40 - 28 * n, sy + 60 + 28 * n, *DEFAULT_SIZE)
        x, y, w, h = clamp_to_screens(rect, screens)
        win.set_expanded_height(h)
        win.setGeometry(x, y, w, h)

    def _screens_changed(self, *_: object) -> None:
        screens = self._screen_rects()
        for win in self._windows.values():
            g = win.geometry()
            x, y, w, h = clamp_to_screens((g.x(), g.y(), g.width(), g.height()), screens)
            if (x, y, w) != (g.x(), g.y(), g.width()):
                win.setGeometry(x, y, w, g.height() if win.compact else h)

    # file events --------------------------------------------------------------------
    def _on_content(self, key: str, text: str) -> None:
        win = self._windows.get(key)
        if win:
            win.update_markdown(text)

    def _on_file_state(self, key: str, state: str, message: str) -> None:
        win = self._windows.get(key)
        if not win:
            return
        if state == "ok":
            win.clear_status()
        else:
            win.show_status(message, "error", persistent=True)

    def _on_toggle_failed(self, key: str, message: str) -> None:
        win = self._windows.get(key)
        if win:
            win.show_status(message, "error")
