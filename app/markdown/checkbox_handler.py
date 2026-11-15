"""Locating and toggling GitHub-style task items in Markdown source, line-precisely."""
from __future__ import annotations

import re

# optional blockquote markers, indent, list marker, then [ ] / [x]
TASK_RE = re.compile(r"^((?:\s*>)*\s*(?:[-*+]|\d{1,9}[.)])\s+\[)([ xX])(\](?:\s|$))")
_LINE_RE = re.compile(r"[^\r\n]*(?:\r\n|\n|\r)|[^\r\n]+")


class CheckboxMismatch(Exception):
    """The line at the requested index is no longer the task the user clicked."""


def split_lines_keepends(text: str) -> list[str]:
    """Split like markdown-it does (\\r\\n, \\n, \\r only) keeping terminators intact."""
    return _LINE_RE.findall(text)


def parse_task(line: str) -> bool | None:
    """True/False for a checked/unchecked task line, None if the line isn't a task."""
    m = TASK_RE.match(line)
    return None if m is None else m.group(2) in "xX"


def toggle_task_line(text: str, line_no: int, expected_checked: bool) -> str:
    """Return `text` with the task on `line_no` (0-based) flipped; all else byte-identical."""
    lines = split_lines_keepends(text)
    if not 0 <= line_no < len(lines):
        raise CheckboxMismatch("line out of range")
    m = TASK_RE.match(lines[line_no])
    if m is None:
        raise CheckboxMismatch("line is no longer a task item")
    if (m.group(2) in "xX") != expected_checked:
        raise CheckboxMismatch("task state differs from what was displayed")
    mark = " " if expected_checked else "x"
    lines[line_no] = lines[line_no][: m.start(2)] + mark + lines[line_no][m.end(2):]
    return "".join(lines)
