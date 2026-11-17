"""Speaking analytics computed locally and deterministically from the transcript and word timestamps.

Nothing here uses AI, and nothing claims to measure confidence, emotion or personality (port of V1's analytics.ts).
"""
from __future__ import annotations

import math
import re
from dataclasses import dataclass, field

from .transcript import TranscriptChunk, Word

PAUSE_MS = 1000
LONG_PAUSE_MS = 1500
VERY_LONG_PAUSE_MS = 3000
WINDOW_MS = 30_000

SINGLE_FILLERS = ["um", "umm", "uh", "uhh", "uhm", "er", "erm", "ah", "hmm", "basically", "actually", "literally", "honestly", "like"]
PHRASE_FILLERS = ["you know", "i mean", "kind of", "sort of"]
STOP = set(("a an the and or but if then so of to in on at for with from by as is are was were be been being it its this that these those "
            "i you he she we they me him her us them my your our their not no yes do does did have has had will would can could should just very "
            "there here what which who whom when where why how about into out up down over more some any all than too also").split())

DELIVERY_CAVEAT = "Based on wording and timing only. Pitch, volume and tone are not measured, and none of this says how confident you were."
PACE_REFERENCE = "Many speaking guides suggest roughly 120–160 words per minute for presentations. That is a rough guide, not a rule."


def _round(x: float, digits: int = 0) -> float | int:
    """Round half up (as JavaScript does), so V1 and V2 report the same numbers."""
    f = 10 ** digits
    r = math.floor(x * f + 0.5) / f
    return int(r) if digits == 0 else r


@dataclass
class Counted:
    term: str
    count: int


@dataclass
class Pause:
    start_ms: int
    duration_ms: int
    before: str
    after: str


@dataclass
class PaceWindow:
    start_ms: int
    wpm: int
    words: int


@dataclass
class Pace:
    windows: list[PaceWindow]
    mean: int
    min: int
    max: int
    cv: float
    flagged: list[tuple[PaceWindow, str]]            # (window, "faster" | "slower")


@dataclass
class PauseStats:
    count: int
    long: int
    very_long: int
    avg_ms: int
    ratio: float
    longest: list[Pause]


@dataclass
class Analytics:
    has_timings: bool
    duration_ms: int
    word_count: int
    wpm: int | None = None                           # whole recorded time, pauses included
    articulation_wpm: int | None = None              # while actually speaking (long pauses removed)
    fillers: list[Counted] = field(default_factory=list)
    filler_total: int = 0
    fillers_per_100: float = 0.0
    repeated_words: list[Counted] = field(default_factory=list)
    repeated_phrases: list[Counted] = field(default_factory=list)
    stutters: list[tuple[str, int | None]] = field(default_factory=list)      # ("the the", at ms)
    pace: Pace | None = None
    pauses: PauseStats | None = None
    notes: list[str] = field(default_factory=list)


def norm(w: str) -> str:
    return re.sub(r"[^\w']", "", w.lower()).replace("_", "").strip("'")


def flatten_words(chunks: list[TranscriptChunk]) -> list[Word]:
    return sorted((w for c in chunks if c.status == "final" for w in c.words if norm(w.w)), key=lambda w: w.start)


def _top(counts: dict[str, int], minimum: int, limit: int) -> list[Counted]:
    items = [(k, n) for k, n in counts.items() if n >= minimum]
    items.sort(key=lambda kv: (-kv[1], kv[0]))
    return [Counted(k, n) for k, n in items[:limit]]


def find_fillers(tokens: list[str]) -> dict[str, int]:
    found: dict[str, int] = {}
    i = 0
    while i < len(tokens):
        pair = f"{tokens[i]} {tokens[i + 1] if i + 1 < len(tokens) else ''}"
        if pair in PHRASE_FILLERS:
            found[pair] = found.get(pair, 0) + 1
            i += 2
            continue
        if tokens[i] in SINGLE_FILLERS:
            found[tokens[i]] = found.get(tokens[i], 0) + 1
        i += 1
    return found


def _repeated_phrases(tokens: list[str]) -> list[Counted]:
    grams: dict[str, int] = {}
    for i in range(len(tokens) - 2):
        g = tokens[i:i + 3]
        if all(t in STOP or t in SINGLE_FILLERS for t in g):
            continue
        k = " ".join(g)
        grams[k] = grams.get(k, 0) + 1
    kept: list[Counted] = []
    for r in _top(grams, 3, 12):                     # drop near-duplicates that only shift by a word
        if not any(k.term in r.term or r.term in k.term or " ".join(k.term.split()[1:]) == " ".join(r.term.split()[:2]) for k in kept):
            kept.append(r)
    return kept[:5]


