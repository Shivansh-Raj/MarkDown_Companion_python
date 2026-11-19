"""Follow-up detection (earlier action items vs. a new meeting) and live question suggestions (port of V1's followups.ts).

Nothing here ever ticks a checkbox or edits a file: it only reports what the transcript suggests.
"""
from __future__ import annotations

import dataclasses
import re
from dataclasses import dataclass, field

from .coach import extract_json, in_transcript, squash

STATUSES = ("resolved", "discussed_open", "delayed", "not_mentioned")
_RETHINK = ("The user was not satisfied with the previous answer. Re-read the transcript carefully and reconsider each point, "
            "this time being stricter about evidence.\n\n")


@dataclass
class PriorItem:
    id: str
    task: str
    owner: str | None
    deadline: str | None
    meeting_title: str
    meeting_date: str
    state: str = "unknown"             # from the Markdown checkbox: "open" | "done" | "unknown"
    file: str | None = None
    # Set for tasks read from the Notion Tasks database: what is needed to mark one done once a suggestion is accepted.
    notion: dict | None = None


@dataclass
class FollowUp:
    id: str
    status: str
    evidence: str                      # a quote from the new transcript (verified to exist)
    note: str


@dataclass
class RecurringTopic:
    topic: str
    meetings: list[str]
    evidence: str


@dataclass
class FollowUpReport:
    created_at_ms: int
    items: list[PriorItem]
    results: list[FollowUp]
    topics: list[RecurringTopic] = field(default_factory=list)
    dropped: int = 0                   # quotes the AI gave that were not in the transcript (statuses were reset)

    def to_json(self) -> dict:
        return {"type": "followups", "createdAtMs": self.created_at_ms, "markdown": follow_up_markdown(self),
                "items": [dataclasses.asdict(i) for i in self.items], "results": [dataclasses.asdict(r) for r in self.results],
                "topics": [dataclasses.asdict(t) for t in self.topics], "dropped": self.dropped}

    @staticmethod
    def from_json(d: dict) -> "FollowUpReport":
        return FollowUpReport(d["createdAtMs"], [PriorItem(**i) for i in d["items"]], [FollowUp(**r) for r in d["results"]],
                              [RecurringTopic(**t) for t in d.get("topics", [])], int(d.get("dropped", 0)))


# ---- reading checkbox state from Markdown ----
_TASK = re.compile(r"^\s*(?:>\s*)*(?:[-*+]|\d{1,9}[.)])\s+\[([ xX])\]\s+(.*\S)\s*$")


def parse_checkboxes(content: str) -> list[tuple[bool, str]]:
    """(checked, text) for every task line outside code fences."""
    out, fence = [], None
    for line in content.splitlines():
        f = re.match(r"^\s{0,3}(`{3,}|~{3,})", line)
        if f:
            ch = f.group(1)[0]
            fence = ch if fence is None else (None if fence == ch else fence)
            continue
        if fence:
            continue
        m = _TASK.match(line)
        if m:
            out.append((m.group(1) != " ", m.group(2)))
    return out


def match_checkbox_state(items: list[PriorItem], files: list[tuple[str, str]]) -> list[PriorItem]:
    """Look each earlier action item up in the user's Markdown files to read its real checkbox state."""
    boxes = [(checked, text, name, squash(text)) for name, content in files for checked, text in parse_checkboxes(content)]
    out = []
    for it in items:
        key = squash(it.task)
        if len(key) < 4:
            out.append(it)
            continue
        hits = [b for b in boxes if b[3] == key or (len(key) >= 12 and (key in b[3] or (len(b[3]) >= 12 and b[3] in key)))]
        if not hits:
            out.append(dataclasses.replace(it, state="unknown"))
            continue
        open_hit = next((h for h in hits if not h[0]), None)     # an unchecked copy means it is still open
        out.append(dataclasses.replace(it, state="open", file=open_hit[2]) if open_hit else dataclasses.replace(it, state="done", file=hits[0][2]))
    return out


# ---- comparing with a new meeting ----
def follow_up_system_prompt() -> str:
    return ("You compare action items from EARLIER meetings with the transcript of a NEW meeting. "
            'Output ONLY a JSON array with one element per action item: {"id": string, "status": "resolved"|"discussed_open"|"delayed"|"not_mentioned", '
            '"evidence": string, "note": string}. '
            "'resolved' = the new transcript says it was completed. 'discussed_open' = it was discussed again but no completion was stated. "
            "'delayed' = someone says it is late, postponed or has a new deadline. 'not_mentioned' = it does not come up. "
            "'evidence' must be an exact quote copied from the new transcript for every status except 'not_mentioned' (use an empty string then). "
            "'note' is one short sentence. Being mentioned again does NOT mean it is unfinished or finished; judge only from what is said. "
            "Never invent facts. No code fences, no commentary.")


