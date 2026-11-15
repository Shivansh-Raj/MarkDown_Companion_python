"""Process entry point."""
from __future__ import annotations

import argparse
import os
import sys

from .utils.logging_config import configure_logging, get_logger


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="markdown-companion-v2")
    parser.add_argument("files", nargs="*", help="Markdown files to open immediately")
    parser.add_argument("--data-dir", help="store settings/logs here instead of the default location")
    parser.add_argument("--debug", action="store_true")
    ns, qt_args = parser.parse_known_args(argv if argv is not None else sys.argv[1:])
    if ns.data_dir:
        os.environ["MDC_V2_DATA_DIR"] = os.path.abspath(ns.data_dir)

    configure_logging(ns.debug)
    log = get_logger("main")

    from PySide6.QtWidgets import QApplication, QMessageBox

    from .core.application import CompanionApp

    qapp = QApplication([sys.argv[0], *qt_args])
    qapp.setApplicationName("Markdown Companion V2")
    qapp.setOrganizationName("MarkdownCompanionV2")

    def excepthook(exc_type, exc, tb):
        log.error("unhandled exception", exc_info=(exc_type, exc, tb))
    sys.excepthook = excepthook

    app = CompanionApp(qapp)
    if not app.acquire_single_instance():
        QMessageBox.information(None, "Markdown Companion V2", "Markdown Companion V2 is already running (check the tray).")
        return 0
    app.start()
    for f in ns.files:
        app.open_path(os.path.abspath(f))
    return qapp.exec()
