"""macOS-only helpers (no extra dependencies: talks to the Objective-C runtime through ctypes)."""
from __future__ import annotations

import ctypes
import ctypes.util
import sys


def set_dock_icon_visible(visible: bool) -> bool:
    """Show or hide the Dock icon. Hidden = "accessory" app: it lives only in the menu bar (top)."""
    if sys.platform != "darwin":
        return False
    try:
        lib = ctypes.cdll.LoadLibrary(ctypes.util.find_library("objc"))
        lib.objc_getClass.restype = ctypes.c_void_p
        lib.objc_getClass.argtypes = [ctypes.c_char_p]
        lib.sel_registerName.restype = ctypes.c_void_p
        lib.sel_registerName.argtypes = [ctypes.c_char_p]
        send = lib.objc_msgSend
        send.restype = ctypes.c_void_p
        send.argtypes = [ctypes.c_void_p, ctypes.c_void_p]
        app = send(lib.objc_getClass(b"NSApplication"), lib.sel_registerName(b"sharedApplication"))
        send.restype = ctypes.c_bool
        send.argtypes = [ctypes.c_void_p, ctypes.c_void_p, ctypes.c_long]
        # 0 = regular (Dock + menu bar), 1 = accessory (menu bar only)
        return bool(send(app, lib.sel_registerName(b"setActivationPolicy:"), 0 if visible else 1))
    except Exception:
        return False
