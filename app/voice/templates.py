"""Markdown documents built from voice outputs (port of V1's templates.ts)."""
from __future__ import annotations

from dataclasses import dataclass

from .analyses import ANALYSIS_LABELS, ANALYSIS_TYPES, AnalysisResult
from .transcript import date_stamp, format_clock

_SUFFIX = {"summary": "summary", "takeaways": "takeaways", "actions": "action items"}
TRUNCATED_NOTE = "the recording was very long, so only the first part was analysed."


@dataclass
class Section:
    id: str
    label: str
    suffix: str                  # used in suggested file names
    markdown: str
    note: str | None = None


def _header(meta, title: str) -> str:
    return "\n".join([f"# {meta.title.strip() or 'Meeting'} — {title}", "", f"- **Date:** {date_stamp(meta.created_at_ms)}",
                      f"- **Length:** {format_clock(meta.duration_ms)}", "", ""])


def _note(s: Section) -> str:
    return f"\n> Note: {s.note}\n" if s.note else ""


def standalone_from_section(meta, s: Section) -> str:
    return f"{_header(meta, s.label)}{s.markdown.strip()}\n{_note(s)}"


def combined_from_sections(meta, doc_title: str, sections: list[Section]) -> str:
    return _header(meta, doc_title) + "\n".join(f"## {s.label}\n\n{s.markdown.strip()}\n{_note(s)}" for s in sections)


def append_from_sections(meta, sections: list[Section]) -> str:
    """Block appended to an existing file under its own heading; existing content is never touched."""
    body = "\n".join(f"### {s.label}\n\n{s.markdown.strip()}\n{_note(s)}" for s in sections)
    return f"## {meta.title.strip() or 'Recording'} ({date_stamp(meta.created_at_ms)})\n\n{body}"


def append_to_content(existing: str, block: str) -> str:
    return f"{existing.rstrip()}\n\n{block.strip()}\n"


def meeting_sections(results: list[AnalysisResult]) -> list[Section]:
    ordered = [next((r for r in results if r.type == t), None) for t in ANALYSIS_TYPES]
    return [Section(r.type, ANALYSIS_LABELS[r.type], _SUFFIX[r.type], r.markdown, TRUNCATED_NOTE if r.truncated else None)
            for r in ordered if r is not None]
