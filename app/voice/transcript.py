"""Transcript model and Markdown helpers (port of V1's transcript.ts)."""
from __future__ import annotations

import datetime
import re
from dataclasses import dataclass, field

MODE_LABELS = {"meeting": "Meeting", "practice": "Practice", "note": "Quick voice note"}


@dataclass
class Word:
    w: str
    start: int
    end: int                                # milliseconds on the active recording timeline


@dataclass
class TranscriptChunk:
    index: int
    start_ms: int
    end_ms: int
    text: str = ""
    words: list[Word] = field(default_factory=list)
    status: str = "pending"                 # "pending" | "final" | "failed"
    voiced_ms: int | None = None
    error: str | None = None

    def to_json(self) -> dict:
        return {"index": self.index, "startMs": self.start_ms, "endMs": self.end_ms, "text": self.text, "status": self.status,
                "voicedMs": self.voiced_ms, "error": self.error, "words": [{"w": w.w, "start": w.start, "end": w.end} for w in self.words]}

    @staticmethod
    def from_json(d: dict) -> "TranscriptChunk":
        return TranscriptChunk(d["index"], d["startMs"], d["endMs"], d.get("text", ""),
                               [Word(w["w"], w["start"], w["end"]) for w in d.get("words", [])], d.get("status", "final"),
                               d.get("voicedMs"), d.get("error"))


def upsert_chunk(chunks: list[TranscriptChunk], nxt: TranscriptChunk) -> list[TranscriptChunk]:
    """Insert or replace by index, keeping order."""
    return sorted([c for c in chunks if c.index != nxt.index] + [nxt], key=lambda c: c.index)


_PHANTOM = re.compile(r"^(thanks? for watching[.!]?|thank you[.!]?|you|bye[.!]?|\.+)$", re.I)


def is_phantom(text: str, voiced_ms: int) -> bool:
    """Whisper sometimes invents text for near-silence; these are the usual phantom phrases."""
    return voiced_ms < 1200 and bool(_PHANTOM.match(text.strip()))


def transcript_text(chunks: list[TranscriptChunk]) -> str:
    return re.sub(r"\s+", " ", " ".join(c.text.strip() for c in chunks if c.status == "final" and c.text.strip())).strip()


def word_count(text: str) -> int:
    t = text.strip()
    return len(t.split()) if t else 0


def format_clock(ms: int) -> str:
    s = max(int(ms), 0) // 1000
    h, m, sec = s // 3600, s // 60 % 60, s % 60
    return f"{h}:{m:02d}:{sec:02d}" if h else f"{m:02d}:{sec:02d}"


def date_stamp(ms: int) -> str:
    return datetime.datetime.fromtimestamp(ms / 1000).strftime("%Y-%m-%d")


def suggest_file_name(title: str, created_at_ms: int, suffix: str = "") -> str:
    """'2026-11-17 Meeting title [suffix]' - the suggested file name."""
    t = title.strip() or "recording"
    return f"{date_stamp(created_at_ms)} {t}{' ' + suffix if suffix else ''}"


def transcript_markdown(meta, chunks: list[TranscriptChunk]) -> str:
    lines = [f"# {meta.title.strip() or 'Voice recording'} — transcript", "",
             f"- **Type:** {MODE_LABELS.get(meta.mode, meta.mode)}", f"- **Date:** {date_stamp(meta.created_at_ms)}",
             f"- **Length:** {format_clock(meta.duration_ms)}", "", "## Transcript", ""]
    body = [c for c in chunks if c.status == "final" and c.text.strip()]
    for c in body:
        lines += [f"**[{format_clock(c.start_ms)}]** {c.text.strip()}", ""]
    if not body:
        lines += ["_No speech was transcribed._", ""]
    return "\n".join(lines)


def note_system_prompt() -> str:
    return ("You turn a spoken voice note (a raw speech-to-text transcript) into a clean Markdown note. "
            "Output ONLY Markdown, with no commentary and no code fences. Start with a single '# ' title line, "
            "then short '##' sections with concise bullet points. Use '- [ ]' checkboxes ONLY for things the speaker "
            "clearly said they need to do. Do not invent facts, names, owners, dates or tasks that are not in the transcript. "
            "Fix obvious speech-to-text errors and remove filler words, but keep the speaker's meaning and wording.")


def note_user_prompt(transcript: str) -> str:
    return f'Transcript:\n"""\n{transcript}\n"""'
