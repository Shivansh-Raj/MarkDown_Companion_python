"""Linked copies: pulling a Notion page into a local file and tracking what changed since.

A link remembers the local text and the Notion Markdown as they were at the last sync (as hashes, plus the Notion text
as a snapshot file). Refresh only ever changes the local file, and only when it has no unsynced edits. Push sends one
targeted edit after a preview, and only when Notion has not changed since the last sync.
"""
from __future__ import annotations

import os
import time
from dataclasses import dataclass
from pathlib import Path

from ..ai.create import write_new_file
from ..services.synchronization import SynchronizationService, ToggleStatus, read_snapshot
from ..utils.paths import normalize_key
from .client import Client, Created, NotionError, parse_page_id
from .edit import single_edit
from .store import NotionStore, sha

IN_SYNC, LOCAL_CHANGED, REMOTE_CHANGED, BOTH = "in_sync", "local_changed", "remote_changed", "both"


def sync_state(link: dict, local_hash: str, remote_hash: str) -> str:
    """What changed since the last sync, judged by content hashes (not timestamps)."""
    local, remote = local_hash != link["local_hash"], remote_hash != link["remote_hash"]
    return BOTH if local and remote else LOCAL_CHANGED if local else REMOTE_CHANGED if remote else IN_SYNC


def read_local(path: str) -> tuple[str, str]:
    """(text with LF line endings and no BOM, raw digest) of a local file."""
    snap = read_snapshot(path)
    return snap.text.lstrip("﻿").replace("\r\n", "\n").replace("\r", "\n"), snap.digest


def _now() -> int:
    return int(time.time())


@dataclass
class Pulled:
    file_key: str
    path: str
    title: str
    unknown_blocks: int
    existing: bool        # the page was already linked, so that file is opened instead of making a duplicate


def pull(client: Client, store: NotionStore, directory: str | os.PathLike, page: str) -> Pulled:
    """Fetches a page and writes it as a new local file, then records the link."""
    pid = parse_page_id(page)
    if not pid:
        raise NotionError("That is not a Notion page link or id.")
    key = store.file_for_page(pid)
    link = store.link(key) if key else None
    if key and link and os.path.exists(key):
        return Pulled(key, key, link.get("title", ""), 0, True)
    md = client.read_page(pid)
    if md.truncated:
        raise NotionError("This page is too large, or part of it is not shared with the connection, so it cannot be opened safely. Open it in Notion.")
    info = client.page_info(pid)
    path = write_new_file(directory, info.title, md.markdown)
    key = normalize_key(path)
    store.save_snapshot(key, md.markdown)
    store.set_link(key, {"page_id": pid, "url": info.url, "title": info.title, "local_hash": sha(md.markdown),
                         "remote_hash": sha(md.markdown), "synced": _now()})
    return Pulled(key, str(path), info.title, len(md.unknown_block_ids), False)


@dataclass
class LinkInfo:
    page_id: str
    url: str
    title: str
    synced: int
    local_changed: bool


def link_info(store: NotionStore, file_key: str, path: str) -> LinkInfo | None:
    link = store.link(file_key)
    if not link:
        return None
    text, _ = read_local(path)
    return LinkInfo(link["page_id"], link.get("url", ""), link.get("title", ""), link.get("synced", 0), sha(text) != link["local_hash"])


@dataclass
class Refreshed:
    state: str
    updated: bool


def refresh(client: Client, store: NotionStore, sync: SynchronizationService, file_key: str, path: str) -> Refreshed:
    """Brings Notion's changes into the local file, but only when the local file is unchanged since the last sync."""
    link = store.link(file_key)
    if not link:
        raise NotionError("This file is not linked to a Notion page.")
    local, digest = read_local(path)
    remote = client.read_page(link["page_id"])
    if remote.truncated:
        raise NotionError("The Notion page is too large, or partly unshared, to compare safely.")
    state = sync_state(link, sha(local), sha(remote.markdown))
    if state != REMOTE_CHANGED:
        return Refreshed(state, False)
    res = sync.save_content(path, digest, remote.markdown)
    if res.status is ToggleStatus.CONFLICT:
        raise NotionError("The file changed while refreshing. Nothing was overwritten; try again.")
    if res.status is not ToggleStatus.OK:
        raise NotionError(res.message or "Could not update the local file.")
    store.save_snapshot(file_key, remote.markdown)
    store.set_link(file_key, {**link, "local_hash": sha(remote.markdown), "remote_hash": sha(remote.markdown), "synced": _now()})
    return Refreshed(state, True)


