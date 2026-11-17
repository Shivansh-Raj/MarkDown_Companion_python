"""AI speaking coach (port of V1's coach.ts): sentence improvements, clarity, structure, vocabulary.

Every quoted sentence must appear in the transcript; suggestions that quote words the speaker never said are dropped.
"""
from __future__ import annotations

import json
import re
from dataclasses import dataclass, field

from .analytics import DELIVERY_CAVEAT, PACE_REFERENCE, Analytics
from .transcript import format_clock

COACH_TYPES = ("sentences", "clarity", "structure", "vocabulary")
COACH_LABELS = {"sentences": "Sentence improvements", "clarity": "Clarity check", "structure": "Presentation structure",
                "vocabulary": "Vocabulary suggestions"}
STYLES = ("natural", "professional", "concise")
STYLE_LABELS = {"natural": "Natural", "professional": "Professional", "concise": "Concise"}

_RULES = ("Rules: Keep the speaker's meaning and natural voice. Do NOT turn it into generic corporate language, and do not add facts. "
          "Quote the speaker's words EXACTLY as they appear in the transcript (copy them verbatim, including any speech-recognition errors). "
          "Only include items that genuinely need improvement; fewer, better items beat many weak ones. "
          "Output ONLY JSON, with no code fences and no commentary. Write in the same language as the transcript.")


@dataclass
class SentenceFix:
    original: str
    category: str
    natural: str
    professional: str
    concise: str
    why: str


@dataclass
class ClarityIssue:
    quote: str
    problem: str
    suggestion: str


@dataclass
class StructurePart:
    status: str                        # "present" | "weak" | "missing"
    note: str = ""


@dataclass
class StructureReport:
    introduction: StructurePart
    main_explanation: StructurePart
    supporting_points: StructurePart
    conclusion: StructurePart
    suggestions: list[str] = field(default_factory=list)


@dataclass
class VocabSuggestion:
    said: str
    better: list[str]
    why: str = ""


@dataclass
class CoachResult:
    type: str
    created_at_ms: int
    truncated: bool = False
    dropped: int = 0                   # suggestions discarded because their quote was not in the transcript
    sentences: list[SentenceFix] | None = None
    clarity: list[ClarityIssue] | None = None
    structure: StructureReport | None = None
    vocabulary: list[VocabSuggestion] | None = None

    def to_json(self) -> dict:
        d: dict = {"type": self.type, "createdAtMs": self.created_at_ms, "truncated": self.truncated, "dropped": self.dropped}
        for key in ("sentences", "clarity", "vocabulary"):
            v = getattr(self, key)
            if v is not None:
                d[key] = [vars(x).copy() for x in v]
        if self.structure is not None:
            s = self.structure
            d["structure"] = {"introduction": vars(s.introduction), "mainExplanation": vars(s.main_explanation),
                              "supportingPoints": vars(s.supporting_points), "conclusion": vars(s.conclusion), "suggestions": s.suggestions}
        return d

    @staticmethod
    def from_json(d: dict) -> "CoachResult":
        r = CoachResult(d["type"], d["createdAtMs"], bool(d.get("truncated")), int(d.get("dropped", 0)))
        if "sentences" in d:
            r.sentences = [SentenceFix(**x) for x in d["sentences"]]
        if "clarity" in d:
            r.clarity = [ClarityIssue(**x) for x in d["clarity"]]
        if "vocabulary" in d:
            r.vocabulary = [VocabSuggestion(**x) for x in d["vocabulary"]]
        if "structure" in d:
            s = d["structure"]
            r.structure = StructureReport(StructurePart(**s["introduction"]), StructurePart(**s["mainExplanation"]),
                                          StructurePart(**s["supportingPoints"]), StructurePart(**s["conclusion"]), list(s.get("suggestions", [])))
        return r


def build_system_prompt(kind: str) -> str:
    if kind == "sentences":
        return ("You are a speaking coach reviewing a spoken practice session. Find up to 8 sentences that are awkwardly structured, too casual "
                "or unclear for a workplace, or longer than they need to be. "
                'Output a JSON array of {"original": string, "category": "awkward"|"casual"|"wordy", "natural": string, "professional": string, '
                '"concise": string, "why": string}. '
                "'natural' keeps the speaker's own conversational voice; 'professional' suits a workplace; 'concise' is the shortest version that "
                "keeps the meaning. 'why' is one short sentence on what was wrong. " + _RULES)
    if kind == "clarity":
        return ("You are a speaking coach. Find up to 6 places where the explanation is confusing, incomplete, or hard for a listener to follow "
                "(a missing step, an undefined term, a jump in logic). "
                'Output a JSON array of {"quote": string, "problem": string, "suggestion": string}. ' + _RULES)
    if kind == "structure":
        return ("You are a speaking coach judging how a presentation is organised. Judge whether it has an introduction, a main explanation, "
                "supporting points and a conclusion. "
                'Output ONE JSON object: {"introduction": {"status": "present"|"weak"|"missing", "note": string}, "mainExplanation": {...}, '
                '"supportingPoints": {...}, "conclusion": {...}, "suggestions": [string]}. '
                "Notes are one short sentence grounded in what was actually said. Up to 4 suggestions. " + _RULES)
    if kind == "vocabulary":
        return ("You are a speaking coach. Find up to 8 words or short phrases the speaker used where a more precise, natural and "
                "context-appropriate alternative exists (vague words, overused words, wrong register). "
                'Output a JSON array of {"said": string, "better": [string, string], "why": string}. Offer 1 to 3 alternatives. ' + _RULES)
    raise ValueError(f"unknown coach type {kind!r}")


