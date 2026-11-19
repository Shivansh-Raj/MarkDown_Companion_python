"""The "Markdown Companion" home page and the section pages under it (Notes, Meetings, ...).

The user picks the home page once. Sections are created the first time they are needed, and an existing child page with
the same title is reused, so nothing is duplicated even if the saved record is lost.
"""
from __future__ import annotations

from .client import Client, NotionError, parse_page_id
from .store import NotionStore

SECTIONS = {"notes": "Notes", "meetings": "Meetings", "practice": "Practice sessions", "answers": "AI answers"}


def set_home(client: Client, store: NotionStore, page: str) -> dict:
    """Checks the page is visible to the connection, then makes it the home page."""
    info = client.page_info(page)
    home = {"page_id": parse_page_id(info.id) or info.id, "title": info.title, "url": info.url}
    store.set_home(home)
    return home


def ensure_section(client: Client, store: NotionStore, key: str) -> str:
    """The page id for a section, creating it under the home page if it does not exist yet."""
    title = SECTIONS.get(key)
    if title is None:
        raise NotionError("Unknown section.")
    home = store.home()
    if not home:
        raise NotionError("Choose a home page first (Settings > Notion).")
    saved = store.section(key)
    if saved and client.page_exists(saved):
        return saved
    pid = client.find_child_page(home["page_id"], title) or client.create_empty_page(home["page_id"], title).id
    store.set_section(key, pid)
    return pid
