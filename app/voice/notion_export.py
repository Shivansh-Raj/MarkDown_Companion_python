"""Where a voice session goes in Notion, and the values for its columns (port of V1's notionExport.ts). Qt-free."""
from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import datetime

from .analyses import ActionItem
from .templates import Section, _note   # noqa: PLC2701 - same section note format as the file export


@dataclass(frozen=True)
class NotionTarget:
    key: str            # destination key of the database: meetings | practice | notes
    section: str        # section page used when no database is set up
    label: str


def notion_target_for(meta) -> NotionTarget:
    if meta.mode == "practice":
        return NotionTarget("practice", "practice", "Practice sessions")
    if meta.mode == "meeting":
        return NotionTarget("meetings", "meetings", "Meetings")
    return NotionTarget("notes", "notes", "Notes")


def day_stamp(created_at_ms: int) -> str:
    return datetime.fromtimestamp(created_at_ms / 1000).strftime("%Y-%m-%d")


def notion_values(meta, extra: dict | None = None) -> dict:
    """Values for the mapped columns. The field keys match the database destinations in the Notion settings."""
    return {"date": day_stamp(meta.created_at_ms), "type": meta.mode, "duration_min": round(meta.duration_ms / 60000, 1),
            "tags": ["voice", meta.mode], "source": "Markdown Companion", **(extra or {})}


def practice_properties(a) -> dict:
    """Numbers from the local speaking analysis. Missing values are left out."""
    out: dict = {"words": a.word_count}
    if a.wpm is not None:
        out["pace_wpm"] = a.wpm
    if a.articulation_wpm is not None:
        out["speaking_wpm"] = a.articulation_wpm
    out["filler_count"] = a.filler_total
    out["fillers_per_100_words"] = round(a.fillers_per_100, 1)
    if a.pauses:
        out["long_pauses"] = a.pauses.long
    return out


def body_from_sections(sections: list[Section]) -> str:
    """Sections as the body of a Notion page: the title, date and length go in the page's title and properties instead."""
    return "\n".join(f"## {s.label}\n\n{s.markdown.strip()}\n{_note(s)}" for s in sections).strip() + "\n"


@dataclass
class TaskRow:
    key: str                  # the same wording in the same recording is the same task
    title: str
    body: str
    values: dict
    summary: str


_ISO_DAY = re.compile(r"^\d{4}-\d{2}-\d{2}$")


def _normalise(s: str) -> str:
    return re.sub(r"\s+", " ", s.lower()).strip()


def task_rows_for(items: list[ActionItem], meeting_page_id: str | None) -> list[TaskRow]:
    """Task rows for the action items. A due date is filled only when the deadline is already a real date; wording such as
    "by Friday" stays as text in the page, never turned into a date we are guessing. The owner is written only if named.
    `meeting_page_id` links each task to the meeting's row, when it was saved as one."""
    rows = []
    for i in items:
        if not i.task.strip():
            continue
        title = re.sub(r"\s*\n\s*", " ", i.task).strip()[:2000]
        deadline = (i.deadline or "").strip()
        values: dict = {"status": "Not started"}
        if _ISO_DAY.match(deadline):
            values["due"] = deadline
        if (i.owner or "").strip():
            values["owner"] = i.owner.strip()
        if meeting_page_id:
            values["meeting"] = meeting_page_id
        lines = []
        if deadline and not _ISO_DAY.match(deadline):
            lines.append(f"Due: {deadline}")
        if (i.context or "").strip():
            lines.append(f"Context: {i.context.strip()}")
        bits = [f"due {values['due']}" if "due" in values else f"due “{deadline}” (in the page)" if deadline else "",
                f"owner {values['owner']}" if "owner" in values else ""]
        rows.append(TaskRow(_normalise(i.task), title, "\n\n".join(lines), values, " · ".join(b for b in bits if b)))
    return rows
