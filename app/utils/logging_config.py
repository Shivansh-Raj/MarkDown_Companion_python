"""Structured (key=value) logging to a rotating file in the data dir plus stderr."""
from __future__ import annotations

import logging
import logging.handlers
import sys

from .paths import app_data_dir

_FMT = "%(asctime)s level=%(levelname)s logger=%(name)s msg=%(message)s"


def configure_logging(debug: bool = False) -> None:
    root = logging.getLogger("mdc")
    if root.handlers:
        return
    root.setLevel(logging.DEBUG if debug else logging.INFO)
    fmt = logging.Formatter(_FMT)
    try:
        fh = logging.handlers.RotatingFileHandler(
            app_data_dir() / "companion.log", maxBytes=512_000, backupCount=2, encoding="utf-8"
        )
        fh.setFormatter(fmt)
        root.addHandler(fh)
    except OSError:
        pass
    if sys.stderr is not None:  # windowed PyInstaller builds have no stderr
        sh = logging.StreamHandler(sys.stderr)
        sh.setFormatter(fmt)
        root.addHandler(sh)


def get_logger(name: str) -> logging.Logger:
    return logging.getLogger(f"mdc.{name}")