def compute_analytics(chunks: list[TranscriptChunk], duration_ms: int) -> Analytics:
    words = flatten_words(chunks)
    has_timings = bool(words)
    if has_timings:
        tokens = [norm(w.w) for w in words]
    else:
        tokens = [t for t in (norm(x) for c in chunks if c.status == "final" for x in c.text.split()) if t]
    n = len(tokens)

    filler_map = find_fillers(tokens)
    fillers = _top(filler_map, 1, 10)
    filler_total = sum(f.count for f in fillers)
    content = [t for t in tokens if t not in STOP and t not in SINGLE_FILLERS and len(t) > 2]
    counts: dict[str, int] = {}
    for t in content:
        counts[t] = counts.get(t, 0) + 1
    repeated_words = _top(counts, max(4, math.ceil(n * 0.02)), 5)

    stutters: list[tuple[str, int | None]] = []
    for i in range(1, len(tokens)):
        if tokens[i] == tokens[i - 1] and tokens[i] not in ("that", "had", "very"):
            stutters.append((f"{tokens[i]} {tokens[i]}", words[i].start if has_timings else None))

    a = Analytics(has_timings, duration_ms, n, fillers=fillers, filler_total=filler_total,
                  fillers_per_100=_round(filler_total / n * 100, 1) if n else 0.0, repeated_words=repeated_words,
                  repeated_phrases=_repeated_phrases(tokens), stutters=stutters[:10])
    if duration_ms > 0 and n > 0:
        a.wpm = _round(n / (duration_ms / 60000))
    if not has_timings:
        return a

    gaps = [Pause(words[i - 1].end, words[i].start - words[i - 1].end, words[i - 1].w, words[i].w)
            for i in range(1, len(words)) if words[i].start - words[i - 1].end >= PAUSE_MS]
    pause_total = sum(g.duration_ms for g in gaps)
    span = words[-1].end - words[0].start
    a.pauses = PauseStats(len(gaps), sum(1 for g in gaps if g.duration_ms >= LONG_PAUSE_MS), sum(1 for g in gaps if g.duration_ms >= VERY_LONG_PAUSE_MS),
                          _round(pause_total / len(gaps)) if gaps else 0, _round(pause_total / span, 2) if span > 0 else 0.0,
                          sorted(gaps, key=lambda g: -g.duration_ms)[:5])
    speaking = span - pause_total
    if speaking > 5000:
        a.articulation_wpm = _round(n / (speaking / 60000))

    end = words[-1].end
    windows: list[PaceWindow] = []
    s = 0
    while s < end:
        length = min(WINDOW_MS, end - s)
        if length >= 10_000:                         # a short tail would give a misleading rate
            cnt = sum(1 for w in words if s <= w.start < s + WINDOW_MS)
            windows.append(PaceWindow(s, _round(cnt / (length / 60000)), cnt))
        s += WINDOW_MS
    if len(windows) >= 2:
        rates = [w.wpm for w in windows]
        mean = sum(rates) / len(rates)
        sd = math.sqrt(sum((r - mean) ** 2 for r in rates) / len(rates))
        a.pace = Pace(windows, _round(mean), min(rates), max(rates), _round(sd / mean, 2) if mean else 0.0,
                      [(w, "faster" if w.wpm >= mean else "slower") for w in windows if w.words >= 8 and (w.wpm >= mean * 1.3 or w.wpm <= mean * 0.7)])
    a.notes = delivery_notes(a)
    return a


def delivery_notes(a: Analytics) -> list[str]:
    """Cautious, evidence-based observations: each states the number it comes from."""
    notes: list[str] = []
    if a.pace:
        p = a.pace
        if p.cv <= 0.15:
            notes.append(f"Your pace was steady: it stayed between {p.min} and {p.max} words per minute across {len(p.windows)} half-minute sections.")
        elif p.cv >= 0.3:
            notes.append(f"Your pace varied a lot: from {p.min} to {p.max} words per minute between sections.")
        else:
            notes.append(f"Your pace varied moderately: {p.min} to {p.max} words per minute between sections.")
    if a.pauses:
        if a.pauses.very_long > 0:
            vl = a.pauses.very_long
            notes.append(f"There {'was 1 pause' if vl == 1 else f'were {vl} pauses'} of 3 seconds or more.")
        if a.pauses.ratio >= 0.25:
            notes.append(f"About {_round(a.pauses.ratio * 100)}% of the time between your first and last word was silence.")
    if a.filler_total > 0 and a.word_count >= 50:
        notes.append(f"Filler words made up {a.fillers_per_100:g} of every 100 words.")
    if len(a.stutters) >= 3:
        notes.append(f"Immediate word repeats happened {len(a.stutters)} times.")
    return notes
