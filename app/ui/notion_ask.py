"""A window for asking about a Notion page or database (the Ask panel, outside a note window)."""
from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtWidgets import QDialog, QVBoxLayout

from .ask_panel import AskPanel


class NotionAskDialog(QDialog):
    def __init__(self, ai, parent=None):
        super().__init__(parent)
        self.setWindowFlag(Qt.WindowType.WindowStaysOnTopHint, True)
        self.setWindowTitle("Markdown Companion - Ask Notion")
        self.resize(520, 620)
        self.panel = AskPanel(ai, self)
        self.panel.closed.connect(self.close)
        lay = QVBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.addWidget(self.panel)

    def closeEvent(self, e) -> None:  # noqa: N802
        self.panel.cancel_worker()
        super().closeEvent(e)
