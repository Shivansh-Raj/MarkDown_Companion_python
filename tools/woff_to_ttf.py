"""Unwrap a WOFF 1.0 web font into a plain TrueType/OpenType file (standard library only).

Qt widgets cannot load web-font containers, only raw sfnt fonts. WOFF 1.0 is just the sfnt tables, each optionally
zlib-compressed, so unwrapping is lossless. Usage: python tools/woff_to_ttf.py in.woff out.ttf
"""
from __future__ import annotations

import struct
import sys
import zlib


def woff_to_sfnt(data: bytes) -> bytes:
    if data[:4] != b"wOFF":
        raise ValueError("not a WOFF 1.0 file")
    flavor, _length, num_tables = struct.unpack(">4xIIH", data[:14])
    entries = []
    for i in range(num_tables):
        tag, offset, comp, orig, _sum = struct.unpack(">4sIIII", data[44 + 20 * i:64 + 20 * i])
        blob = data[offset:offset + comp]
        if comp < orig:
            blob = zlib.decompress(blob)
        if len(blob) != orig:
            raise ValueError(f"table {tag!r} has the wrong length")
        entries.append((tag, blob))
    entries.sort(key=lambda e: e[0])
    n = len(entries)
    entry_selector = n.bit_length() - 1
    search_range = (1 << entry_selector) * 16
    out = bytearray(struct.pack(">IHHHH", flavor, n, search_range, entry_selector, n * 16 - search_range))
    offset = 12 + 16 * n
    body = bytearray()
    for tag, blob in entries:
        padded = blob + b"\0" * (-len(blob) % 4)
        checksum = sum(struct.unpack(f">{len(padded) // 4}I", padded)) & 0xFFFFFFFF
        out += struct.pack(">4sIII", tag, checksum, offset + len(body), len(blob))
        body += padded
    return bytes(out + body)


if __name__ == "__main__":
    src, dst = sys.argv[1:3]
    with open(src, "rb") as f, open(dst, "wb") as g:
        g.write(woff_to_sfnt(f.read()))
