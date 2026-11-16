"""Typed, validated application settings backed by the SQLite settings table."""
from __future__ import annotations

from typing import Any

from PySide6.QtCore import QObject, Signal

from ..services.persistence import Database

DEFAULTS: dict[str, Any] = {
    "shortcut_primary": "Ctrl+Shift+M",   # open/focus the associated file
    "shortcut_home": "Ctrl+Shift+H",      # show the home screen
    "shortcut_picker": "",                # optional: always open the file picker
    "associated_file": "",                # file bound to the primary shortcut
    "opacity": 1.0,                       # default for new windows
    "theme": "retro",
    "compact_default": False,
    "restore_windows": False,             # reopen last session's windows on startup
    "last_dir": "",
    "button_shadow": False,               # V1-style outline + offset shadow on buttons (off = classic bevel)
    "show_home": True,                    # show the home screen on startup
    "ai_save_dir": "",                    # where AI-created files go ("" = ~/Documents/MarkdownCompanion)
    "ai_history": [],                     # recent AI-edit instructions, newest first
    "animations": True,                   # typing cat, diff reveal
    "groq_api_key": "",                   # only used when the OS keychain is unavailable
}


def _valid_opacity(v: Any) -> float:
    return min(1.0, max(0.3, float(v)))


def _valid_history(v: Any) -> list[str]:
    if not isinstance(v, list):
        raise ValueError("history must be a list")
    return [str(x) for x in v if isinstance(x, str)][:8]


_VALIDATORS = {
    "ai_save_dir": str, "groq_api_key": str, "animations": bool, "ai_history": _valid_history,
    "opacity": _valid_opacity,
    "compact_default": bool,
    "show_home": bool, "button_shadow": bool,
    "restore_windows": bool,
    "theme": str, "last_dir": str, "associated_file": str,
    "shortcut_primary": str, "shortcut_picker": str, "shortcut_home": str,
}


class SettingsManager(QObject):
    changed = Signal(str)  # key

    def __init__(self, db: Database, parent: QObject | None = None):
        super().__init__(parent)
        self._db = db

    def get(self, key: str) -> Any:
        if key not in DEFAULTS:
            raise KeyError(key)
        value = self._db.get_setting(key, DEFAULTS[key])
        try:
            return _VALIDATORS[key](value)
        except (TypeError, ValueError):
            return DEFAULTS[key]

    def set(self, key: str, value: Any) -> None:
        if key not in DEFAULTS:
            raise KeyError(key)
        value = _VALIDATORS[key](value)
        if self._db.get_setting(key, DEFAULTS[key]) == value:
            return
        self._db.set_setting(key, value)
        self.changed.emit(key)
