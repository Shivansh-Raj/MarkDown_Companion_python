"""Native file dialog restricted to Markdown files."""
from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtWidgets import QFileDialog, QWidget

MD_FILTER = "Markdown (*.md *.markdown *.mdown *.txt);;All files (*)"


def pick_markdown_file(start_dir: str = "", parent: QWidget | None = None) -> str | None:
    dlg = QFileDialog(parent, "Open Markdown file", start_dir, MD_FILTER)
    dlg.setFileMode(QFileDialog.FileMode.ExistingFile)
    # The shortcut can fire while another app is active; make sure the dialog is not hidden behind it.
    dlg.setWindowFlag(Qt.WindowType.WindowStaysOnTopHint, True)
    dlg.show()
    dlg.raise_()
    dlg.activateWindow()
    if dlg.exec() and dlg.selectedFiles():
        return dlg.selectedFiles()[0]
    return None
