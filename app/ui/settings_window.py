"""Settings dialog."""
from __future__ import annotations

from PySide6.QtCore import Qt, Signal
from PySide6.QtGui import QKeySequence
from PySide6.QtWidgets import (QCheckBox, QComboBox, QDialog, QFormLayout, QHBoxLayout, QKeySequenceEdit,
                               QLabel, QPushButton, QSlider, QVBoxLayout)

from ..core.settings_manager import SettingsManager
from ..markdown.theme import list_themes


def _seq_text(edit: QKeySequenceEdit) -> str:
    return edit.keySequence().toString(QKeySequence.SequenceFormat.PortableText)


class SettingsWindow(QDialog):
    applied = Signal()

    def __init__(self, settings: SettingsManager, shortcut_status: str = ""):
        super().__init__()
        self.settings = settings
        self.setWindowTitle("Markdown Companion V2 - Settings")
        self.setWindowFlag(Qt.WindowType.WindowStaysOnTopHint, True)
        form = QFormLayout()

        self.primary = QKeySequenceEdit(QKeySequence(settings.get("shortcut_primary")))
        self.home_seq = QKeySequenceEdit(QKeySequence(settings.get("shortcut_home")))
        self.picker = QKeySequenceEdit(QKeySequence(settings.get("shortcut_picker")))
        form.addRow("Open / focus main file:", self.primary)
        form.addRow("Show home screen:", self.home_seq)
        form.addRow("Always open file picker:", self.picker)
        self.status = QLabel(shortcut_status)
        self.status.setWordWrap(True)
        form.addRow("", self.status)

        assoc = settings.get("associated_file")
        self.assoc_label = QLabel(assoc or "(none yet - chosen the first time you press the shortcut)")
        self.assoc_label.setWordWrap(True)
        clear = QPushButton("Forget")
        clear.clicked.connect(self._forget_assoc)
        row = QHBoxLayout()
        row.addWidget(self.assoc_label, 1)
        row.addWidget(clear)
        form.addRow("Main file:", row)

        self.opacity = QSlider(Qt.Orientation.Horizontal)
        self.opacity.setRange(30, 100)
        self.opacity.setValue(int(settings.get("opacity") * 100))
        form.addRow("Default opacity:", self.opacity)

        self.theme = QComboBox()
        for tid, name in list_themes().items():
            self.theme.addItem(name, tid)
        self.theme.setCurrentIndex(max(0, self.theme.findData(settings.get("theme"))))
        form.addRow("Theme:", self.theme)

        self.compact = QCheckBox("Open new windows rolled up (compact)")
        self.compact.setChecked(settings.get("compact_default"))
        self.restore = QCheckBox("Reopen last session's windows at startup")
        self.restore.setChecked(settings.get("restore_windows"))
        self.show_home = QCheckBox("Show the home screen at startup")
        self.show_home.setChecked(settings.get("show_home"))
        form.addRow("", self.show_home)
        form.addRow("", self.compact)
        form.addRow("", self.restore)

        apply_btn = QPushButton("Apply")
        apply_btn.clicked.connect(self.apply)
        close_btn = QPushButton("Close")
        close_btn.clicked.connect(self.close)
        buttons = QHBoxLayout()
        buttons.addStretch(1)
        buttons.addWidget(apply_btn)
        buttons.addWidget(close_btn)
        root = QVBoxLayout(self)
        root.addLayout(form)
        root.addLayout(buttons)
        self.setMinimumWidth(420)

    def _forget_assoc(self) -> None:
        self.settings.set("associated_file", "")
        self.assoc_label.setText("(none)")

    def set_shortcut_status(self, text: str) -> None:
        self.status.setText(text)

    def apply(self) -> None:
        s = self.settings
        s.set("opacity", self.opacity.value() / 100)
        s.set("theme", self.theme.currentData())
        s.set("compact_default", self.compact.isChecked())
        s.set("restore_windows", self.restore.isChecked())
        s.set("show_home", self.show_home.isChecked())
        s.set("shortcut_primary", _seq_text(self.primary))
        s.set("shortcut_home", _seq_text(self.home_seq))
        s.set("shortcut_picker", _seq_text(self.picker))
        self.applied.emit()
