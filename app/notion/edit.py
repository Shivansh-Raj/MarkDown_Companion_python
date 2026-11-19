"""Turns "this text became that text" into one targeted Notion `update_content` edit.

Notion's search-and-replace needs an `old_str` that matches exactly one place. The unchanged start and end are cut off
both texts, then the changed middle is widened with surrounding text until it is unique, so only the changed part of the
page is touched, never the whole page.
"""
from __future__ import annotations

from dataclasses import dataclass

from .client import NotionError

STEP = 40


@dataclass(frozen=True)
class Edit:
    old_str: str
    new_str: str


def _common_prefix(a: str, b: str) -> int:
    n = 0
    for x, y in zip(a, b):
        if x != y:
            break
        n += 1
    return n


def _common_suffix(a: str, b: str) -> int:
    n = 0
    for x, y in zip(reversed(a), reversed(b)):
        if x != y:
            break
        n += 1
    return n


def single_edit(old: str, new: str) -> Edit | None:
    """None when nothing changed. Raises NotionError when the change cannot be expressed safely."""
    if old == new:
        return None
    if not old.strip():
        raise NotionError("The Notion page is empty, so there is nothing to anchor the edit to. Add some text in Notion first.")
    pre = _common_prefix(old, new)
    suf = min(_common_suffix(old[pre:], new[pre:]), len(old) - pre, len(new) - pre)
    before = after = 0
    while True:
        start = max(0, pre - before)
        end = min(len(old), len(old) - suf + after)
        old_str = old[start:end]
        if old_str and old.count(old_str) == 1:
            nstart = max(0, pre - before)
            nend = min(len(new), len(new) - suf + after)
            return _finish(old_str, new[nstart:nend])
        can_before, can_after = start > 0, end < len(old)
        if not can_before and not can_after:
            raise NotionError("This change cannot be sent as a targeted edit.")
        if can_before:
            before += STEP
        if can_after:
            after += STEP


def _finish(old_str: str, new_str: str) -> Edit:
    if "<unknown" in old_str or "<unknown" in new_str:
        raise NotionError("This change touches a block Markdown cannot show (an <unknown> tag). Edit that part in Notion.")
    return Edit(old_str, new_str)
