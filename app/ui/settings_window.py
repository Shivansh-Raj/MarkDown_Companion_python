"""Settings dialog."""
from __future__ import annotations

import sys

from PySide6.QtCore import QSize, Qt, QTimer, Signal
from PySide6.QtGui import QGuiApplication, QKeySequence
from PySide6.QtWidgets import (QCheckBox, QComboBox, QDialog, QFileDialog, QFormLayout, QHBoxLayout, QLayout, QKeySequenceEdit,
                               QLabel, QLineEdit, QPushButton, QScrollArea, QSlider, QVBoxLayout, QWidget)

from ..ai.worker import KeyTestWorker

from ..core.settings_manager import SettingsManager
from ..markdown.theme import list_themes
from . import scale
from .fonts import use_body_font, use_pixel_font


def _seq_text(edit: QKeySequenceEdit) -> str:
    return edit.keySequence().toString(QKeySequence.SequenceFormat.PortableText)


class SettingsWindow(QDialog):
    applied = Signal()

    def __init__(self, settings: SettingsManager, shortcut_status: str = "", ai=None, notion=None):
        super().__init__()
        use_body_font(self, 12)                      # same text look as the Markdown view (and follows the Text size setting)
        self.settings = settings
        self.ai = ai
        self.notion = notion
        self._test: KeyTestWorker | None = None
        self.setWindowTitle("Markdown Companion V2 - Settings")
        self.setWindowFlag(Qt.WindowType.WindowStaysOnTopHint, True)
        form = QFormLayout()
        self._form = form

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
        self.font_scale = QSlider(Qt.Orientation.Horizontal)
        self.font_scale.setRange(60, 200)
        self.font_scale.setSingleStep(10)
        self.font_scale.setToolTip("Scales all text in the app: buttons, labels, lists and the Markdown view. Applies when you press Apply.")
        self.font_scale.setValue(int(settings.get("font_scale")))
        self._font_label = QLabel(f"{self.font_scale.value()}%")
        self.font_scale.valueChanged.connect(lambda v: self._font_label.setText(f"{v}%"))
        frow = QHBoxLayout()
        frow.addWidget(self.font_scale, 1)
        frow.addWidget(self._font_label)
        form.addRow("Text size:", frow)

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
        self.hide_dock = QCheckBox("macOS: hide the Dock icon (menu bar only)")
        self.hide_dock.setChecked(settings.get("hide_dock_icon"))
        form.addRow("", self.show_home)
        if sys.platform == "darwin":
            form.addRow("", self.hide_dock)
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
            # Which AI answers chat requests. Speech-to-text (voice) always uses Groq, so keep a Groq key for recordings.
            self.provider = QComboBox()
            self.provider.addItem("Groq", "groq")
            self.provider.addItem("Company AI (gateway)", "gateway")
            self.provider.setCurrentIndex(max(0, self.provider.findData(settings.get("ai_provider"))))
            self.provider.currentIndexChanged.connect(self._provider_changed)
            form.addRow("AI provider:", self.provider)
            from ..ai import gateway as gw
            env = gw.load_config(settings)
            self.gw_auth = QLineEdit(settings.get("gateway_auth_url"))
            self.gw_auth.setPlaceholderText(env.auth_url or "Authentication URL (…/auth)")
            self.gw_model_url = QLineEdit(settings.get("gateway_model_url"))
            self.gw_model_url.setPlaceholderText(env.model_url or "Model URL (…/model-as-a-service)")
            self.gw_model = QLineEdit(settings.get("gateway_model"))
            self.gw_model.setPlaceholderText(env.model or gw.DEFAULT_MODEL)
            self.gw_verify = QCheckBox("Verify the server's certificate (turn off only behind a re-signing company proxy)")
            self.gw_verify.setChecked(settings.get("gateway_verify_tls"))
            self.gw_test = QPushButton("Test connection")
            self.gw_test.clicked.connect(self._test_gateway)
            self.gw_msg = QLabel("The client id and secret are read from the .env file (CLIENT_ID, CLIENT_SECRET). They are never saved in the "
                                 "app's settings. Leave a field empty to use DAIA_AUTH_URL, DAIA_MODEL_URL or DAIA_MODEL from .env.")
            self.gw_msg.setWordWrap(True)
            self.gw_msg.setObjectName("hint")
            self.gw_rows = [("Company AI auth URL:", self.gw_auth), ("Company AI model URL:", self.gw_model_url), ("Company AI model:", self.gw_model),
                            ("", self.gw_verify), ("", self.gw_test), ("", self.gw_msg)]
            for label, wdg in self.gw_rows:
                form.addRow(label, wdg)
            self._provider_changed()
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

        if notion is not None:
            from .notion_settings import NotionSettings
            self.notion_section = NotionSettings(notion)
            form.addRow(self.notion_section)

        apply_btn = QPushButton("Apply")
        apply_btn.clicked.connect(self.apply)
        close_btn = QPushButton("Close")
        close_btn.clicked.connect(self.close)
        buttons = QHBoxLayout()
        buttons.addStretch(1)
        buttons.addWidget(apply_btn)
        buttons.addWidget(close_btn)
        # The settings can be taller than the screen (the Notion block alone is long), so they scroll while Apply / Close stay put.
        self._content = QWidget()
        self._content.setLayout(form)
        self._scroll = QScrollArea()
        self._scroll.setWidgetResizable(True)
        self._scroll.setFrameShape(QScrollArea.Shape.NoFrame)
        self._scroll.viewport().setAutoFillBackground(False)
        self._scroll.setStyleSheet("QScrollArea { background: transparent; border: none; }")
        self._content.setObjectName("settingscontent")
        self._content.setStyleSheet("QWidget#settingscontent { background: transparent; }")
        self._scroll.setWidget(self._content)
        root = QVBoxLayout(self)
        root.addWidget(self._scroll, 1)
        root.addLayout(buttons)
        for i in range(form.rowCount()):                 # row labels in the pixel font, like the Markdown view's headings
            item = form.itemAt(i, QFormLayout.ItemRole.LabelRole)
            if item is not None and item.widget() is not None:
                use_pixel_font(item.widget(), 11)
        # The dialog sizes itself from its content (so larger text is never cut off); the strut keeps a sensible minimum width.
        self._strut = QWidget()
        self._strut.setFixedHeight(0)
        self._strut.setMinimumWidth(scale.px(500))
        root.addWidget(self._strut)
        self.resize(self.sizeHint())

    def sizeHint(self) -> QSize:  # noqa: N802
        """The content's size, but never taller than the screen: beyond that the settings scroll."""
        content = self._content.sizeHint()
        bar = self._scroll.verticalScrollBar().sizeHint().width()
        screen = QGuiApplication.primaryScreen()
        limit = int(screen.availableGeometry().height() * 0.9) if screen else 800
        buttons = 60
        return QSize(max(content.width() + bar + 24, scale.px(500)), min(content.height() + buttons + 24, limit))

    def rescale(self) -> None:
        """Text size changed: keep the minimum width in proportion and grow the window to fit its larger content."""
        self._strut.setMinimumWidth(scale.px(500))
        self._content.updateGeometry()
        self.layout().invalidate()
        QTimer.singleShot(0, self._fit)

    def _fit(self) -> None:
        hint = self.sizeHint()
        self.resize(max(self.width(), hint.width()), max(self.height(), hint.height()))

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

    def _provider_changed(self) -> None:
        show = self.provider.currentData() == "gateway"
        for _label, wdg in self.gw_rows:
            wdg.setVisible(show)
        for _label, wdg in self.gw_rows:
            lab = self._form.labelForField(wdg)
            if lab is not None:
                lab.setVisible(show)
        self.key_msg.setText("Recordings are still turned into text by Groq, so keep a Groq key if you use Voice." if show else "")

    def _gateway_config(self):
        from ..ai import gateway as gw
        env = gw.load_config(self.settings)
        return gw.GatewayConfig(self.gw_auth.text().strip().rstrip("/") or env.auth_url, self.gw_model_url.text().strip().rstrip("/") or env.model_url,
                                self.gw_model.text().strip() or env.model, env.client_id, env.client_secret, self.gw_verify.isChecked())

    def _test_gateway(self) -> None:
        from ..ai import gateway as gw
        from ..notion.worker import run_async
        cfg = self._gateway_config()
        self.gw_test.setEnabled(False)
        self.gw_msg.setText("Testing…")
        gw.forget_token()
        run_async(self, lambda: gw.test_connection(cfg),
                  lambda model: (self.gw_test.setEnabled(True), self.gw_msg.setText(f"Works. Will use {model}.")),
                  lambda m: (self.gw_test.setEnabled(True), self.gw_msg.setText(m)))

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
        s.set("font_scale", self.font_scale.value())
        s.set("theme", self.theme.currentData())
        s.set("compact_default", self.compact.isChecked())
        s.set("restore_windows", self.restore.isChecked())
        s.set("show_home", self.show_home.isChecked())
        s.set("hide_dock_icon", self.hide_dock.isChecked())
        s.set("button_shadow", self.button_shadow.isChecked())
        s.set("shortcut_primary", _seq_text(self.primary))
        s.set("shortcut_home", _seq_text(self.home_seq))
        s.set("shortcut_picker", _seq_text(self.picker))
        if self.ai is not None:
            s.set("ai_save_dir", self._custom_dir)
            s.set("animations", self.animations.isChecked())
            s.set("ai_provider", self.provider.currentData())
            s.set("gateway_auth_url", self.gw_auth.text().strip())
            s.set("gateway_model_url", self.gw_model_url.text().strip())
            s.set("gateway_model", self.gw_model.text().strip())
            s.set("gateway_verify_tls", self.gw_verify.isChecked())
            from ..ai import gateway as gw
            gw.forget_token()
        self.applied.emit()
