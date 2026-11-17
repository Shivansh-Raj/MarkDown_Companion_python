"""Fallback conversion of raw microphone audio to 16 kHz mono int16 (used only if the device refuses that format)."""
from __future__ import annotations

import array

from .wav import SAMPLE_RATE

_WIDTH = {"u8": 1, "i16": 2, "i32": 4, "f32": 4}


def convert(raw: bytes, fmt: str, channels: int, rate: int) -> bytes:
    """Downmix to mono and average-decimate to 16 kHz. `fmt` is one of u8, i16, i32, f32 (little-endian)."""
    width = _WIDTH[fmt]
    raw = raw[: len(raw) // (width * channels) * (width * channels)]
    if fmt == "u8":
        vals = [(b - 128) / 128.0 for b in raw]
    elif fmt == "i16":
        a = array.array("h"); a.frombytes(raw); vals = [v / 32768.0 for v in a]
    elif fmt == "i32":
        a = array.array("i"); a.frombytes(raw); vals = [v / 2147483648.0 for v in a]
    else:
        a = array.array("f"); a.frombytes(raw); vals = list(a)
    mono = [sum(vals[i:i + channels]) / channels for i in range(0, len(vals), channels)] if channels > 1 else vals
    step = rate / SAMPLE_RATE
    out = array.array("h")
    pos = acc = 0.0
    n = 0
    for v in mono:
        acc += v
        n += 1
        pos += 1.0
        if pos >= step:
            out.append(int(max(-1.0, min(1.0, acc / n)) * 32767))
            acc, n, pos = 0.0, 0, pos - step
    return out.tobytes()
