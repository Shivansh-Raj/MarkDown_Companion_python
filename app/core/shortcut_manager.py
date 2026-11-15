"""Global hotkeys.

Windows: RegisterHotKey via ctypes (no keyboard hooks, no admin rights needed). The
hotkey is bound to the GUI thread's message queue and WM_HOTKEY is picked up by a
QAbstractNativeEventFilter, so registration, delivery and cleanup all happen on one thread.

Other platforms: `UnsupportedBackend` reports a clear failure; the rest of the app keeps
working (tray menu etc.). The backend is injectable, which is how the tests exercise
registration, conflicts and cleanup without a real OS hotkey.
"""
from __future__ import annotations

import sys
from dataclasses import dataclass
from typing import Protocol

from PySide6.QtCore import QAbstractNativeEventFilter, QObject, Signal

from ..utils.logging_config import get_logger

log = get_logger("shortcuts")

MOD_ALT, MOD_CONTROL, MOD_SHIFT, MOD_WIN, MOD_NOREPEAT = 0x1, 0x2, 0x4, 0x8, 0x4000
WM_HOTKEY = 0x0312
ERROR_HOTKEY_ALREADY_REGISTERED = 1409

_MODS = {"ctrl": MOD_CONTROL, "control": MOD_CONTROL, "alt": MOD_ALT, "shift": MOD_SHIFT,
         "win": MOD_WIN, "meta": MOD_WIN}
_NAMED = {"space": 0x20, "enter": 0x0D, "return": 0x0D, "esc": 0x1B, "escape": 0x1B, "tab": 0x09,
          "backspace": 0x08, "insert": 0x2D, "ins": 0x2D, "delete": 0x2E, "del": 0x2E, "home": 0x24,
          "end": 0x23, "pgup": 0x21, "pageup": 0x21, "pgdown": 0x22, "pagedown": 0x22,
          "left": 0x25, "up": 0x26, "right": 0x27, "down": 0x28}


class ShortcutError(Exception):
    pass


def parse_shortcut(text: str) -> tuple[int, int]:
    """'Ctrl+Shift+M' -> (modifiers, virtual-key). Raises ShortcutError."""
    parts = [p.strip() for p in text.split("+") if p.strip()]
    if len(parts) < 2:
        raise ShortcutError("A global shortcut needs at least one modifier and a key (e.g. Ctrl+Shift+M).")
    *mod_names, key = parts
    mods = 0
    for m in mod_names:
        if m.lower() not in _MODS:
            raise ShortcutError(f"Unknown modifier '{m}'.")
        mods |= _MODS[m.lower()]
    k = key.lower()
    if len(k) == 1 and k.isalnum() and k.isascii():
        vk = ord(k.upper())
    elif k in _NAMED:
        vk = _NAMED[k]
    elif k.startswith("f") and k[1:].isdigit() and 1 <= int(k[1:]) <= 24:
        vk = 0x6F + int(k[1:])
    else:
        raise ShortcutError(f"Unsupported key '{key}'.")
    if not mods & (MOD_ALT | MOD_CONTROL | MOD_WIN):
        raise ShortcutError("Use at least one of Ctrl, Alt or Win (Shift alone would block normal typing).")
    return mods, vk


class HotkeyBackend(Protocol):
    def register(self, hotkey_id: int, modifiers: int, vk: int) -> None: ...  # raises ShortcutError
    def unregister(self, hotkey_id: int) -> None: ...
    def close(self) -> None: ...


class UnsupportedBackend:
    def register(self, hotkey_id: int, modifiers: int, vk: int) -> None:
        raise ShortcutError(f"Global shortcuts are only implemented for Windows (platform: {sys.platform}).")

    def unregister(self, hotkey_id: int) -> None:
        pass

    def close(self) -> None:
        pass


