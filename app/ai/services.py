"""Shared AI helpers for windows: API key storage, history, save folder, animation preference."""
from __future__ import annotations

import os
from pathlib import Path

from ..utils.logging_config import get_logger
from .create import default_save_dir

log = get_logger("ai")
_SERVICE, _USER = "MarkdownCompanionV2", "groq-api-key"


def _keyring():
    """The OS keychain (Windows Credential Manager / macOS Keychain / Secret Service), if usable."""
    if os.environ.get("MDC_NO_KEYRING") == "1":
        return None
    try:
        import keyring
        return keyring
    except Exception:
        return None


class AiServices:
    def __init__(self, settings):
        self.settings = settings
        from . import gateway
        gateway.configure(lambda: gateway.load_config(settings))

    # Which AI answers chat requests: Groq, or the company gateway. Speech-to-text always uses Groq's key (see api_key).
    def provider(self) -> str:
        return self.settings.get("ai_provider")

    def chat_key(self) -> str:
        """What to hand a chat worker: the Groq key, or a stand-in meaning "use the company AI" (its own checks give clear errors)."""
        from .gateway import GATEWAY_KEY
        return GATEWAY_KEY if self.provider() == "gateway" else self.api_key()

    # API key: OS keychain when available, otherwise the local settings database -------------
    def api_key(self) -> str:
        kr = _keyring()
        if kr is not None:
            try:
                value = kr.get_password(_SERVICE, _USER)
                if value:
                    return value
            except Exception:
                log.warning("keychain read failed; using settings fallback", exc_info=True)
        return self.settings.get("groq_api_key")

    def set_api_key(self, value: str) -> None:
        value = value.strip()
        kr = _keyring()
        if kr is not None:
            try:
                if value:
                    kr.set_password(_SERVICE, _USER, value)
                else:
                    try:
                        kr.delete_password(_SERVICE, _USER)
                    except Exception:
                        pass
                self.settings.set("groq_api_key", "")      # never keep a second plaintext copy
                return
            except Exception:
                log.warning("keychain write failed; using settings fallback", exc_info=True)
        self.settings.set("groq_api_key", value)

    # history ---------------------------------------------------------------------------------
    def history(self) -> list[str]:
        return list(self.settings.get("ai_history"))

    def push_history(self, entry: str) -> list[str]:
        entry = entry.strip()
        if not entry:
            return self.history()
        new = [entry] + [h for h in self.history() if h != entry]
        self.settings.set("ai_history", new[:8])
        return self.history()

    # misc ------------------------------------------------------------------------------------
    def save_dir(self) -> Path:
        custom = self.settings.get("ai_save_dir")
        return Path(custom) if custom else default_save_dir()

    def animations(self) -> bool:
        return bool(self.settings.get("animations"))

    def theme_name(self) -> str:
        return self.settings.get("theme")
