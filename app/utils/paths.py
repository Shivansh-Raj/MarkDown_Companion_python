"""Filesystem locations. Everything V2 writes lives under its own data directory."""
from __future__ import annotations

import os
import sys
from pathlib import Path

APP_DIR_NAME = "MarkdownCompanionV2"  # deliberately different from V1's identifier


def app_data_dir() -> Path:
    """Per-user data dir (%APPDATA% on Windows). MDC_V2_DATA_DIR overrides (tests, --data-dir)."""
    override = os.environ.get("MDC_V2_DATA_DIR")
    if override:
        base = Path(override)
    elif sys.platform == "win32":
        base = Path(os.environ.get("APPDATA") or Path.home() / "AppData" / "Roaming") / APP_DIR_NAME
    elif sys.platform == "darwin":
        base = Path.home() / "Library" / "Application Support" / APP_DIR_NAME
    else:
        base = Path(os.environ.get("XDG_DATA_HOME") or Path.home() / ".local" / "share") / APP_DIR_NAME
    base.mkdir(parents=True, exist_ok=True)
    return base


def resource_dir() -> Path:
    """Bundled read-only assets (works from source and from a PyInstaller bundle)."""
    if getattr(sys, "frozen", False):
        return Path(getattr(sys, "_MEIPASS", Path(sys.executable).parent)) / "assets"
    return Path(__file__).resolve().parents[2] / "assets"


def normalize_key(path: str | os.PathLike) -> str:
    """Canonical identity of a file: symlinks resolved, case-folded on Windows.

    Two requests for the same file always produce the same key, which is what
    prevents duplicate windows.
    """
    return os.path.normcase(os.path.realpath(os.fspath(path)))