def follow_up_user_prompt(items: list[PriorItem], transcript: str, rethink: bool = False) -> str:
    lst = "\n".join(f"- id={i.id} | task: {i.task} | owner: {i.owner or 'not stated'} | due: {i.deadline or 'not stated'} | "
                    f"from: {i.meeting_title} ({i.meeting_date})" for i in items)
    return f'{_RETHINK if rethink else ""}Earlier action items:\n{lst}\n\nNew meeting transcript:\n"""\n{transcript}\n"""'


def to_follow_ups(raw: str, items: list[PriorItem], transcript: str) -> tuple[list[FollowUp], int]:
    """Unknown ids are dropped; any status without a verifiable quote becomes "not_mentioned"."""
    data = extract_json(raw)
    if not isinstance(data, list):
        raise ValueError("The follow-up check did not return a list. Try again.")
    t = squash(transcript)
    by_id: dict[str, FollowUp] = {}
    dropped = 0
    for row in data:
        r = row if isinstance(row, dict) else {}
        id_ = r.get("id") if isinstance(r.get("id"), str) else ""
        if not any(i.id == id_ for i in items):
            continue
        status = r.get("status") if r.get("status") in STATUSES else "not_mentioned"
        evidence = r["evidence"].strip() if isinstance(r.get("evidence"), str) else ""
        note = r["note"].strip()[:300] if isinstance(r.get("note"), str) else ""
        if status != "not_mentioned" and not in_transcript(evidence, t):
            dropped += 1
            status = "not_mentioned"
        by_id[id_] = FollowUp(id_, status, "" if status == "not_mentioned" else evidence, note)
    # anything the model skipped is reported as not mentioned rather than silently disappearing
    return [by_id.get(i.id) or FollowUp(i.id, "not_mentioned", "", "") for i in items], dropped


def topics_system_prompt() -> str:
    return ("You find topics that keep coming up across several meetings. You get summaries of earlier meetings and the transcript of a new one. "
            'Output ONLY a JSON array (at most 5) of {"topic": string, "meetings": [string], "evidence": string}. '
            "A topic counts only if it appears in the new meeting AND in at least one earlier summary. 'meetings' lists the titles of earlier meetings that covered it. "
            "'evidence' is an exact quote from the NEW transcript. If nothing recurs output []. Never invent topics. No code fences.")


def topics_user_prompt(summaries: list[tuple[str, str, str]], transcript: str, rethink: bool = False) -> str:
    prior = "\n\n".join(f"### {title} ({date})\n{md}" for title, date, md in summaries)
    return f'{_RETHINK if rethink else ""}Earlier meetings:\n{prior}\n\nNew meeting transcript:\n"""\n{transcript}\n"""'


def to_topics(raw: str, transcript: str) -> tuple[list[RecurringTopic], int]:
    data = extract_json(raw)
    if not isinstance(data, list):
        raise ValueError("The topic check did not return a list. Try again.")
    t = squash(transcript)
    out, dropped = [], 0
    for row in data:
        r = row if isinstance(row, dict) else {}
        topic = r["topic"].strip() if isinstance(r.get("topic"), str) else ""
        evidence = r["evidence"].strip() if isinstance(r.get("evidence"), str) else ""
        meetings = [m for m in (r.get("meetings") if isinstance(r.get("meetings"), list) else []) if isinstance(m, str) and m.strip()]
        if not topic or not meetings:
            continue
        if not in_transcript(evidence, t):
            dropped += 1
            continue
        out.append(RecurringTopic(topic, meetings, evidence))
    return out[:5], dropped


_HEADINGS = [("delayed", "Possibly delayed"), ("discussed_open", "Discussed again, no completion recorded"),
             ("resolved", "Sounds finished (check, then tick it yourself)"), ("not_mentioned", "Not discussed in this meeting")]


def _item_line(i: PriorItem, f: FollowUp | None) -> str:
    meta = [f"Owner: {i.owner}" if i.owner else "Owner: _not stated_", f"Due: {i.deadline}" if i.deadline else "Due: _not stated_",
            f"From: {i.meeting_title} ({i.meeting_date})"]
    where = "_not found in your Markdown files, so its checkbox state is unknown_" if i.state == "unknown" else (f"still unchecked in {i.file}" if i.file else "")
    out = f"- {i.task}\n  - {' · '.join(meta)}"
    if where and i.state != "done":
        out += f"\n  - {where}"
    if f and f.evidence:
        out += f"\n  - Said: “{f.evidence}”"
    if f and f.note:
        out += f"\n  - {f.note}"
    return out


