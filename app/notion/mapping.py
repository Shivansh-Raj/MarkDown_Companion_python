"""Helpers for the mapping screen (Qt-free): which properties fit which field, a first guess, and sample values."""
from __future__ import annotations

import re
from datetime import date

from .db import Field, Prop, compatible


def fits(field: Field, prop: Prop) -> bool:
    return prop.type != "title" and compatible(field.kind, prop.type)


def _norm(s: str) -> str:
    return re.sub(r"[^a-z0-9]", "", s.lower())


def suggest_mapping(fields: tuple[Field, ...], props: tuple[Prop, ...]) -> list[dict]:
    """A first guess: a property whose name matches the field's key or label and that fits. Only a suggestion; each
    property is used for at most one field."""
    used: set[str] = set()
    out = []
    for f in fields:
        names = {_norm(f.key), _norm(f.label)}
        if f.key == "duration_min":
            names |= {"duration", "minutes", "length"}
        if f.key == "due":
            names |= {"duedate", "deadline"}
        hit = next((p for p in props if p.name not in used and fits(f, p) and _norm(p.name) in names), None)
        if hit:
            used.add(hit.name)
            out.append({"field": f.key, "property": hit.name})
    return out


def sample_values(destination: str) -> dict:
    today = date.today().isoformat()
    if destination == "practice":
        return {"date": today, "duration_min": 4.5, "words": 620, "pace_wpm": 138, "speaking_wpm": 151,
                "filler_count": 9, "fillers_per_100_words": 1.5, "long_pauses": 2}
    if destination == "tasks":
        return {"due": today, "status": "Not started", "owner": "Sam"}
    return {"date": today, "type": "meeting", "duration_min": 12.5, "tags": ["voice", "meeting"],
            "project": "Apollo", "source": "Markdown Companion"}