@dataclass
class PushPlan:
    kind: str                       # nothing_to_send | remote_changed | ready
    both: bool = False
    old_str: str = ""
    new_str: str = ""
    local_hash: str = ""


def _base(store: NotionStore, file_key: str, link: dict) -> str:
    base = store.load_snapshot(file_key)
    if base is None:
        raise NotionError("The saved copy from the last sync is missing, so changes cannot be compared safely. Refresh from Notion first.")
    if sha(base) != link["remote_hash"]:
        raise NotionError("The saved copy from the last sync does not match its record. Refresh from Notion first.")
    return base


def plan_push(client: Client, store: NotionStore, file_key: str, path: str) -> PushPlan:
    """What a push would do. Reads only; nothing is changed in Notion."""
    link = store.link(file_key)
    if not link:
        raise NotionError("This file is not linked to a Notion page.")
    local, _ = read_local(path)
    remote = client.read_page(link["page_id"])
    if remote.truncated:
        raise NotionError("The Notion page is too large, or partly unshared, to update safely. Edit it in Notion.")
    state = sync_state(link, sha(local), sha(remote.markdown))
    if state == IN_SYNC:
        return PushPlan("nothing_to_send")
    if state in (REMOTE_CHANGED, BOTH):
        return PushPlan("remote_changed", both=state == BOTH)
    edit = single_edit(_base(store, file_key, link), local)
    return PushPlan("nothing_to_send") if edit is None else PushPlan("ready", old_str=edit.old_str, new_str=edit.new_str, local_hash=sha(local))


def push(client: Client, store: NotionStore, file_key: str, path: str, expected_local_hash: str) -> list[str]:
    """Sends the previewed edit after re-checking that neither side changed, and saving the Notion text to a backup first.
    Returns Notion's warnings, if any."""
    plan = plan_push(client, store, file_key, path)
    if plan.kind != "ready":
        raise NotionError("Notion or this note changed since the preview. Nothing was sent; review it again.")
    if plan.local_hash != expected_local_hash:
        raise NotionError("This note changed since the preview. Nothing was sent; review it again.")
    link = store.link(file_key)
    base = _base(store, file_key, link)
    try:
        store.save_backup(file_key, base, _now())
    except OSError as e:
        raise NotionError(f"Could not save a backup, so nothing was sent: {e}") from None
    updated = client.update_content(link["page_id"], plan.old_str, plan.new_str)
    if updated.truncated:
        raise NotionError("Notion applied the edit, but the page is too large to confirm it. Check it in Notion.")
    store.save_snapshot(file_key, updated.markdown)
    store.set_link(file_key, {**link, "local_hash": plan.local_hash, "remote_hash": sha(updated.markdown), "synced": _now()})
    return updated.warnings


def link_created(client: Client, store: NotionStore, file_key: str, local_content: str, created: Created, title: str) -> bool:
    """After a page was created from a local file: read it back (this also checks Notion accepted the Markdown) and record
    the link. False when the read-back failed, in which case no link is made."""
    try:
        back = client.read_page(created.id)
        store.save_snapshot(file_key, back.markdown)
    except (NotionError, OSError):
        return False
    text = local_content.lstrip("﻿").replace("\r\n", "\n").replace("\r", "\n")
    store.set_link(file_key, {"page_id": parse_page_id(created.id) or created.id, "url": created.url, "title": title,
                              "local_hash": sha(text), "remote_hash": sha(back.markdown), "synced": _now()})
    return True
