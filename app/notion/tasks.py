"""Open tasks from the Notion Tasks database, and setting a task's status (port of V1's notion/tasks.rs).

Reads use the field mapping chosen in Settings, so any database layout works. A task is "done" when its status is one of
the usual finished names; ticking one sets the mapped status property to such an option, only after checking that the
task still has the status the user saw. Nothing else on the page is touched.
"""
from __future__ import annotations

from dataclasses import dataclass, field

from .client import Client, NotionError, parse_page_id
from .db import Prop, Schema
from .store import NotionStore

DONE_NAMES = ("done", "complete", "completed", "finished", "closed")
MAX_PAGES = 3


def is_done_name(s: str) -> bool:
    return s.strip().lower() in DONE_NAMES


@dataclass
class NotionTask:
    id: str
    title: str
    due: str | None
    status: str | None
    owner: str | None
    url: str
    meeting_ids: list[str] = field(default_factory=list)      # the meeting pages this task is linked to (the mapped Meeting relation)


@dataclass
class TasksView:
    database: str
    rows: list[NotionTask] = field(default_factory=list)
    truncated: bool = False
    done_status: str | None = None          # the option that ticking a task sets
    read_only_reason: str | None = None     # why ticking is not available, if it is not
    links_meetings: bool = False            # whether the Meeting column is mapped, so tasks can be tied to meetings


@dataclass
class _Cols:
    title: Prop
    due: Prop | None
    status: Prop | None
    owner: Prop | None
    meeting: Prop | None = None


def _columns(schema: Schema, mapping: list[dict]) -> _Cols:
    def find(f: str) -> Prop | None:
        m = next((m for m in mapping if m["field"] == f), None)
        return next((p for p in schema.properties if m and p.name == m["property"]), None)
    title = next((p for p in schema.properties if p.type == "title"), None)
    if title is None:
        raise NotionError("The tasks database has no title column.")
    due, status = find("due"), find("status")
    return _Cols(title, due if due and due.type == "date" else None,
                 status if status and status.type in ("status", "select") else None, find("owner"),
                 (lambda m: m if m and m.type == "relation" else None)(find("meeting")))


def _plain(parts) -> str:
    return "".join(t.get("plain_text", "") for t in (parts or []) if isinstance(t, dict))


def _status_of(page: dict, col: Prop) -> str | None:
    v = ((page.get("properties") or {}).get(col.name) or {}).get(col.type) or {}
    return v.get("name") if isinstance(v, dict) else None


def _task_from(page: dict, c: _Cols) -> NotionTask | None:
    if not isinstance(page.get("id"), str) or page.get("in_trash") or page.get("archived"):
        return None
    props = page.get("properties") or {}
    title = _plain((props.get(c.title.name) or {}).get("title"))
    due = None
    if c.due:
        start = ((props.get(c.due.name) or {}).get("date") or {}).get("start")
        due = start[:10] if isinstance(start, str) else None
    owner = None
    if c.owner:
        p = props.get(c.owner.name) or {}
        if p.get("type") == "rich_text":
            owner = _plain(p.get("rich_text"))
        elif p.get("type") == "select":
            owner = (p.get("select") or {}).get("name")
    return NotionTask(page["id"], title if title.strip() else "Untitled", due, _status_of(page, c.status) if c.status else None,
                      owner if owner and owner.strip() else None, page.get("url") or "",
                      [i for i in (parse_page_id(str(r.get("id", ""))) for r in ((props.get(c.meeting.name) or {}).get("relation") or []) if isinstance(r, dict)) if i]
                      if c.meeting else [])


def _open_filter(status: Prop) -> dict | None:
    clauses = [{"property": status.name, status.type: {"does_not_equal": o}} for o in status.options if is_done_name(o)]
    return None if not clauses else clauses[0] if len(clauses) == 1 else {"and": clauses}


def _load(store: NotionStore) -> dict:
    dest = store.destination("tasks")
    if not dest:
        raise NotionError("Set up a Tasks database first (Settings > Notion).")
    return dest


def open_tasks(client: Client, store: NotionStore) -> TasksView:
    """The open tasks (up to 300), soonest due first."""
    dest = _load(store)
    cols = _columns(client.data_source_schema(dest["data_source_id"]), dest["mapping"])
    flt = _open_filter(cols.status) if cols.status else None
    rows: list[NotionTask] = []
    cursor, truncated = None, False
    for n in range(MAX_PAGES):
        results, cursor, incomplete = client.query_data_source(dest["data_source_id"], flt, cursor)
        rows += [t for t in (_task_from(r, cols) for r in results) if t]
        truncated = truncated or incomplete
        if cursor is None:
            break
        if n + 1 == MAX_PAGES:
            truncated = True
    rows = [t for t in rows if t.status is None or not is_done_name(t.status)]       # when no server-side filter was possible
    rows.sort(key=lambda t: (t.due is None, t.due or "", t.title.lower()))
    done = next((o for o in cols.status.options if is_done_name(o)), None) if cols.status else None
    reason = None
    if cols.status is None:
        reason = "Map a Status column in Settings > Notion > Tasks to tick tasks off here."
    elif done is None:
        reason = 'The Status column has no "Done" option, so tasks cannot be ticked off here.'
    return TasksView(dest["title"], rows, truncated, done, reason, cols.meeting is not None)


def set_status(client: Client, store: NotionStore, page: str, expect: str | None, to: str) -> str:
    """Sets a task's status to `to`, but only if its current status is still `expect` and `to` is a real option.
    Returns "done" or "stale" (changed in Notion since it was shown, or not in the Tasks database; nothing written)."""
    dest = _load(store)
    cols = _columns(client.data_source_schema(dest["data_source_id"]), dest["mapping"])
    if cols.status is None:
        raise NotionError("Map a Status column in Settings > Notion > Tasks first.")
    option = next((o for o in cols.status.options if o == to), None)
    if option is None:
        raise NotionError(f'"{to}" is not one of the Status options.')
    pid = parse_page_id(page)
    if not pid:
        raise NotionError("That is not a Notion page id.")
    current = client.get_page(pid)
    parent = current.get("parent") or {}
    mine = parse_page_id(dest["data_source_id"])
    in_db = any(parse_page_id(str(parent.get(k) or "")) == mine for k in ("data_source_id", "database_id"))
    if not in_db or current.get("in_trash") or _status_of(current, cols.status) != expect:
        return "stale"
    client.update_properties(pid, {cols.status.name: {cols.status.type: {"name": option}}})
    return "done"


GROUP_ORDER = ("overdue", "today", "upcoming", "none")
GROUP_LABELS = {"overdue": "OVERDUE", "today": "TODAY", "upcoming": "UPCOMING", "none": "NO DATE"}


def group_of(due: str | None, today: str) -> str:
    return "none" if not due else "overdue" if due < today else "today" if due == today else "upcoming"


def group_tasks(rows: list[NotionTask], today: str, query: str = "") -> list[tuple[str, list[NotionTask]]]:
    """Tasks grouped overdue, today, upcoming, no date; within a group by date, then title. `query` filters title, owner, status."""
    q = query.strip().lower()
    keep = [r for r in rows if not q or any(q in (s or "").lower() for s in (r.title, r.owner, r.status))]
    groups = {g: [] for g in GROUP_ORDER}
    for r in keep:
        groups[group_of(r.due, today)].append(r)
    return [(g, sorted(groups[g], key=lambda r: (r.due or "", r.title.lower()))) for g in GROUP_ORDER if groups[g]]