def follow_up_markdown(r: FollowUpReport) -> str:
    out: list[str] = []
    active = [i for i in r.items if i.state != "done"]
    done = [i for i in r.items if i.state == "done"]
    result = {x.id: x for x in r.results}
    for status, heading in _HEADINGS:
        rows = [i for i in active if (result.get(i.id).status if result.get(i.id) else "not_mentioned") == status]
        if rows:
            out += [f"**{heading}**", "", *[_item_line(i, result.get(i.id)) for i in rows], ""]
    if done:
        out += ["**Already ticked in your notes (not re-checked)**", "", *[f"- {i.task} ({i.file})" for i in done], ""]
    if r.topics:
        out += ["**Topics that keep coming up**", "", *[f"- {t.topic} — also in: {', '.join(t.meetings)}\n  - Said: “{t.evidence}”" for t in r.topics], ""]
    if not out:
        out += ["_No earlier action items to follow up on._", ""]
    if r.dropped:
        out.append(f"_{r.dropped} point(s) were left out or reset because their quote did not match the transcript._")
    out.append("_Nothing was ticked or changed in your files. These are suggestions to review._")
    return "\n".join(out)


# ---- live question suggestions ----
QUESTION_KINDS = ("clarify", "owner", "deadline", "assumption", "risk", "dependency", "unresolved")
SUGGEST_MIN_GAP_MS = 45_000
SUGGEST_MIN_WORDS = 40
SUGGEST_CONTEXT_CHARS = 2400


@dataclass
class Suggestion:
    question: str
    reason: str
    kind: str


def should_suggest(enabled: bool, paused: bool, in_flight: bool, transcribing: int, since_ms: int, new_words: int) -> bool:
    """A good moment to ask for new questions: never while off, paused, busy or too soon."""
    return enabled and not paused and not in_flight and transcribing == 0 and since_ms >= SUGGEST_MIN_GAP_MS and new_words >= SUGGEST_MIN_WORDS


def question_system_prompt() -> str:
    return ("You help someone attending a live meeting by suggesting questions they MAY want to ask. You see the most recent part of the transcript. "
            'Output ONLY a JSON array of at most 3 items: {"question": string, "reason": string, "kind": "clarify"|"owner"|"deadline"|"assumption"|"risk"|"dependency"|"unresolved"}. '
            "Suggest a question only when the transcript shows a real gap: an ambiguous technical explanation (clarify), a task with no named person (owner), "
            "a commitment with no timeline (deadline), an unstated assumption, an unmentioned risk or dependency, or an issue left unresolved. "
            "Each question is short, polite and specific to what was said. 'reason' is one short sentence pointing at the gap. "
            "Do not repeat the questions already suggested. If there is nothing worth asking, output []. No code fences, no commentary.")


def question_user_prompt(recent: str, already: list[str], rethink: bool = False) -> str:
    prev = "Already suggested (do not repeat):\n" + "\n".join(f"- {q}" for q in already) + "\n\n" if already else ""
    again = "The user was not satisfied with those suggestions. Think again and offer questions from different angles.\n\n" if rethink else ""
    return f'{again}{prev}Most recent transcript:\n"""\n{recent}\n"""'


def parse_questions(raw: str, already: list[str]) -> list[Suggestion]:
    try:
        data = extract_json(raw)
    except ValueError:
        return []
    if not isinstance(data, list):
        return []
    seen = {squash(q) for q in already}
    out: list[Suggestion] = []
    for row in data:
        r = row if isinstance(row, dict) else {}
        q = r["question"].strip()[:240] if isinstance(r.get("question"), str) else ""
        if not q or squash(q) in seen:
            continue
        seen.add(squash(q))
        out.append(Suggestion(q, r["reason"].strip()[:200] if isinstance(r.get("reason"), str) else "",
                              r["kind"] if r.get("kind") in QUESTION_KINDS else "clarify"))
    return out[:3]


def recent_context(text: str, limit: int = SUGGEST_CONTEXT_CHARS) -> str:
    """The tail of the transcript sent for live suggestions (keeps latency and cost low)."""
    if len(text) <= limit:
        return text
    tail = text[-limit:]
    space = tail.find(" ")
    return tail[space + 1:] if 0 < space < 80 else tail
