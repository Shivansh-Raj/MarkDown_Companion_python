"""Database destinations: a Notion database (a "data source"), a mapping of MC's fields onto its properties, and the
property JSON for a new row. No schema is hardcoded: the mapping is checked against the live database every time a
row is built, and anything that no longer fits is skipped with a warning instead of failing.
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any

from .client import NotionError, parse_page_id

TEXT, NUMBER, DATE, LIST, RELATION = "text", "number", "date", "list", "relation"


@dataclass(frozen=True)
class Field:
    key: str
    label: str
    kind: str


FIELDS: dict[str, tuple[Field, ...]] = {
    "meetings": (Field("date", "Date", DATE), Field("type", "Type", TEXT), Field("duration_min", "Duration (minutes)", NUMBER),
                 Field("tags", "Tags", LIST), Field("project", "Project", TEXT), Field("source", "Source", TEXT)),
    "practice": (Field("date", "Date", DATE), Field("duration_min", "Duration (minutes)", NUMBER), Field("words", "Words", NUMBER),
                 Field("pace_wpm", "Pace (words per minute)", NUMBER), Field("speaking_wpm", "Speaking pace (words per minute)", NUMBER),
                 Field("filler_count", "Filler words", NUMBER), Field("fillers_per_100_words", "Fillers per 100 words", NUMBER),
                 Field("long_pauses", "Long pauses", NUMBER)),
    "tasks": (Field("due", "Due date", DATE), Field("status", "Status", TEXT), Field("owner", "Owner (as text)", TEXT),
              Field("meeting", "Meeting page", RELATION)),
}
DESTINATIONS = tuple(FIELDS)
LABELS = {"meetings": "Meetings", "practice": "Practice sessions", "tasks": "Tasks"}


def fields_for(destination: str) -> tuple[Field, ...]:
    if destination not in FIELDS:
        raise NotionError("Unknown destination.")
    return FIELDS[destination]


@dataclass(frozen=True)
class Prop:
    id: str
    name: str
    type: str
    options: tuple[str, ...] = ()


@dataclass(frozen=True)
class Schema:
    id: str
    title: str
    properties: tuple[Prop, ...]


# The property types MC can write to. Formulas, rollups, ids and timestamps are set by Notion.
WRITABLE = ("title", "rich_text", "number", "select", "multi_select", "status", "date", "url", "relation")


def schema_from(v: Any) -> Schema | None:
    if not isinstance(v, dict) or not isinstance(v.get("id"), str) or not isinstance(v.get("properties"), dict):
        return None
    title = "".join(t.get("plain_text", "") for t in v.get("title", []) if isinstance(t, dict))
    props = []
    for p in v["properties"].values():
        if not isinstance(p, dict) or p.get("type") not in WRITABLE or not p.get("id") or not p.get("name"):
            continue
        cfg = p.get(p["type"]) or {}
        opts = tuple(o["name"] for o in cfg.get("options", []) if isinstance(o, dict) and isinstance(o.get("name"), str)) \
            if isinstance(cfg, dict) else ()
        props.append(Prop(p["id"], p["name"], p["type"], opts))
    props.sort(key=lambda p: p.name.lower())
    return Schema(v["id"], title if title.strip() else "Untitled database", tuple(props))


def compatible(field_kind: str, prop_type: str) -> bool:
    return prop_type in {TEXT: ("rich_text", "select", "status", "url"), NUMBER: ("number", "rich_text"),
                         DATE: ("date", "rich_text"), LIST: ("multi_select", "rich_text"), RELATION: ("relation",)}[field_kind]


def validate_mapping(destination: str, schema: Schema, mapping: list[dict]) -> None:
    """Raises NotionError naming the first problem, so the settings screen can show it."""
    fields = {f.key: f for f in fields_for(destination)}
    used: set[str] = set()
    for m in mapping:
        f = fields.get(m["field"])
        if f is None:
            raise NotionError(f"\"{m['field']}\" is not a field of this destination.")
        p = next((p for p in schema.properties if p.name == m["property"]), None)
        if p is None:
            raise NotionError(f"The database has no property \"{m['property']}\".")
        if p.type == "title":
            raise NotionError("The title property is always filled with the title; it cannot be mapped to another field.")
        if not compatible(f.kind, p.type):
            raise NotionError(f"{f.label} cannot be written into \"{p.name}\" ({p.type}).")
        if p.name in used:
            raise NotionError(f"\"{p.name}\" is used for more than one field.")
        used.add(p.name)


def _rich(text: str) -> list[dict]:
    # Notion allows 2,000 characters per text segment, so longer values are split.
    return [{"text": {"content": text[i:i + 2000]}} for i in range(0, len(text), 2000)] or [{"text": {"content": ""}}]


def _as_text(v: Any) -> str | None:
    if isinstance(v, str):
        return v.strip() or None
    if isinstance(v, (int, float)) and not isinstance(v, bool):
        return str(v)
    if isinstance(v, list):
        parts = [x for x in v if isinstance(x, str)]
        return ", ".join(parts) if parts else None
    return None


_ISO_DATE = re.compile(r"^\d{4}-\d{2}-\d{2}(T.*)?$")


def build_properties(schema: Schema, destination: str, mapping: list[dict], title: str,
                     values: dict[str, Any]) -> tuple[dict, list[str]]:
    """The `properties` for a new row, plus what was left out and why. `values` maps field keys to str, number or list."""
    title_prop = next((p for p in schema.properties if p.type == "title"), None)
    if title_prop is None:
        raise NotionError("This database has no title property.")
    fields = {f.key: f for f in fields_for(destination)}
    props: dict[str, Any] = {title_prop.name: {"title": _rich(title.strip())}}
    warnings: list[str] = []
    for m in mapping:
        value = values.get(m["field"])
        field = fields.get(m["field"])
        if value is None or field is None:
            continue
        p = next((p for p in schema.properties if p.name == m["property"]), None)
        if p is None:
            warnings.append(f"{field.label}: the database no longer has \"{m['property']}\", so it was left empty.")
            continue
        if p.type == "title" or not compatible(field.kind, p.type):
            warnings.append(f"{field.label}: \"{p.name}\" is now a {p.type} property, which does not fit, so it was left empty.")
            continue
        out: Any = None
        if p.type == "rich_text":
            t = _as_text(value)
            out = {"rich_text": _rich(t)} if t else None
        elif p.type == "number":
            out = {"number": value} if isinstance(value, (int, float)) and not isinstance(value, bool) else None
        elif p.type == "select":
            t = _as_text(value)
            out = {"select": {"name": t.replace(",", " ")}} if t else None
        elif p.type == "status":
            t = _as_text(value)
            if t:
                opt = next((o for o in p.options if o.lower() == t.lower()), None)
                if opt:
                    out = {"status": {"name": opt}}
                else:
                    warnings.append(f"{field.label}: \"{t}\" is not one of the options of \"{p.name}\" ({', '.join(p.options)}), so it was left empty.")
        elif p.type == "multi_select":
            if isinstance(value, list):
                seen, names = set(), []
                for x in value:
                    n = x.replace(",", " ").strip() if isinstance(x, str) else ""
                    if n and n.lower() not in seen:
                        seen.add(n.lower())
                        names.append(n)
                out = {"multi_select": [{"name": n} for n in names[:100]]}
        elif p.type == "date":
            if isinstance(value, str):
                s = value.strip()
                if _ISO_DATE.match(s):
                    out = {"date": {"start": s}}
                else:
                    warnings.append(f"{field.label}: \"{s}\" is not a date, so it was left empty.")
        elif p.type == "url":
            t = _as_text(value)
            if t and t.startswith(("http://", "https://")):
                out = {"url": t}
            elif t:
                warnings.append(f"{field.label}: only web links fit a URL property, so it was left empty.")
        elif p.type == "relation":
            pid = parse_page_id(value) if isinstance(value, str) else None
            out = {"relation": [{"id": pid}]} if pid else None
        if out is not None:
            props[p.name] = out
    return props, warnings


@dataclass
class Template:
    title: str
    columns: list[tuple[str, str]]       # (column name, Notion type); the title column first
    properties: dict
    mapping: list[dict]


def template_for(destination: str, meetings_source: str | None = None) -> Template:
    """The database to create for a destination. Tasks gets a relation to Meetings when there is a Meetings database."""
    num = {"number": {"format": "number"}}
    cols: list[tuple[str, str, dict]]
    if destination == "meetings":
        cols = [("date", "Date", {"date": {}}), ("type", "Type", {"select": {}}), ("duration_min", "Duration (min)", num),
                ("tags", "Tags", {"multi_select": {}}), ("project", "Project", {"rich_text": {}}), ("source", "Source", {"rich_text": {}})]
    elif destination == "practice":
        cols = [("date", "Date", {"date": {}}), ("duration_min", "Duration (min)", num), ("words", "Words", num),
                ("pace_wpm", "Pace (wpm)", num), ("speaking_wpm", "Speaking pace (wpm)", num), ("filler_count", "Filler words", num),
                ("fillers_per_100_words", "Fillers per 100 words", num), ("long_pauses", "Long pauses", num)]
    elif destination == "tasks":
        # Notion's API does not create "status" columns, so a select with the usual options is used.
        cols = [("due", "Due date", {"date": {}}),
                ("status", "Status", {"select": {"options": [{"name": "Not started"}, {"name": "In progress"}, {"name": "Done"}]}}),
                ("owner", "Owner", {"rich_text": {}})]
        if meetings_source:
            cols.append(("meeting", "Meeting", {"relation": {"data_source_id": meetings_source, "single_property": {}}}))
    else:
        raise NotionError("Unknown destination.")
    properties: dict[str, Any] = {"Name": {"title": {}}}
    columns = [("Name", "title")]
    mapping = []
    for key, name, cfg in cols:
        columns.append((name, next(iter(cfg))))
        mapping.append({"field": key, "property": name})
        properties[name] = cfg
    return Template(LABELS[destination], columns, properties, mapping)
