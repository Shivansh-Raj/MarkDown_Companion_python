"""Creating new Markdown files from a prompt: types, prompts, parsing and safe file writing."""
from __future__ import annotations

import os
import re
from pathlib import Path

DOC_TYPES = ("plan", "tasks", "scheduler", "notes")
DOC_TYPE_LABELS = {"auto": "Auto-detect", "plan": "Plan", "tasks": "Task list",
                   "scheduler": "Scheduler", "notes": "Notes"}

_GUIDE = {
    "plan": "a PLAN: a title, a short goal, phases as '##' sections, and '- [ ]' checkbox steps inside each phase.",
    "tasks": "a TASK LIST: a title and '##' groups of '- [ ]' checkbox tasks, ordered by priority.",
    "scheduler": "a SCHEDULER: a title and one '##' section per day or time block, with '- [ ] HH:MM - activity' checkbox lines.",
    "notes": "NOTES: a title, '##' sections, concise bullet points. Use checkboxes only for real action items.",
}


def build_system_prompt(doc_type: str) -> str:
    base = ("You write Markdown files for a checklist app. Output ONLY the Markdown document, no commentary and "
            "no code fences around it. Use GitHub-flavoured Markdown. Begin with a single '# ' title line.")
    if doc_type == "auto":
        return (base + " First decide which document type best fits the request: plan, tasks, scheduler or notes. "
                "Your very first line must be exactly 'TYPE: <type>' (one of those four words), then a blank line, "
                "then the document. Types: " + " ".join(f"{t} = {_GUIDE[t]}" for t in DOC_TYPES))
    return base + " Write " + _GUIDE[doc_type]


_TYPE_LINE = re.compile(r"^\s*TYPE:\s*([A-Za-z]+)[^\n]*(\n|$)", re.I)


def split_type_line(raw: str) -> tuple[str | None, str, bool]:
    """Splits a leading `TYPE: x` line from the body, tolerating a partially streamed first line.

    Returns (type or None, body, pending). While `pending` the body is withheld.
    """
    m = _TYPE_LINE.match(raw)
    if not m:
        head = raw.lstrip()
        pending = len(head) < 6 or (head[:4].upper() == "TYPE" and "\n" not in head)
        return None, ("" if pending else raw), pending
    word = m.group(1).lower()
    doc_type = word if word in DOC_TYPES else None
    if not m.group(2):
        return doc_type, "", True
    body = re.sub(r"^\s*\n", "", raw[m.end():], count=1)
    return doc_type, body, False


def strip_fence(md: str) -> str:
    m = re.match(r"^\s*```(?:markdown|md)?\s*\n([\s\S]*?)\n?```\s*$", md)
    return m.group(1) if m else md


def title_from_markdown(md: str, fallback: str = "untitled") -> str:
    m = re.search(r"^#\s+(.+)$", md, re.M)
    title = re.sub(r"[#*_`]", "", m.group(1)).strip() if m else ""
    return title or fallback


def safe_stem(raw: str) -> str:
    out = re.sub(r"[^\w]+", "-", raw.lower(), flags=re.UNICODE).strip("-_")[:60].strip("-")
    return out or "untitled"


def write_new_file(directory: str | os.PathLike, title: str, content: str) -> Path:
    """Writes `<stem>.md` into `directory` (created if needed), never overwriting; returns the path."""
    d = Path(directory)
    d.mkdir(parents=True, exist_ok=True)
    stem = safe_stem(title)
    for n in range(1000):
        path = d / (f"{stem}.md" if n == 0 else f"{stem}-{n}.md")
        try:
            with open(path, "x", encoding="utf-8", newline="") as f:   # "x": fail if it exists
                f.write(content)
            return path
        except FileExistsError:
            continue
    raise OSError("Could not find a free file name")


def default_save_dir() -> Path:
    docs = Path.home() / "Documents"
    return (docs if docs.is_dir() else Path.home()) / "MarkdownCompanion"
