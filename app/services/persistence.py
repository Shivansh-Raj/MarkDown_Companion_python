"""SQLite storage: settings key/value pairs plus a registry of known Markdown files."""
from __future__ import annotations

import json
import sqlite3
import time
from pathlib import Path
from typing import Any

from ..utils.logging_config import get_logger

log = get_logger("persistence")

_SCHEMA = """
CREATE TABLE IF NOT EXISTS settings (key TEXT PRIMARY KEY, value TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS files (
    key TEXT PRIMARY KEY,          -- normalized path (identity)
    path TEXT NOT NULL,            -- path as the user opened it
    last_opened REAL NOT NULL,
    is_open INTEGER NOT NULL DEFAULT 0,
    x INTEGER, y INTEGER, w INTEGER, h INTEGER,
    compact INTEGER NOT NULL DEFAULT 0,
    expanded_h INTEGER,
    opacity REAL
);
"""
_STATE_FIELDS = {"x", "y", "w", "h", "compact", "expanded_h", "opacity"}


class Database:
    def __init__(self, path: str | Path):
        self._conn = sqlite3.connect(str(path))
        self._conn.row_factory = sqlite3.Row
        with self._conn:
            self._conn.executescript(_SCHEMA)

    def close(self) -> None:
        self._conn.close()

    # settings -----------------------------------------------------------
    def get_setting(self, key: str, default: Any = None) -> Any:
        row = self._conn.execute("SELECT value FROM settings WHERE key=?", (key,)).fetchone()
        if row is None:
            return default
        try:
            return json.loads(row["value"])
        except json.JSONDecodeError:
            log.warning("corrupt setting key=%s", key)
            return default

    def set_setting(self, key: str, value: Any) -> None:
        with self._conn:
            self._conn.execute(
                "INSERT INTO settings(key,value) VALUES(?,?) "
                "ON CONFLICT(key) DO UPDATE SET value=excluded.value",
                (key, json.dumps(value)),
            )

    # file registry --------------------------------------------------------
    def touch_file(self, key: str, path: str) -> None:
        with self._conn:
            self._conn.execute(
                "INSERT INTO files(key,path,last_opened) VALUES(?,?,?) "
                "ON CONFLICT(key) DO UPDATE SET path=excluded.path,last_opened=excluded.last_opened",
                (key, path, time.time()),
            )

    def get_file(self, key: str) -> dict | None:
        row = self._conn.execute("SELECT * FROM files WHERE key=?", (key,)).fetchone()
        return dict(row) if row else None

    def save_window_state(self, key: str, **fields: Any) -> None:
        bad = set(fields) - _STATE_FIELDS
        if bad:
            raise ValueError(f"unknown window-state fields: {sorted(bad)}")
        if not fields:
            return
        sets = ", ".join(f"{k}=?" for k in fields)
        with self._conn:
            self._conn.execute(f"UPDATE files SET {sets} WHERE key=?", (*fields.values(), key))

    def set_open(self, key: str, is_open: bool) -> None:
        with self._conn:
            self._conn.execute("UPDATE files SET is_open=? WHERE key=?", (int(is_open), key))

    def open_files(self) -> list[dict]:
        rows = self._conn.execute("SELECT * FROM files WHERE is_open=1 ORDER BY last_opened").fetchall()
        return [dict(r) for r in rows]

    def recent_files(self, limit: int = 10) -> list[dict]:
        rows = self._conn.execute(
            "SELECT * FROM files ORDER BY last_opened DESC LIMIT ?", (limit,)
        ).fetchall()
        return [dict(r) for r in rows]

    def forget_file(self, key: str) -> None:
        with self._conn:
            self._conn.execute("DELETE FROM files WHERE key=?", (key,))
