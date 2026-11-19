"""Where the Notion connection keeps its state: the home page, section pages, linked notes and database mappings.

Small values live in MC's settings table. The Notion text as of each note's last sync is kept as a snapshot file, so the
settings stay small. The token never goes here: it lives in the OS credential store.
"""
from __future__ import annotations

import hashlib
from pathlib import Path

from .client import NotionError


def sha(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


class NotionStore:
    def __init__(self, settings, snapshot_dir: Path):
        self.settings = settings
        self.snapshot_dir = Path(snapshot_dir)

    # home page and sections --------------------------------------------------------------
    def home(self) -> dict | None:
        return self.settings.get("notion_home") or None

    def set_home(self, home: dict | None) -> None:
        self.settings.set("notion_home", home or {})
        self.settings.set("notion_sections", {})          # sections live under the old home page

    def section(self, key: str) -> str | None:
        return (self.settings.get("notion_sections") or {}).get(key)

    def set_section(self, key: str, page_id: str) -> None:
        self.settings.set("notion_sections", {**(self.settings.get("notion_sections") or {}), key: page_id})

    # database destinations -----------------------------------------------------------------
    def destination(self, key: str) -> dict | None:
        return (self.settings.get("notion_destinations") or {}).get(key)

    def set_destination(self, key: str, dest: dict | None) -> None:
        cur = dict(self.settings.get("notion_destinations") or {})
        if dest is None:
            cur.pop(key, None)
        else:
            cur[key] = dest
        self.settings.set("notion_destinations", cur)

    # links between local files and Notion pages -----------------------------------------------
    def link(self, file_key: str) -> dict | None:
        return (self.settings.get("notion_links") or {}).get(file_key)

    def set_link(self, file_key: str, link: dict) -> None:
        self.settings.set("notion_links", {**(self.settings.get("notion_links") or {}), file_key: link})

    def file_for_page(self, page_id: str) -> str | None:
        return next((k for k, v in (self.settings.get("notion_links") or {}).items() if v.get("page_id") == page_id), None)

    # snapshots --------------------------------------------------------------------------------
    def _snap_path(self, file_key: str) -> Path:
        return self.snapshot_dir / (hashlib.sha256(file_key.encode()).hexdigest()[:32] + ".md")

    def save_snapshot(self, file_key: str, markdown: str) -> None:
        self.snapshot_dir.mkdir(parents=True, exist_ok=True)
        p = self._snap_path(file_key)
        tmp = p.with_suffix(".md.tmp")
        tmp.write_text(markdown, encoding="utf-8", newline="")
        tmp.replace(p)

    def load_snapshot(self, file_key: str) -> str | None:
        try:
            return self._snap_path(file_key).read_text(encoding="utf-8")
        except OSError:
            return None

    def save_backup(self, file_key: str, markdown: str, stamp: int) -> Path:
        d = self.snapshot_dir / "backups"
        d.mkdir(parents=True, exist_ok=True)
        p = d / f"{hashlib.sha256(file_key.encode()).hexdigest()[:16]}-{stamp}.md"
        p.write_text(markdown, encoding="utf-8", newline="")
        return p


class MemoryTokens:
    """Token storage for tests (and a stand-in when told not to use the OS store)."""

    def __init__(self):
        self.value: str | None = None
        self.fail = False

    def _check(self):
        if self.fail:
            raise NotionError("The system credential store is locked.")

    def get(self) -> str | None:
        self._check()
        return self.value

    def set(self, value: str) -> None:
        self._check()
        self.value = value

    def delete(self) -> None:
        self._check()
        self.value = None


_SERVICE, _USER = "MarkdownCompanionV2", "notion-token"


class KeyringTokens:
    """The OS credential store (Windows Credential Manager / macOS Keychain / Secret Service). No plaintext fallback."""

    @staticmethod
    def _kr():
        try:
            import keyring
            return keyring
        except Exception:
            raise NotionError("The 'keyring' package is not installed, so there is nowhere secure to keep the Notion token. "
                              "Install it with:  pip install keyring  (it is listed in requirements.txt), then restart the app.") from None

    def get(self) -> str | None:
        try:
            return self._kr().get_password(_SERVICE, _USER)
        except NotionError:
            raise
        except Exception as e:
            raise NotionError(f"Could not read the token from the system credential store: {e}") from None

    def set(self, value: str) -> None:
        try:
            self._kr().set_password(_SERVICE, _USER, value)
        except NotionError:
            raise
        except Exception as e:
            raise NotionError(f"Could not save the token in the system credential store ({type(e).__name__}: {e}). "
                              "On Linux this needs a running keyring service such as GNOME Keyring or KWallet.") from None

    def delete(self) -> None:
        try:
            self._kr().delete_password(_SERVICE, _USER)
        except NotionError:
            raise
        except Exception:
            pass          # nothing stored is not an error
