"""Meeting analyses: summary, key takeaways, action items (port of V1's analyses.ts).

Everything is grounded in the transcript: prompts forbid invented owners/dates and unknowns are stored as None.
"""
from __future__ import annotations

import json
import re
from dataclasses import dataclass, field

from .transcript import TranscriptChunk, format_clock

ANALYSIS_TYPES = ("summary", "takeaways", "actions")
ANALYSIS_LABELS = {"summary": "Meeting summary", "takeaways": "Key takeaways", "actions": "Action items"}
MAX_TRANSCRIPT_CHARS = 100_000

_GROUNDING = ("Base everything strictly on the transcript. Never invent facts, names, owners, deadlines or decisions. "
              "The transcript comes from speech recognition and may contain mistakes; do not over-interpret odd words. "
              "Write in the same language as the transcript.")


@dataclass
class ActionItem:
    task: str
    owner: str | None = None
    deadline: str | None = None
    context: str | None = None


@dataclass
class AnalysisResult:
    type: str
    created_at_ms: int
    markdown: str
    items: list[ActionItem] | None = None
    truncated: bool = False

    def to_json(self) -> dict:
        d = {"type": self.type, "createdAtMs": self.created_at_ms, "markdown": self.markdown, "truncated": self.truncated}
        if self.items is not None:
            d["items"] = [{"task": i.task, "owner": i.owner, "deadline": i.deadline, "context": i.context} for i in self.items]
        return d

    @staticmethod
    def from_json(d: dict) -> "AnalysisResult":
        items = [ActionItem(i["task"], i.get("owner"), i.get("deadline"), i.get("context")) for i in d["items"]] if d.get("items") is not None else None
        return AnalysisResult(d["type"], d["createdAtMs"], d["markdown"], items, bool(d.get("truncated")))


def timed_transcript(chunks: list[TranscriptChunk]) -> str:
    """The transcript with [mm:ss] markers so the model can ground its answers in time."""
    return "\n".join(f"[{format_clock(c.start_ms)}] {c.text.strip()}" for c in chunks if c.status == "final" and c.text.strip())


def limit_transcript(text: str, max_chars: int = MAX_TRANSCRIPT_CHARS) -> tuple[str, bool]:
    if len(text) <= max_chars:
        return text, False
    cut = text[:max_chars]
    nl = cut.rfind("\n")
    return (cut[:nl] if nl > 0 else cut), True


def build_system_prompt(kind: str) -> str:
    if kind == "summary":
        return ("You summarise meetings. Output ONLY Markdown with no code fences. Use exactly these sections, in order: "
                "'## Overview' (3-5 sentences), '## Decisions' (bullets; only if decisions were actually made), "
                "'## Open questions' (bullets; only if questions were left unresolved). Omit a section that has no content. " + _GROUNDING)
    if kind == "takeaways":
        return ("You extract the key takeaways of a meeting: the most important information, explanations and discussion points. "
                "Output ONLY a Markdown bullet list of 5 to 10 items, most important first, each one short sentence, with no code fences "
                "and no heading. " + _GROUNDING)
    if kind == "actions":
        return ("You extract action items from a meeting. Output ONLY a JSON array, with no code fences and no commentary. "
                'Each element is {"task": string, "owner": string|null, "deadline": string|null, "context": string|null}. '
                "A task is something someone committed to do or was asked to do. 'owner' is a person ONLY if the transcript names who "
                "does it; otherwise null. 'deadline' is a date or time ONLY if one was stated, written as said (for example 'Friday'); otherwise null. "
                "'context' is a short phrase on why or what it relates to, or null. If there are no action items output []. " + _GROUNDING)
    raise ValueError(f"unknown analysis type {kind!r}")


def build_user_prompt(title: str, transcript: str) -> str:
    return f'Meeting title: {title}\n\nTranscript:\n"""\n{transcript}\n"""'


_UNKNOWN = re.compile(r"^\s*(null|none|n/a|na|unknown|unspecified|not (stated|specified|mentioned|assigned)|tbd|-+)\s*$", re.I)


def _clean(v) -> str | None:
    if not isinstance(v, str):
        return None
    t = v.strip()
    return None if not t or _UNKNOWN.match(t) else t


def parse_action_items(raw: str) -> list[ActionItem]:
    """Pull the JSON array out of a model reply, tolerating fences and stray text."""
    start, end = raw.find("["), raw.rfind("]")
    if start < 0 or end <= start:
        raise ValueError("The AI did not return a list of action items. Try again.")
    try:
        data = json.loads(raw[start:end + 1])
    except ValueError:
        raise ValueError("The action items came back in an unreadable format. Try again.") from None
    if not isinstance(data, list):
        raise ValueError("The AI did not return a list of action items. Try again.")
    items = []
    for row in data:
        r = row if isinstance(row, dict) else {}
        task = _clean(r.get("task"))
        if task:
            items.append(ActionItem(task, _clean(r.get("owner")), _clean(r.get("deadline")), _clean(r.get("context"))))
    return items


def action_items_markdown(items: list[ActionItem]) -> str:
    if not items:
        return "_No action items were identified in this meeting._"
    out = []
    for i in items:
        ctx = f"\n  - Context: {i.context}" if i.context else ""
        out.append(f"- [ ] {i.task}\n  - Owner: {i.owner or '_not stated_'}\n  - Due: {i.deadline or '_not stated_'}{ctx}")
    return "\n".join(out)


def strip_fences(text: str) -> str:
    m = re.match(r"^\s*```(?:markdown|md|json)?\s*\n([\s\S]*?)\n?```\s*$", text)
    return (m.group(1) if m else text).strip()


def to_result(kind: str, raw: str, truncated: bool, now_ms: int) -> AnalysisResult:
    if kind == "actions":
        items = parse_action_items(raw)
        return AnalysisResult(kind, now_ms, action_items_markdown(items), items, truncated)
    md = strip_fences(raw)
    if not md:
        raise ValueError("The AI returned nothing. Try again.")
    return AnalysisResult(kind, now_ms, md, None, truncated)


def latest_by_type(results: list[AnalysisResult]) -> dict[str, AnalysisResult]:
    out: dict[str, AnalysisResult] = {}
    for r in results:
        cur = out.get(r.type)
        if cur is None or r.created_at_ms >= cur.created_at_ms:
            out[r.type] = r
    return out
