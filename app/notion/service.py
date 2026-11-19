"""The Notion connection as the UI sees it: one object holding the token, the saved state and the operations.

Every method that talks to Notion is blocking; the UI runs them on a worker thread (see `worker.py`).
"""
from __future__ import annotations

import os
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable

from ..services.synchronization import SynchronizationService
from ..utils.paths import app_data_dir
from . import dest, home, sync, tasks
from .client import Client, Http, Network, NotionError, PageHit, clean_token
from .db import Schema, Template, template_for
from .store import KeyringTokens, MemoryTokens, NotionStore


@dataclass
class Sent:
    id: str
    url: str
    linked: bool
    warnings: list[str]


class NotionService:
    def __init__(self, settings, tokens=None, http: Http | None = None, snapshot_dir: Path | None = None,
                 sync_service: SynchronizationService | None = None, sleep: Callable[[float], None] = time.sleep):
        self.settings = settings
        # No OS credential store (tests set MDC_NO_KEYRING) means the token is kept in memory only, never in plaintext.
        self.tokens = tokens or (MemoryTokens() if os.environ.get("MDC_NO_KEYRING") == "1" else KeyringTokens())
        self.http = http or Network()
        self.store = NotionStore(settings, snapshot_dir or app_data_dir() / "notion-snapshots")
        self.sync = sync_service or SynchronizationService()
        self._sleep = sleep
        self._token: str | None | bool = False        # False = not read yet

    # connection ---------------------------------------------------------------------------------
    def token(self) -> str | None:
        if self._token is False:
            self._token = self.tokens.get()
        return self._token or None

    def connected(self) -> bool:
        try:
            return self.token() is not None
        except NotionError:
            return False

    def client(self) -> Client:
        t = self.token()
        if not t:
            raise NotionError("Connect Notion first (Settings > Notion).")
        return Client(self.http, t, self._sleep)

    def connect(self, raw: str) -> tuple[str, str]:
        """Checks the token with Notion, then stores it. A rejected token is never stored."""
        t = clean_token(raw)
        me = Client(self.http, t, self._sleep).me()
        self.tokens.set(t)
        self._token = t
        return me

    def disconnect(self) -> None:
        self.tokens.delete()
        self._token = None

    def test(self) -> tuple[str, str]:
        return self.client().me()

    # finding things -------------------------------------------------------------------------------
    def search_pages(self, query: str) -> list[PageHit]:
        return self.client().search_pages(query)

    def search_databases(self, query: str) -> list[PageHit]:
        return self.client().search_data_sources(query)

    def schema(self, data_source: str) -> Schema:
        return self.client().data_source_schema(data_source)

    # home page ------------------------------------------------------------------------------------
    def home(self) -> dict | None:
        return self.store.home()

    def set_home(self, page: str) -> dict:
        return home.set_home(self.client(), self.store, page)

    def clear_home(self) -> None:
        self.store.set_home(None)

    # sending, pulling, syncing -----------------------------------------------------------------------
    def create_page(self, parent: str, title: str, markdown: str, file_key: str | None = None, content: str = "",
                    section: str | None = None) -> Sent:
        """A new page under `parent`, or under the home page's section. Always a new page; nothing existing is changed."""
        c = self.client()
        if section:
            parent = home.ensure_section(c, self.store, section)
        made = c.create_page(parent, title, markdown)
        linked = bool(file_key) and sync.link_created(c, self.store, file_key, content, made, title.strip())
        return Sent(made.id, made.url, linked, [])

    def pull(self, page: str, directory: str | os.PathLike) -> sync.Pulled:
        return sync.pull(self.client(), self.store, directory, page)

    def link_info(self, file_key: str, path: str):
        return sync.link_info(self.store, file_key, path)

    def refresh(self, file_key: str, path: str) -> sync.Refreshed:
        return sync.refresh(self.client(), self.store, self.sync, file_key, path)

    def plan_push(self, file_key: str, path: str) -> sync.PushPlan:
        return sync.plan_push(self.client(), self.store, file_key, path)

    def push(self, file_key: str, path: str, expected_hash: str) -> list[str]:
        return sync.push(self.client(), self.store, file_key, path, expected_hash)

    # database destinations ------------------------------------------------------------------------
    def destination(self, key: str) -> dict | None:
        return self.store.destination(key)

    def set_destination(self, key: str, data_source: str, mapping: list[dict]) -> dict:
        return dest.set_destination(self.client(), self.store, key, data_source, mapping)

    def clear_destination(self, key: str) -> None:
        self.store.set_destination(key, None)

    def template(self, key: str) -> Template:
        return template_for(key, (self.store.destination("meetings") or {}).get("data_source_id"))

    def create_destination(self, key: str) -> dict:
        return dest.create_destination(self.client(), self.store, key)

    def preview_row(self, key: str, title: str, values: dict[str, Any]) -> dest.RowPreview:
        return dest.preview_row(self.client(), self.store, key, title, values)

    def create_row(self, key: str, title: str, markdown: str, values: dict[str, Any],
                   file_key: str | None = None, content: str = "") -> Sent:
        c = self.client()
        made = dest.create_row(c, self.store, key, title, markdown, values)
        linked = False
        if file_key:
            from .client import Created
            linked = sync.link_created(c, self.store, file_key, content, Created(made.id, made.url), title.strip())
        return Sent(made.id, made.url, linked, made.warnings)

    # tasks -----------------------------------------------------------------------------------------
    def open_tasks(self) -> tasks.TasksView:
        return tasks.open_tasks(self.client(), self.store)

    def set_task_status(self, page: str, expect: str | None, to: str) -> str:
        return tasks.set_status(self.client(), self.store, page, expect, to)

    def briefing(self, topic: str):
        from .briefing import briefing
        return briefing(self.client(), self.store, topic)

    # asking ----------------------------------------------------------------------------------------
    def page_text(self, page: str) -> tuple[str, bool]:
        """A page's Markdown for asking about it (unknown-block tags removed) and whether Notion returned only part of it."""
        from .askdb import strip_unknown
        p = self.client().read_page(page)
        return strip_unknown(p.markdown).strip(), p.truncated

    def db_pages(self, data_source: str, question: str) -> dict:
        from .askdb import pages_for
        return pages_for(self.client(), data_source, question)