def build_user_prompt(title: str, transcript: str) -> str:
    return f'Practice session: {title}\n\nTranscript:\n"""\n{transcript}\n"""'


def extract_json(raw: str):
    """The first complete JSON value ([...] or {...}) in a reply, tolerating fences and brackets inside strings."""
    m = re.search(r"[\[{]", raw)
    if not m:
        raise ValueError("The coach did not return structured feedback. Try again.")
    start = m.start()
    open_ch = raw[start]
    close_ch = "]" if open_ch == "[" else "}"
    depth, in_str, i = 0, False, start
    while i < len(raw):
        c = raw[i]
        if in_str:
            if c == "\\":
                i += 1
            elif c == '"':
                in_str = False
        elif c == '"':
            in_str = True
        elif c == open_ch:
            depth += 1
        elif c == close_ch:
            depth -= 1
            if depth == 0:
                try:
                    return json.loads(raw[start:i + 1])
                except ValueError:
                    break
        i += 1
    raise ValueError("The coach's feedback came back in an unreadable format. Try again.")


def squash(s: str) -> str:
    return re.sub(r"\s+", " ", re.sub(r"[^\w\s']", " ", s.lower()).replace("_", " ")).strip()


def in_transcript(quote: str, transcript_squashed: str) -> bool:
    """True when the quote really appears in what the speaker said (guards against invented quotes)."""
    q = squash(quote)
    return len(q) >= 3 and q in transcript_squashed


def _s(v, limit=600) -> str:
    return v.strip()[:limit] if isinstance(v, str) else ""


def _part(v) -> StructurePart:
    o = v if isinstance(v, dict) else {}
    status = o.get("status")
    return StructurePart(status if status in ("present", "weak") else "missing", _s(o.get("note"), 300))


def to_result(kind: str, raw: str, transcript: str, truncated: bool, now_ms: int) -> CoachResult:
    """Parse and validate a reply; suggestions quoting words the speaker never said are dropped."""
    data = extract_json(raw)
    t = squash(transcript)
    base = CoachResult(kind, now_ms, truncated)
    if kind == "structure":
        if not isinstance(data, dict):
            raise ValueError("The coach did not return a structure report. Try again.")
        sugg = [_s(x, 300) for x in (data.get("suggestions") if isinstance(data.get("suggestions"), list) else [])]
        base.structure = StructureReport(_part(data.get("introduction")), _part(data.get("mainExplanation")), _part(data.get("supportingPoints")),
                                         _part(data.get("conclusion")), [x for x in sugg if x][:4])
        return base
    if not isinstance(data, list):
        raise ValueError("The coach did not return a list. Try again.")
    rows = [r if isinstance(r, dict) else {} for r in data]
    if kind == "sentences":
        out = []
        for r in rows:
            original, natural = _s(r.get("original")), _s(r.get("natural"))
            if not original or not natural:
                continue
            if not in_transcript(original, t):
                base.dropped += 1
                continue
            cat = r.get("category") if r.get("category") in ("casual", "wordy") else "awkward"
            out.append(SentenceFix(original, cat, natural, _s(r.get("professional")) or natural, _s(r.get("concise")) or natural, _s(r.get("why"), 300)))
        base.sentences = out
    elif kind == "clarity":
        out2 = []
        for r in rows:
            quote = _s(r.get("quote"))
            if not quote or not _s(r.get("problem")):
                continue
            if not in_transcript(quote, t):
                base.dropped += 1
                continue
            out2.append(ClarityIssue(quote, _s(r.get("problem"), 300), _s(r.get("suggestion"), 400)))
        base.clarity = out2
    else:
        out3 = []
        for r in rows:
            said = _s(r.get("said"), 120)
            better = [x for x in (_s(b, 120) for b in (r.get("better") if isinstance(r.get("better"), list) else [])) if x][:3]
            if not said or not better:
                continue
            if not in_transcript(said, t):
                base.dropped += 1
                continue
            out3.append(VocabSuggestion(said, better, _s(r.get("why"), 300)))
        base.vocabulary = out3
    return base


