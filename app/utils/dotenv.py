"""A tiny .env reader (standard library only), so no extra package is needed.

Accepts `KEY=value` and `key: "value"` lines, optional `export `, quotes and # comments. Real environment variables win over
the file. The file is searched for next to the app, in the current folder and in the app's data folder.
"""
from __future__ import annotations

import os
import re
from pathlib import Path

_LINE = re.compile(r"^\s*(?:export\s+)?([A-Za-z_][A-Za-z0-9_.-]*)\s*[=:]\s*(.*?)\s*$")


def parse(text: str) -> dict[str, str]:
    out: dict[str, str] = {}
    for raw in text.splitlines():
        if not raw.strip() or raw.lstrip().startswith("#"):
            continue
        m = _LINE.match(raw)
        if not m:
            continue
        key, val = m.group(1), m.group(2)
        if val[:1] in "\"'" and val.endswith(val[0]) and len(val) >= 2:
            val = val[1:-1]
        elif " #" in val:
            val = val.split(" #", 1)[0].rstrip()
        out[key.upper()] = val
    return out


def candidate_paths() -> list[Path]:
    from .paths import app_data_dir
    root = Path(__file__).resolve().parents[2]
    return [Path.cwd() / ".env", root / ".env", app_data_dir() / ".env"]


def load() -> dict[str, str]:
    """Values from the first .env files found (earlier paths win), then real environment variables on top. Keys are upper-case."""
    merged: dict[str, str] = {}
    for p in reversed(candidate_paths()):
        try:
            merged.update(parse(p.read_text(encoding="utf-8-sig")))
        except OSError:
            continue
    merged.update({k.upper(): v for k, v in os.environ.items() if k.upper() in _WANTED})
    return merged


_WANTED = {"CLIENT_ID", "CLIENT_SECRET", "DAIA_AUTH_URL", "DAIA_MODEL_URL", "DAIA_MODEL"}
