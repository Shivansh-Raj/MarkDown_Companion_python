"""A meeting briefing (port of V1's briefing.rs): earlier meetings matching a topic, the decisions written in them and the open
tasks linked to them. Read-only, built from the user's own Meetings and Tasks databases, with no AI involved."""
from __future__ import annotations

import re
from dataclasses import dataclass, field

from .client import Client, NotionError, page_hit, parse_page_id
from .store import NotionStore
from .tasks import NotionTask, open_tasks

MAX_MEETINGS, MAX_DECISIONS, MAX_TASKS = 3, 5, 10


@dataclass
class BriefMeeting:
    title: str
    date: str
    url: str
    decisions: list[str]


@dataclass
class Briefing:
    database: str
    meetings: list[BriefMeeting]
    latest_instead: bool = False            # no meeting matched the topic, so the latest ones are shown
    tasks: list[NotionTask] = field(default_factory=list)
    tasks_note: str | None = None


def _text(v: dict | None) -> str:
    if not isinstance(v, dict):
        return ""
    t = v.get("type")
    parts = v.get("title") if t == "title" else v.get("rich_text") if t == "rich_text" else None
    if isinstance(parts, list):
        return "".join(p.get("plain_text", "") for p in parts if isinstance(p, dict))
    return (v.get("select") or {}).get("name", "") if isinstance(v.get("select"), dict) else ""


def extract_decisions(md: str) -> list[str]:
    """Bullets under headings about decisions, agreements or takeaways, with checkbox marks stripped."""
    out, in_section = [], False
    for line in md.splitlines():
        t = line.strip()
        if t.startswith("#"):
            h = t.lstrip("#").strip().lower()
            in_section = any(k in h for k in ("decision", "decided", "agreed", "agreement", "takeaway"))
            continue
        if not in_section:
            continue
        m = re.match(r"^(?:[-*+]|\d+\.)\s+(.*)$", t)
        if m:
            item = re.sub(r"^\[[ xX]\]\s*", "", m.group(1)).strip()
            if item and len(out) < MAX_DECISIONS:
                out.append(item)
    return out


def briefing(client: Client, store: NotionStore, topic: str) -> Briefing:
    dest = store.destination("meetings")
    if not dest:
        raise NotionError("Set up a Meetings database first (Settings > Notion).")
    rows = [r for r in client.query_recent(dest["data_source_id"]) if not r.get("in_trash")]
    mapped = lambda f: next((m["property"] for m in dest["mapping"] if m["field"] == f), None)   # noqa: E731
    project_col, date_col = mapped("project"), mapped("date")
    words = sorted({w for w in re.findall(r"\w+", topic.lower()) if len(w) >= 3})

    def title_of(r: dict) -> str:
        h = page_hit(r)
        return h.title if h and h.title != "Untitled" else ""

    def hay(r: dict) -> str:
        proj = _text((r.get("properties") or {}).get(project_col)) if project_col else ""
        return f"{title_of(r)} {proj}".lower()
    scored = [(i, sum(1 for w in words if w in hay(r))) for i, r in enumerate(rows)]
    matched = any(s > 0 for _, s in scored)
    if matched:
        scored = sorted((x for x in scored if x[1] > 0), key=lambda x: (-x[1], x[0]))
    chosen = [rows[i] for i, _ in scored[:MAX_MEETINGS]]
    meetings, ids = [], []
    for r in chosen:
        if not isinstance(r.get("id"), str):
            continue
        date = (((r.get("properties") or {}).get(date_col) or {}).get("date") or {}).get("start") if date_col else None
        date = (date or r.get("created_time") or "")[:10]
        try:
            decisions = extract_decisions(client.read_page(r["id"]).markdown)
        except NotionError:
            decisions = []
        pid = parse_page_id(r["id"])
        if pid:
            ids.append(pid)
        meetings.append(BriefMeeting(title_of(r) or "Untitled", date, r.get("url") or "", decisions))
    tasks, note = [], None
    if not store.destination("tasks"):
        note = "Set up a Tasks database to see open tasks here."
    else:
        try:
            view = open_tasks(client, store)
            if view.links_meetings:
                tasks = [t for t in view.rows if set(t.meeting_ids) & set(ids)][:MAX_TASKS]
            else:
                tasks, note = view.rows[:MAX_TASKS], "Tasks are not linked to meetings in your database, so these are all open tasks."
        except NotionError as e:
            note = str(e)
    return Briefing(dest["title"], meetings, not matched and bool(words), tasks, note)