class Win32Backend(QAbstractNativeEventFilter):
    """RegisterHotKey/UnregisterHotKey on the calling (GUI) thread."""

    def __init__(self, on_hotkey) -> None:
        super().__init__()
        import ctypes
        from ctypes import wintypes

        self._ctypes, self._wintypes = ctypes, wintypes
        self._user32 = ctypes.WinDLL("user32", use_last_error=True)
        self._user32.RegisterHotKey.argtypes = [wintypes.HWND, ctypes.c_int, wintypes.UINT, wintypes.UINT]
        self._user32.RegisterHotKey.restype = wintypes.BOOL
        self._user32.UnregisterHotKey.argtypes = [wintypes.HWND, ctypes.c_int]
        self._user32.UnregisterHotKey.restype = wintypes.BOOL
        self._on_hotkey = on_hotkey
        from PySide6.QtCore import QCoreApplication
        QCoreApplication.instance().installNativeEventFilter(self)

    def register(self, hotkey_id: int, modifiers: int, vk: int) -> None:
        if not self._user32.RegisterHotKey(None, hotkey_id, modifiers | MOD_NOREPEAT, vk):
            err = self._ctypes.get_last_error()
            if err == ERROR_HOTKEY_ALREADY_REGISTERED:
                raise ShortcutError("This shortcut is already in use by another application.")
            raise ShortcutError(f"Windows refused the shortcut (error {err}).")

    def unregister(self, hotkey_id: int) -> None:
        self._user32.UnregisterHotKey(None, hotkey_id)

    def close(self) -> None:
        from PySide6.QtCore import QCoreApplication
        app = QCoreApplication.instance()
        if app is not None:
            app.removeNativeEventFilter(self)

    def nativeEventFilter(self, event_type, message):  # noqa: N802 (Qt API)
        if event_type in (b"windows_generic_MSG", "windows_generic_MSG"):
            msg = self._wintypes.MSG.from_address(int(message))
            if msg.message == WM_HOTKEY:
                self._on_hotkey(int(msg.wParam))
                return True, 0
        return False, 0


def default_backend(on_hotkey) -> HotkeyBackend:
    if sys.platform == "win32":
        try:
            return Win32Backend(on_hotkey)
        except Exception:
            log.exception("could not initialise Win32 hotkey backend")
    return UnsupportedBackend()


@dataclass(frozen=True)
class RegisterResult:
    ok: bool
    message: str = ""


class ShortcutManager(QObject):
    activated = Signal(str)  # action name

    def __init__(self, backend: HotkeyBackend | None = None, parent: QObject | None = None):
        super().__init__(parent)
        self._ids: dict[str, int] = {}
        self._sequences: dict[str, str] = {}
        self._next_id = 1
        self._backend = backend if backend is not None else default_backend(self.dispatch)

    def dispatch(self, hotkey_id: int) -> None:
        for name, hid in self._ids.items():
            if hid == hotkey_id:
                self.activated.emit(name)
                return

    def registered(self) -> dict[str, str]:
        return dict(self._sequences)

    def register(self, name: str, sequence: str) -> RegisterResult:
        """(Re)bind `name` to `sequence`. An empty sequence just unbinds. Never raises."""
        self.unregister(name)
        if not sequence.strip():
            return RegisterResult(True)
        try:
            mods, vk = parse_shortcut(sequence)
            hid = self._next_id
            self._backend.register(hid, mods, vk)
        except ShortcutError as exc:
            log.warning("shortcut registration failed name=%s seq=%s reason=%s", name, sequence, exc)
            return RegisterResult(False, f"{sequence}: {exc}")
        except Exception as exc:  # ctypes/OS surprises must not crash startup
            log.exception("unexpected hotkey failure")
            return RegisterResult(False, f"{sequence}: {exc}")
        self._next_id += 1
        self._ids[name] = hid
        self._sequences[name] = sequence
        return RegisterResult(True)

    def unregister(self, name: str) -> None:
        hid = self._ids.pop(name, None)
        self._sequences.pop(name, None)
        if hid is not None:
            try:
                self._backend.unregister(hid)
            except Exception:
                log.exception("unregister failed name=%s", name)

    def unregister_all(self) -> None:
        for name in list(self._ids):
            self.unregister(name)
        self._backend.close()
