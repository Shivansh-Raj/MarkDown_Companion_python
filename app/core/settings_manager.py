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
    "font_scale": 100,                    # text size of the Markdown view, percent
    "hide_dock_icon": True,               # macOS: keep the app out of the Dock, tray/menu bar only
    "voice_questions": False,             # opt-in: suggest questions during meetings (sends recent transcript to Groq)
    "voice_consent": False,               # user accepted that speech is sent to Groq for transcription
    "show_home": True,                    # show the home screen on startup
    "ai_save_dir": "",                    # where AI-created files go ("" = ~/Documents/MarkdownCompanion)
    "ai_history": [],                     # recent AI-edit instructions, newest first
    "animations": True,                   # typing cat, diff reveal
    "groq_api_key": "",                   # only used when the OS keychain is unavailable
    "ai_provider": "groq",                # "groq" or "gateway" (the company AI); speech-to-text always uses Groq
    "gateway_auth_url": "",               # base URL of the company AI's authentication service ("" = DAIA_AUTH_URL from .env)
    "gateway_model_url": "",              # base URL of its model service ("" = DAIA_MODEL_URL from .env)
    "gateway_model": "",                  # model name ("" = DAIA_MODEL from .env, else gpt-5.1)
    "gateway_verify_tls": True,           # check the server's certificate (turn off only behind a re-signing proxy)
    "notion_home": {},                    # the Notion page everything MC creates goes into
    "notion_sections": {},                # section key -> page id under the home page
    "notion_links": {},                   # local file key -> linked Notion page and last-synced hashes
    "notion_destinations": {},            # meetings/practice/tasks -> database and field mapping
}


def _valid_opacity(v: Any) -> float:
    return min(1.0, max(0.3, float(v)))


def _valid_history(v: Any) -> list[str]:
    if not isinstance(v, list):
        raise ValueError("history must be a list")
    return [str(x) for x in v if isinstance(x, str)][:8]


def _valid_dict(v: Any) -> dict:
    if not isinstance(v, dict):
        raise ValueError("must be a mapping")
    return v


_VALIDATORS = {
    "ai_provider": lambda v: v if v in ("groq", "gateway") else (_ for _ in ()).throw(ValueError("provider")),
    "gateway_auth_url": str, "gateway_model_url": str, "gateway_model": str, "gateway_verify_tls": bool,
    "notion_home": _valid_dict, "notion_sections": _valid_dict, "notion_links": _valid_dict, "notion_destinations": _valid_dict,
    "ai_save_dir": str, "groq_api_key": str, "animations": bool, "ai_history": _valid_history,
    "opacity": _valid_opacity, "font_scale": lambda v: min(200, max(60, int(v))),
    "compact_default": bool,
    "show_home": bool, "button_shadow": bool, "voice_consent": bool, "voice_questions": bool, "hide_dock_icon": bool,
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
