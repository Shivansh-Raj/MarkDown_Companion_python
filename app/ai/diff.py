"""GitHub-style line diff: added / removed lines with collapsed unchanged context."""
from __future__ import annotations

import difflib
from dataclasses import dataclass


@dataclass(frozen=True)
class Row:
    kind: str          # "add" | "del" | "ctx" | "gap"
    text: str = ""
    hidden: int = 0    # for "gap": how many unchanged lines are collapsed


def diff_lines(old: str, new: str) -> list[Row]:
    a, b = old.split("\n"), new.split("\n")
    rows: list[Row] = []
    for tag, i1, i2, j1, j2 in difflib.SequenceMatcher(a=a, b=b, autojunk=False).get_opcodes():
        if tag == "equal":
            rows += [Row("ctx", t) for t in a[i1:i2]]
        else:
            rows += [Row("del", t) for t in a[i1:i2]]
            rows += [Row("add", t) for t in b[j1:j2]]
    return rows


def with_context(rows: list[Row], context: int = 2) -> list[Row]:
    changed = [r.kind in ("add", "del") for r in rows]
    out: list[Row] = []
    hidden = 0
    for i, r in enumerate(rows):
        near = any(changed[j] for j in range(max(0, i - context), min(len(rows), i + context + 1)))
        if near:
            if hidden:
                out.append(Row("gap", hidden=hidden))
            hidden = 0
            out.append(r)
        else:
            hidden += 1
    if hidden and out:
        out.append(Row("gap", hidden=hidden))
    return out


def has_changes(rows: list[Row]) -> bool:
    return any(r.kind in ("add", "del") for r in rows)