# ---- Markdown ----
_STATUS = {"present": "Present", "weak": "Weak", "missing": "Missing"}


def sentence_block(f: SentenceFix, style: str = "all") -> str:
    lines = [f'**You said:** "{f.original}"']
    if style == "all":
        lines += [f"- **Natural:** {f.natural}", f"- **Professional:** {f.professional}", f"- **Concise:** {f.concise}"]
    else:
        lines.append(f"**Better ({STYLE_LABELS[style].lower()}):** {getattr(f, style)}")
    if f.why:
        lines.append(f"**Why:** {f.why}")
    return "\n".join(lines)


def coach_markdown(r: CoachResult) -> str:
    note = f"\n_{r.dropped} suggestion(s) were left out because they did not match what was said._\n" if r.dropped else ""
    if r.sentences is not None:
        return ("\n\n".join(sentence_block(f) for f in r.sentences) if r.sentences else "_No sentences needed improving._") + note
    if r.clarity is not None:
        body = ("\n\n".join(f'**You said:** "{c.quote}"\n**Problem:** {c.problem}\n**Try:** {c.suggestion}' for c in r.clarity)
                if r.clarity else "_Nothing was flagged as unclear._")
        return body + note
    if r.vocabulary is not None:
        body = ("\n".join(f'- **"{v.said}"** → {" / ".join(chr(34) + b + chr(34) for b in v.better)}{" — " + v.why if v.why else ""}' for v in r.vocabulary)
                if r.vocabulary else "_No vocabulary changes suggested._")
        return body + note
    if r.structure is not None:
        s = r.structure

        def row(label, p):
            return f"- **{label}:** {_STATUS[p.status]}{' — ' + p.note if p.note else ''}"
        out = [row("Introduction", s.introduction), row("Main explanation", s.main_explanation), row("Supporting points", s.supporting_points),
               row("Conclusion", s.conclusion)]
        if s.suggestions:
            out += ["", "**Suggestions**", *[f"- {x}" for x in s.suggestions]]
        return "\n".join(out)
    return ""


def speaking_analysis_markdown(a: Analytics) -> str:
    """The objective, locally computed numbers as a Markdown section."""
    out = ["**Pace**", "", f"- Words: {a.word_count}"]
    if a.wpm is not None:
        out.append(f"- Overall: {a.wpm} words per minute" + (f" ({a.articulation_wpm} while actually speaking)" if a.articulation_wpm else ""))
    if a.pace:
        out.append(f"- Range across {len(a.pace.windows)} half-minute sections: {a.pace.min}–{a.pace.max} wpm")
        out += [f"- {format_clock(w.start_ms)}: noticeably {kind} ({w.wpm} wpm)" for w, kind in a.pace.flagged]
    out += [f"- _{PACE_REFERENCE}_", "", "**Filler words**", ""]
    out.append("\n".join(f'- "{f.term}" × {f.count}' for f in a.fillers) if a.fillers else "- None found.")
    if a.filler_total:
        out.append(f'- Total: {a.filler_total} ({a.fillers_per_100:g} per 100 words). "like" and "actually" are sometimes used on purpose.')
    out += ["", "**Repetition**", ""]
    rep = [*(f'- Word "{w.term}" used {w.count} times' for w in a.repeated_words), *(f'- Phrase "{p.term}" said {p.count} times' for p in a.repeated_phrases),
           *(f'- "{p}"' + (f" at {format_clock(at)}" if at is not None else "") for p, at in a.stutters[:5])]
    out.append("\n".join(rep) if rep else "- Nothing notable.")
    if a.pauses:
        out += ["", "**Pauses**", "", f"- {a.pauses.count} pause(s) of 1 s or more; {a.pauses.long} of 1.5 s or more; {a.pauses.very_long} of 3 s or more"]
        out += [f'- {format_clock(p.start_ms)}: {p.duration_ms / 1000:.1f} s, after "{p.before}"' for p in a.pauses.longest[:3]]
    if a.notes:
        out += ["", "**Delivery indicators**", "", *[f"- {n}" for n in a.notes], f"- _{DELIVERY_CAVEAT}_"]
    return "\n".join(out)


def selected_sentences_markdown(fixes: list[SentenceFix], picks: list[tuple[int, str]]) -> str:
    """Only the improvements the user picked, in the style they picked."""
    blocks = [sentence_block(fixes[i], style) for i, style in picks if 0 <= i < len(fixes)]
    return "\n\n".join(blocks) if blocks else "_No improvements selected._"
