"""Cuts the continuous 16 kHz stream into chunks for transcription, preferably at a pause in speech.

Pure logic (no audio device), fully unit-tested. Samples are little-endian int16 `bytes`.
"""
from __future__ import annotations

import array
import math
import sys
from dataclasses import dataclass

from .wav import SAMPLE_RATE

FRAME = SAMPLE_RATE // 50                 # 20 ms, in samples
MIN_CHUNK = SAMPLE_RATE * 4               # do not cut before 4 s...
MAX_CHUNK = SAMPLE_RATE * 15              # ...and always cut by 15 s
SILENCE_CUT = SAMPLE_RATE * 6 // 10       # 0.6 s of quiet = a natural boundary
SILENCE_RMS = 0.012                       # below this a frame counts as silence (0..1)


@dataclass
class Chunk:
    index: int
    start_ms: int                         # position on the *active* recording timeline (paused time excluded)
    end_ms: int
    voiced_ms: int                        # how much of the chunk contains sound (the UI skips near-silent chunks)
    pcm: bytes


def to_samples(pcm: bytes) -> array.array:
    a = array.array("h")
    a.frombytes(pcm)
    if sys.byteorder == "big":
        a.byteswap()
    return a


def rms(pcm: bytes) -> float:
    s = to_samples(pcm)
    if not s:
        return 0.0
    return math.sqrt(sum(v * v for v in s) / len(s)) / 32768.0


class Chunker:
    def __init__(self) -> None:
        self._pending = bytearray()
        self._chunk = bytearray()
        self._chunk_start = 0             # samples
        self._total = 0                   # samples accepted so far
        self._silence_run = 0
        self._voiced_frames = 0
        self._next_index = 0

    @property
    def active_ms(self) -> int:
        return (self._total + len(self._pending) // 2) * 1000 // SAMPLE_RATE

    def push(self, pcm: bytes) -> list[Chunk]:
        self._pending += pcm
        out: list[Chunk] = []
        step = FRAME * 2
        while len(self._pending) >= step:
            frame = bytes(self._pending[:step])
            del self._pending[:step]
            quiet = rms(frame) < SILENCE_RMS
            self._total += FRAME
            self._chunk += frame
            if quiet:
                self._silence_run += FRAME
            else:
                self._silence_run = 0
                self._voiced_frames += 1
            n = len(self._chunk) // 2
            if n >= MAX_CHUNK or (n >= MIN_CHUNK and self._silence_run >= SILENCE_CUT):
                c = self._cut()
                if c:
                    out.append(c)
        return out

    def flush(self) -> Chunk | None:
        """End the current chunk (on pause/stop); a tail shorter than a frame is included."""
        if self._pending:
            rest = bytes(self._pending)
            self._pending.clear()
            self._total += len(rest) // 2
            self._chunk += rest
        return self._cut()

    def _cut(self) -> Chunk | None:
        if not self._chunk:
            return None
        pcm = bytes(self._chunk)
        self._chunk.clear()
        start = self._chunk_start
        self._chunk_start = self._total
        c = Chunk(self._next_index, start * 1000 // SAMPLE_RATE, self._total * 1000 // SAMPLE_RATE, self._voiced_frames * 20, pcm)
        self._next_index += 1
        self._silence_run = 0
        self._voiced_frames = 0
        return c
