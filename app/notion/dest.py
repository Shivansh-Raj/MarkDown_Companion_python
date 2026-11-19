"""Saving and using a database destination: validate the user's mapping against the live database, then build and create
rows with it."""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from .client import Client, NotionError
from .db import build_properties, fields_for, template_for, validate_mapping
from .store import NotionStore


def set_destination(client: Client, store: NotionStore, key: str, data_source: str, mapping: list[dict]) -> dict:
    """Saves the database and mapping after checking them against the database's current properties."""
    fields_for(key)
    schema = client.data_source_schema(data_source)
    validate_mapping(key, schema, mapping)
    dest = {"data_source_id": schema.id, "title": schema.title, "mapping": mapping}
    store.set_destination(key, dest)
    return dest


def _load(store: NotionStore, key: str) -> dict:
    dest = store.destination(key)
    if not dest:
        raise NotionError("Choose a Notion database for this first (Settings > Notion).")
    return dest


@dataclass
class RowPreview:
    database: str
    properties: dict
    warnings: list[str]


def preview_row(client: Client, store: NotionStore, key: str, title: str, values: dict[str, Any]) -> RowPreview:
    """What a new row would contain. Reads the database's current properties; changes nothing in Notion."""
    dest = _load(store, key)
    props, warnings = build_properties(client.data_source_schema(dest["data_source_id"]), key, dest["mapping"], title, values)
    return RowPreview(dest["title"], props, warnings)


@dataclass
class RowCreated:
    id: str
    url: str
    warnings: list[str]


def create_row(client: Client, store: NotionStore, key: str, title: str, markdown: str, values: dict[str, Any]) -> RowCreated:
    """Adds a row with the mapped properties and the Markdown as the page body. Always a new row."""
    if not title.strip():
        raise NotionError("Give it a title.")
    dest = _load(store, key)
    props, warnings = build_properties(client.data_source_schema(dest["data_source_id"]), key, dest["mapping"], title, values)
    made = client.create_row(dest["data_source_id"], props, markdown)
    return RowCreated(made.id, made.url, warnings)


def create_destination(client: Client, store: NotionStore, key: str) -> dict:
    """Creates the database for a destination under the home page and saves the mapping to its columns.
    Refuses if a database with that name is already there, so nothing is duplicated."""
    home = store.home()
    if not home:
        raise NotionError("Choose a home page first (Settings > Notion).")
    meetings = (store.destination("meetings") or {}).get("data_source_id")
    t = template_for(key, meetings)
    if t.title in client.child_database_titles(home["page_id"]):
        raise NotionError(f"A database called \"{t.title}\" is already in your home page. Use Choose existing to pick it instead.")
    ds = client.create_database(home["page_id"], t.title, t.properties)
    return set_destination(client, store, key, ds, t.mapping)
