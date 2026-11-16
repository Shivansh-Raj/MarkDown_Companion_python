"""Settings dialog."""
from __future__ import annotations

from PySide6.QtCore import Qt, Signal
from PySide6.QtGui import QKeySequence
from PySide6.QtWidgets import (QCheckBox, QComboBox, QDialog, QFileDialog, QFormLayout, QHBoxLayout, QKeySequenceEdit,
                               QLabel, QLineEdit, QPushButton, QSlider, QVBoxLayout)

from ..ai.worker import KeyTestWorker

from ..core.settings_manager import SettingsManager
from ..markdown.theme import list_themes


def _seq_text(edit: QKeySequenceEdit) -> str:
    return edit.keySequence().toString(QKeySequence.SequenceFormat.PortableText)


class SettingsWindow(QDialog):
    applied = Signal()

    def __init__(self, settings: SettingsManager, shortcut_status: str = "", ai=None):
        super().__init__()
        self.settings = settings
        self.ai = ai
        self._test: KeyTestWorker | None = None
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
        self.button_shadow = QCheckBox("V1-style button borders (outline + shadow)")
        self.button_shadow.setChecked(settings.get("button_shadow"))
        form.addRow("", self.show_home)
        form.addRow("", self.button_shadow)
        form.addRow("", self.compact)
        form.addRow("", self.restore)

        if ai is not None:
            self.key_edit = QLineEdit()
            self.key_edit.setEchoMode(QLineEdit.EchoMode.Password)
            self.key_edit.setPlaceholderText("Key saved - paste a new one to replace" if ai.api_key()
                                             else "Groq API key (gsk_...)")
            key_save = QPushButton("Save")
            key_save.clicked.connect(self._save_key)
            key_test = QPushButton("Test")
            key_test.clicked.connect(self._test_key)
            key_remove = QPushButton("Remove")
            key_remove.clicked.connect(self._remove_key)
            krow = QHBoxLayout()
            for wdg in (self.key_edit, key_save, key_test, key_remove):
                krow.addWidget(wdg, 1 if wdg is self.key_edit else 0)
            form.addRow("Groq API key:", krow)
            self.key_msg = QLabel("")
            self.key_msg.setWordWrap(True)
            self.key_msg.setObjectName("hint")
            form.addRow("", self.key_msg)
            self.save_dir = QLineEdit(str(ai.save_dir()))
            self.save_dir.setReadOnly(True)
            browse = QPushButton("Change")
            browse.clicked.connect(self._browse_dir)
            reset = QPushButton("Default")
            reset.clicked.connect(self._reset_dir)
            drow = QHBoxLayout()
            drow.addWidget(self.save_dir, 1)
            drow.addWidget(browse)
            drow.addWidget(reset)
            form.addRow("AI files save to:", drow)
            self._custom_dir = settings.get("ai_save_dir")
            self.animations = QCheckBox("Animations (typing cat, diff reveal)")
            self.animations.setChecked(settings.get("animations"))
            form.addRow("", self.animations)

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
        self.setMinimumWidth(520)

    # AI ----------------------------------------------------------------------------------------
    def _save_key(self) -> None:
        value = self.key_edit.text().strip()
        if value:
            self.ai.set_api_key(value)
            self.key_edit.clear()
            self.key_edit.setPlaceholderText("Key saved - paste a new one to replace")
            self.key_msg.setText("Key saved.")

    def _remove_key(self) -> None:
        self.ai.set_api_key("")
        self.key_edit.clear()
        self.key_edit.setPlaceholderText("Groq API key (gsk_...)")
        self.key_msg.setText("Key removed.")

    def _test_key(self) -> None:
        key = self.key_edit.text().strip() or self.ai.api_key()
        if not key:
            self.key_msg.setText("Enter a key first.")
            return
        if self._test is not None:
            return
        self.key_msg.setText("Testing...")
        w = KeyTestWorker(key, self)
        w.ok.connect(lambda model: self.key_msg.setText(f"Key works. Will use {model}."))
        w.failed.connect(self.key_msg.setText)
        w.finished.connect(self._test_done)
        self._test = w
        w.start()

    def _test_done(self) -> None:
        w, self._test = self._test, None
        if w is not None:
            w.deleteLater()

    def _browse_dir(self) -> None:
        picked = QFileDialog.getExistingDirectory(self, "Folder for AI-created files", self.save_dir.text())
        if picked:
            self._custom_dir = picked
            self.save_dir.setText(picked)

    def _reset_dir(self) -> None:
        from ..ai.create import default_save_dir
        self._custom_dir = ""
        self.save_dir.setText(str(default_save_dir()))

    def done(self, result: int) -> None:  # noqa: D401 - don't leave a request running after close
        if self._test is not None:
            self._test.wait(3000)
        super().done(result)

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
        s.set("button_shadow", self.button_shadow.isChecked())
        s.set("shortcut_primary", _seq_text(self.primary))
        s.set("shortcut_home", _seq_text(self.home_seq))
        s.set("shortcut_picker", _seq_text(self.picker))
        if self.ai is not None:
            s.set("ai_save_dir", self._custom_dir)
            s.set("animations", self.animations.isChecked())
        self.applied.emit()
