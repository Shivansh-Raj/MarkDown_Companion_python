"""Notion core: client, targeted edits, database mapping, linked copies, push, home page and destinations.
No real network: a fake Notion answers. Mirrors the V1 (Rust) tests so both apps keep the same rules."""
from __future__ import annotations

import random

import pytest

from app.core.settings_manager import SettingsManager
from app.notion import dest, home, sync
from app.notion.client import Client, Created, NotionError, PageHit, Response, clean_token, describe, parse_page_id
from app.notion.db import (Prop, Schema, build_properties, compatible, fields_for, schema_from, template_for, validate_mapping)
from app.notion.edit import single_edit
from app.notion.service import NotionService
from app.notion.store import MemoryTokens, NotionStore, sha
from app.services.persistence import Database

ID = "1429989f-e8ac-4eff-bc8f-57f56486db54"
HOME = "aaaaaaaa-e8ac-4eff-bc8f-57f56486db54"
NEW_DS = "bbbbbbbb-e8ac-4eff-bc8f-57f56486db54"


class Scripted:
    """Plays back canned responses and records the requests."""

    def __init__(self, *replies):
        self.replies = list(replies)
        self.seen = []

    def send(self, token, method, path, body):
        self.seen.append((method, path, body, token))
        r = self.replies.pop(0)
        return r if isinstance(r, Response) else Response(200, r)


def client(http, sleeps=None):
    return Client(http, "tok", sleep=(sleeps.append if sleeps is not None else (lambda s: None)))


@pytest.fixture
def settings(qapp, tmp_path):
    return SettingsManager(Database(tmp_path / "db.sqlite"))


@pytest.fixture
def store(settings, tmp_path):
    return NotionStore(settings, tmp_path / "snaps")


# --- client -------------------------------------------------------------------------------------------
def test_page_ids_from_links_and_bare_ids():
    compact = ID.replace("-", "")
    assert parse_page_id(f"https://www.notion.so/Work/My-Page-{compact}") == ID
    assert parse_page_id(f"https://www.notion.so/{compact}?v=abc#frag") == ID
    assert parse_page_id(ID) == ID and parse_page_id(ID.upper()) == ID
    for bad in ("", "hello", "https://notion.so/abc", "z" * 32, "é"):
        assert parse_page_id(bad) is None


def test_clean_token():
    assert clean_token("  ntn_abc  ") == "ntn_abc"
    for bad in ("  ", "two words", "x" * 501):
        with pytest.raises(NotionError):
            clean_token(bad)


def test_retries_a_rate_limit_using_retry_after_then_succeeds():
    http = Scripted(Response(429, {}, 7), {"name": "MC", "bot": {"workspace_name": "Acme"}})
    waits = []
    assert client(http, waits).me() == ("MC", "Acme")
    assert waits == [7] and len(http.seen) == 2


def test_caps_the_wait_and_gives_up_after_three_attempts():
    http = Scripted(Response(429, {}, 500), Response(529, {}), Response(429, {}, 1))
    waits = []
    with pytest.raises(NotionError, match="slow down"):
        client(http, waits).me()
    assert waits == [30, 1]


@pytest.mark.parametrize("status,needle", [(401, "rejected the token"), (403, "permission"), (404, "not been shared")])
def test_client_errors_are_not_retried_and_hide_internal_detail(status, needle):
    waits = []
    with pytest.raises(NotionError, match=needle) as e:
        client(Scripted(Response(status, {"message": "internal detail"})), waits).me()
    assert "internal detail" not in str(e.value) and waits == []


def test_validation_errors_keep_notions_message():
    assert "body.markdown is too large" in describe(400, {"message": "body.markdown is too large"})


def test_create_page_uses_v2_markdown_and_never_allows_deleting():
    http = Scripted({"id": "new", "url": "https://notion.so/x"})
    made = client(http).create_page(f"https://notion.so/P-{ID.replace('-', '')}", "  My note ", "# Hi\n\n- [ ] a")
    assert made == Created("new", "https://notion.so/x")
    method, path, body, token = http.seen[0]
    assert (method, path, token) == ("POST", "/v1/pages", "tok")
    assert body["parent"]["page_id"] == ID and body["properties"]["title"]["title"][0]["text"]["content"] == "My note"
    assert body["markdown"] == "# Hi\n\n- [ ] a" and body["markdown_version"] == "v2" and "children" not in body
    assert "allow_deleting_content" not in str(body)


def test_bad_create_input_fails_before_any_request():
    http = Scripted()
    c = client(http)
    for args in ((ID, "  ", "x"), ("nonsense", "T", "x"), (ID, "T", "x" * 400_001), (ID, "t" * 2001, "x")):
        with pytest.raises(NotionError):
            c.create_page(*args)
    assert http.seen == []


def test_read_search_and_update():
    http = Scripted({"markdown": "# T", "truncated": True, "unknown_block_ids": ["b1"]})
    page = client(http).read_page(ID)
    assert (page.markdown, page.truncated, page.unknown_block_ids) == ("# T", True, ["b1"])
    assert http.seen[0][1] == f"/v1/pages/{ID}/markdown?markdown_version=v2"

    http = Scripted({"results": [
        {"object": "page", "id": "a", "url": "u1", "properties": {"Name": {"type": "title", "title": [{"plain_text": "Sprint "}, {"plain_text": "plan"}]}}},
        {"object": "page", "id": "b", "url": "u2", "properties": {}}, {"object": "data_source", "id": "c"}]},
        {"results": []})
    c = client(http)
    assert c.search_pages(" sprint ") == [PageHit("a", "Sprint plan", "u1"), PageHit("b", "Untitled", "u2")]
    assert http.seen[0][2]["query"] == "sprint"
    c.search_pages("  ")
    assert "query" not in http.seen[1][2]

    http = Scripted({"markdown": "# New\n", "truncated": False, "warnings": [{"message": "tag fixed"}, "plain"]})
    up = client(http).update_content(ID, "# Old", "# New")
    assert up.warnings == ["tag fixed", "plain"]
    method, path, body, _ = http.seen[0]
    assert (method, path) == ("PATCH", f"/v1/pages/{ID}/markdown")
    assert body["type"] == "update_content" and len(body["update_content"]["content_updates"]) == 1
    assert "allow_deleting_content" not in str(body) and "replace_content" not in str(body)


# --- tokens and the connection ----------------------------------------------------------------------
def test_a_rejected_token_is_never_stored_and_an_accepted_one_is(settings, tmp_path):
    tokens = MemoryTokens()
    svc = NotionService(settings, tokens, Scripted(Response(401, {})), tmp_path / "s")
    with pytest.raises(NotionError):
        svc.connect("ntn_bad")
    assert svc.token() is None and tokens.value is None

    svc = NotionService(settings, tokens, Scripted({"name": "MC", "bot": {"workspace_name": "Acme"}}), tmp_path / "s")
    assert svc.connect(" ntn_ok ")[1] == "Acme"
    assert svc.token() == "ntn_ok" and tokens.value == "ntn_ok"
    svc.disconnect()
    assert svc.token() is None and tokens.value is None and not svc.connected()
    with pytest.raises(NotionError, match="Connect Notion"):
        svc.client()


def test_a_locked_credential_store_is_reported_not_swallowed(settings, tmp_path):
    tokens = MemoryTokens()
    tokens.fail = True
    svc = NotionService(settings, tokens, Scripted({}), tmp_path / "s")
    with pytest.raises(NotionError, match="credential store"):
        svc.connect("ntn_x")


# --- targeted edits -------------------------------------------------------------------------------------
def check(old, new):
    e = single_edit(old, new)
    assert e is not None
    assert old.count(e.old_str) == 1, f"old_str must be unique: {e.old_str!r}"
    assert old.replace(e.old_str, e.new_str, 1) == new
    return e


def test_edit_basics():
    assert single_edit("# a\n", "# a\n") is None
    old = "# Plan\n\n- [ ] write docs\n- [ ] ship it\n\nNotes: later\n"
    e = check(old, old.replace("[ ] write", "[x] write"))
    assert len(e.old_str) < len(old) and "[ ] write" in e.old_str
    check("- [ ] task\n- [ ] task\n- [ ] task\n", "- [ ] task\n- [x] task\n- [ ] task\n")
    check("# A\n\nline one\n\nline three\n", "# A\n\nline one\n\nline two\n\nline three\n")
    check("# A\n\nline one\n\nline two\n\nline three\n", "# A\n\nline one\n\nline three\n")
    check("abc", "abcdef"); check("abcdef", "abc"); check("x", "")
    check("Café — résumé\n\nnaïve ✅ done\n", "Café — résumé\n\nnaïve ❌ not done\n")
    check("日本語のメモ\n日本語のメモ\n", "日本語のメモ\n日本語のノート\n")
    with pytest.raises(NotionError, match="empty"):
        single_edit("", "y")


def test_edit_refuses_unknown_blocks_but_allows_changes_elsewhere():
    old = '# T\n\n<unknown url="https://x" alt="form"/>\n\ntext\n'
    with pytest.raises(NotionError, match="unknown"):
        single_edit(old, old.replace("https://x", "https://y"))
    check(old, old.replace("text", "text changed"))


def test_many_random_edits_round_trip():
    rng = random.Random(7)
    base = "".join(f"## S{i % 5}\n\n- [ ] item {i % 7} of {i % 3}\n\n" for i in range(30))
    for _ in range(80):
        new = base
        at = rng.randrange(len(new))
        new = new[:at] + rng.choice(["X", "é\n", "\n\n- [x] new\n"]) + new[at:]
        if rng.random() < 0.4:
            d = rng.randrange(len(new))
            new = new[:d] + new[d + 5:]
        if new != base:
            check(base, new)


# --- database mapping ----------------------------------------------------------------------------------
def p(name, kind, opts=()):
    return Prop(name.lower(), name, kind, tuple(opts))


SCHEMA = Schema("ds1", "Meetings", (p("Name", "title"), p("When", "date"), p("Minutes", "number"), p("Kind", "select", ["meeting", "call"]),
                                    p("Labels", "multi_select"), p("Notes", "rich_text"), p("Stage", "status", ["Not started", "Done"]),
                                    p("Link", "url"), p("Project", "relation")))


def mp(*pairs):
    return [{"field": f, "property": pr} for f, pr in pairs]


def test_data_source_schema_keeps_only_writable_properties():
    s = schema_from({"id": "ds1", "title": [{"plain_text": "Meet"}, {"plain_text": "ings"}], "properties": {
        "Name": {"id": "title", "name": "Name", "type": "title", "title": {}},
        "Mood": {"id": "a", "name": "Mood", "type": "select", "select": {"options": [{"name": "Good"}, {"name": "Bad"}]}},
        "Total": {"id": "b", "name": "Total", "type": "formula", "formula": {"expression": "1"}},
        "Made": {"id": "c", "name": "Made", "type": "created_time", "created_time": {}}}})
    assert s.title == "Meetings" and [x.name for x in s.properties] == ["Mood", "Name"] and s.properties[0].options == ("Good", "Bad")


def test_compatibility_and_mapping_validation():
    assert compatible("date", "date") and compatible("number", "number") and compatible("list", "multi_select")
    assert not compatible("number", "date") and not compatible("text", "number") and not compatible("date", "status")
    validate_mapping("meetings", SCHEMA, mp(("date", "When"), ("duration_min", "Minutes"), ("type", "Kind"), ("tags", "Labels")))
    validate_mapping("meetings", SCHEMA, [])
    for bad, text in ((mp(("date", "Missing")), "no property"), (mp(("date", "Minutes")), "cannot be written"),
                      (mp(("date", "Name")), "title"), (mp(("date", "Notes"), ("type", "Notes")), "more than one"),
                      (mp(("bogus", "Notes")), "not a field")):
        with pytest.raises(NotionError, match=text):
            validate_mapping("meetings", SCHEMA, bad)


def test_builds_the_properties_for_a_row():
    m = mp(("date", "When"), ("duration_min", "Minutes"), ("type", "Kind"), ("tags", "Labels"), ("project", "Notes"), ("source", "Link"))
    props, warnings = build_properties(SCHEMA, "meetings", m, " Sprint review ", {
        "date": "2026-11-19", "duration_min": 12.5, "type": "meeting", "tags": ["voice", "meeting", "voice", "a,b"],
        "project": "Apollo", "source": "https://example.com"})
    assert warnings == []
    assert props["Name"]["title"][0]["text"]["content"] == "Sprint review"
    assert props["When"]["date"]["start"] == "2026-11-19" and props["Minutes"]["number"] == 12.5
    assert props["Kind"]["select"]["name"] == "meeting"
    assert [x["name"] for x in props["Labels"]["multi_select"]] == ["voice", "meeting", "a b"]
    assert props["Notes"]["rich_text"][0]["text"]["content"] == "Apollo" and props["Link"]["url"] == "https://example.com"


def test_unmapped_values_are_left_out_and_problems_become_warnings():
    props, w = build_properties(SCHEMA, "meetings", mp(("date", "When")), "T", {"duration_min": 5, "tags": ["x"]})
    assert list(props) == ["Name"] and w == []
    m = mp(("date", "When"), ("type", "Stage"), ("project", "Gone"), ("source", "Link"))
    props, w = build_properties(SCHEMA, "meetings", m, "T", {"date": "next friday", "type": "Open", "project": "x", "source": "not a url"})
    assert list(props) == ["Name"]
    text = "\n".join(w)
    assert "not a date" in text and "not one of the options" in text and "no longer has" in text and "web links" in text
    props, w = build_properties(SCHEMA, "meetings", mp(("type", "Stage")), "T", {"type": "done"})
    assert w == [] and props["Stage"]["status"]["name"] == "Done"


def test_long_text_is_split_and_relations_become_references():
    props, _ = build_properties(SCHEMA, "meetings", mp(("project", "Notes")), "T", {"project": "x" * 4500})
    segs = props["Notes"]["rich_text"]
    assert len(segs) == 3 and all(len(s["text"]["content"]) <= 2000 for s in segs)
    s = Schema("t", "Tasks", (p("Name", "title"), p("Meeting", "relation")))
    props, _ = build_properties(s, "tasks", mp(("meeting", "Meeting")), "Do it", {"meeting": "https://notion.so/Page-1429989fe8ac4effbc8f57f56486db54"})
    assert props["Meeting"]["relation"][0]["id"] == ID
    with pytest.raises(NotionError):
        build_properties(Schema("x", "x", (p("A", "rich_text"),)), "meetings", [], "T", {})


def test_templates_map_only_real_fields_to_compatible_columns():
    for d in ("meetings", "practice", "tasks"):
        t = template_for(d, "ds-meetings")
        fields = {f.key: f for f in fields_for(d)}
        for m in t.mapping:
            kind = dict(t.columns)[m["property"]]
            assert m["field"] in fields and compatible(fields[m["field"]].kind, kind), (d, m)
        assert t.properties["Name"] == {"title": {}} and t.columns[0] == ("Name", "title")
        validate_mapping(d, Schema("x", t.title, tuple(p(n, k) for n, k in t.columns)), t.mapping)
    assert all(k != "relation" for _, k in template_for("tasks", None).columns)
    assert template_for("tasks", "ds-m").properties["Meeting"]["relation"]["data_source_id"] == "ds-m"
    with pytest.raises(NotionError):
        template_for("other")


# --- pull, refresh, push -------------------------------------------------------------------------------------
class Page:
    """A tiny Notion with one page whose Markdown can change; PATCH applies edits like Notion would."""

    def __init__(self, text):
        self.remote, self.patches, self.posts = text, 0, 0

    def send(self, token, method, path, body):
        if method == "PATCH":
            self.patches += 1
            c = body["update_content"]["content_updates"][0]
            assert self.remote.count(c["old_str"]) == 1, "Notion would refuse a non-unique old_str"
            self.remote = self.remote.replace(c["old_str"], c["new_str"], 1)
            return Response(200, {"markdown": self.remote, "truncated": False})
        if method == "POST":
            self.posts += 1
            return Response(200, {})
        if path.endswith("/markdown?markdown_version=v2"):
            return Response(200, {"markdown": self.remote, "truncated": False, "unknown_block_ids": ["u1"]})
        return Response(200, {"object": "page", "id": ID, "url": "https://notion.so/x",
                              "properties": {"title": {"type": "title", "title": [{"plain_text": "Team notes"}]}}})


@pytest.fixture
def synced(store, tmp_path):
    """A page pulled into a local file: (page, client, file_key, path)."""
    def make(text="# Plan\n\n- [ ] write\n- [ ] ship\n\n" + "A long untouched paragraph that stays as it is.\n\n" * 20 + "end\n"):
        page = Page(text)
        c = client(page)
        pulled = sync.pull(c, store, tmp_path / "notes", ID)
        return page, c, pulled.file_key, pulled.path
    return make


def test_pull_writes_a_linked_file_and_never_pulls_twice(store, tmp_path):
    page = Page("# Team notes\n\n- [ ] a\n")
    c = client(page)
    first = sync.pull(c, store, tmp_path / "notes", ID)
    assert (first.existing, first.title, first.unknown_blocks) == (False, "Team notes", 1)
    assert open(first.path, encoding="utf-8").read() == "# Team notes\n\n- [ ] a\n"
    assert store.load_snapshot(first.file_key) == "# Team notes\n\n- [ ] a\n"
    info = sync.link_info(store, first.file_key, first.path)
    assert info.local_changed is False and info.url == "https://notion.so/x"
    again = sync.pull(c, store, tmp_path / "notes", f"https://notion.so/Team-{ID.replace('-', '')}")
    assert (again.file_key, again.existing) == (first.file_key, True)
    assert len(list((tmp_path / "notes").iterdir())) == 1 and page.patches == 0 and page.posts == 0


def test_a_truncated_page_is_refused(store, tmp_path):
    class Big(Page):
        def send(self, *a):
            r = super().send(*a)
            if r.body and "truncated" in r.body:
                r.body["truncated"] = True
            return r
    with pytest.raises(NotionError, match="too large"):
        sync.pull(client(Big("# big")), store, tmp_path / "n", ID)
    assert not (tmp_path / "n").exists()


def test_refresh_updates_only_when_just_notion_changed(store, synced):
    page, c, key, path = synced("# v1\n")
    from app.services.synchronization import SynchronizationService
    s = SynchronizationService()
    assert sync.refresh(c, store, s, key, path) == sync.Refreshed("in_sync", False)
    page.remote = "# v2\n"
    assert sync.refresh(c, store, s, key, path) == sync.Refreshed("remote_changed", True)
    assert open(path, encoding="utf-8").read() == "# v2\n" and store.load_snapshot(key) == "# v2\n"
    assert sync.refresh(c, store, s, key, path).state == "in_sync"


def test_refresh_never_overwrites_local_edits(store, synced):
    from app.services.synchronization import SynchronizationService
    page, c, key, path = synced("# v1\n")
    open(path, "w", encoding="utf-8").write("# my edit\n")
    s = SynchronizationService()
    assert sync.link_info(store, key, path).local_changed
    assert sync.refresh(c, store, s, key, path) == sync.Refreshed("local_changed", False)
    page.remote = "# remote edit\n"
    assert sync.refresh(c, store, s, key, path) == sync.Refreshed("both", False)
    assert open(path, encoding="utf-8").read() == "# my edit\n" and store.load_snapshot(key) == "# v1\n"


def test_push_sends_one_targeted_change_with_a_backup(store, synced):
    page, c, key, path = synced()
    assert sync.plan_push(c, store, key, path).kind == "nothing_to_send"
    open(path, "w", encoding="utf-8").write(page.remote.replace("[ ] write", "[x] write"))
    plan = sync.plan_push(c, store, key, path)
    assert plan.kind == "ready" and len(plan.old_str) < 150 and "[x] write" in plan.new_str and page.patches == 0
    before = page.remote
    assert sync.push(c, store, key, path, plan.local_hash) == []
    assert page.remote == before.replace("[ ] write", "[x] write")
    backups = list((store.snapshot_dir / "backups").iterdir())
    assert len(backups) == 1 and backups[0].read_text(encoding="utf-8") == before
    assert sync.plan_push(c, store, key, path).kind == "nothing_to_send"
    assert not sync.link_info(store, key, path).local_changed


def test_push_refuses_when_notion_changed_or_the_note_changed_after_the_preview(store, synced):
    page, c, key, path = synced("# A\n\nline\n")
    open(path, "w", encoding="utf-8").write("# A\n\nline mine\n")
    page.remote = "# A\n\nline theirs\n"
    plan = sync.plan_push(c, store, key, path)
    assert (plan.kind, plan.both) == ("remote_changed", True)
    with pytest.raises(NotionError):
        sync.push(c, store, key, path, "whatever")
    assert page.patches == 0 and page.remote == "# A\n\nline theirs\n"

    page2, c2, key2, path2 = synced("# A\n\nline\n")
    page2.remote = "# A\n\nline\n"
    open(path2, "w", encoding="utf-8").write("# A\n\nfirst\n")
    plan = sync.plan_push(c2, store, key2, path2)
    open(path2, "w", encoding="utf-8").write("# A\n\nsecond\n")
    with pytest.raises(NotionError, match="changed since the preview"):
        sync.push(c2, store, key2, path2, plan.local_hash)


def test_push_stops_when_the_saved_copy_is_missing(store, synced):
    page, c, key, path = synced("# A\n\nline\n")
    open(path, "w", encoding="utf-8").write("# A\n\nchanged\n")
    store._snap_path(key).unlink()
    with pytest.raises(NotionError, match="Refresh from Notion first"):
        sync.plan_push(c, store, key, path)


def test_linking_after_create_records_the_read_back_text(store, tmp_path):
    f = tmp_path / "a.md"
    f.write_text("# A\n", encoding="utf-8")
    page = Page("# A\n\n")
    assert sync.link_created(client(page), store, "k1", "# A\n", Created(ID, "https://notion.so/a"), "A")
    assert store.load_snapshot("k1") == "# A\n\n" and store.link("k1")["local_hash"] == sha("# A\n")


# --- home page and destinations ----------------------------------------------------------------------------
class Workspace:
    """A Notion with a home page that can list children, create pages and databases (answering briefly, as Notion may)."""

    def __init__(self, children=(), existing_dbs=()):
        self.children = [(f"{i + 1:08x}-0000-0000-0000-000000000000", t) for i, t in enumerate(children)]
        self.dbs, self.created, self.trashed, self.db_bodies = list(existing_dbs), 0, set(), []

    def send(self, token, method, path, body):
        if method == "POST" and path == "/v1/pages":
            self.created += 1
            pid = f"{100 + self.created:08x}-0000-0000-0000-000000000000"
            self.children.append((pid, body["properties"]["title"]["title"][0]["text"]["content"]))
            return Response(200, {"id": pid, "url": "https://notion.so/new"})
        if path.startswith(f"/v1/blocks/{HOME}/children"):
            res = [{"type": "child_page", "id": i, "child_page": {"title": t}} for i, t in self.children]
            res += [{"type": "child_database", "id": "d", "child_database": {"title": t}} for t in self.dbs]
            return Response(200, {"results": res, "has_more": False})
        if method == "POST" and path == "/v1/databases":
            self.db_bodies.append(body)
            return Response(200, {"object": "database", "id": "db-1"})
        if path == "/v1/databases/db-1":
            return Response(200, {"id": "db-1", "data_sources": [{"id": NEW_DS, "name": "x"}]})
        if path == f"/v1/data_sources/{NEW_DS}":
            props = self.db_bodies[0]["initial_data_source"]["properties"]
            shaped = {n: {"id": n, "name": n, "type": next(iter(c)), next(iter(c)): c[next(iter(c))]} for n, c in props.items()}
            return Response(200, {"id": NEW_DS, "title": [{"plain_text": "Meetings"}], "properties": shaped})
        if path == f"/v1/pages/{HOME}":
            return Response(200, {"object": "page", "id": HOME, "url": "https://notion.so/home",
                                  "properties": {"title": {"type": "title", "title": [{"plain_text": "Markdown Companion"}]}}})
        if path.startswith("/v1/pages/"):
            pid = path.rsplit("/", 1)[1]
            if any(i == pid for i, _ in self.children):
                return Response(200, {"object": "page", "id": pid, "in_trash": pid in self.trashed})
            return Response(404, {})
        raise AssertionError(f"unexpected {method} {path}")


def test_sections_are_created_once_reused_and_replaced_when_trashed(store):
    ws = Workspace()
    c = client(ws)
    with pytest.raises(NotionError, match="home page"):
        home.ensure_section(c, store, "notes")
    h = home.set_home(c, store, HOME)
    assert (h["title"], h["page_id"]) == ("Markdown Companion", HOME)
    a = home.ensure_section(c, store, "notes")
    assert home.ensure_section(c, store, "notes") == a and ws.created == 1
    assert home.ensure_section(c, store, "meetings") != a and ws.created == 2
    ws.trashed.add(a)
    ws.children = [(i, t) for i, t in ws.children if t != "Notes"]
    assert home.ensure_section(c, store, "notes") != a
    home.set_home(c, store, HOME)
    assert store.section("notes") is None            # a new home forgets the old sections


def test_an_existing_section_page_is_reused_instead_of_duplicated(store):
    ws = Workspace(children=["Notes"])
    c = client(ws)
    home.set_home(c, store, HOME)
    assert home.ensure_section(c, store, "notes") == ws.children[0][0] and ws.created == 0


def test_creates_the_database_under_the_home_page_and_maps_it(store):
    ws = Workspace(children=["Something else"])
    c = client(ws)
    home.set_home(c, store, HOME)
    d = dest.create_destination(c, store, "meetings")
    assert (d["data_source_id"], d["title"]) == (NEW_DS, "Meetings")
    assert {"field": "date", "property": "Date"} in d["mapping"]
    body = ws.db_bodies[0]
    assert body["parent"] == {"type": "page_id", "page_id": HOME} and body["is_inline"] is True
    assert body["initial_data_source"]["properties"]["Name"] == {"title": {}}
    assert store.destination("meetings") == d


def test_will_not_create_a_second_database_with_the_same_name(store):
    ws = Workspace(existing_dbs=["Meetings"])
    c = client(ws)
    home.set_home(c, store, HOME)
    with pytest.raises(NotionError, match="already in your home page"):
        dest.create_destination(c, store, "meetings")
    assert ws.db_bodies == [] and store.destination("meetings") is None
    store.set_home(None)
    with pytest.raises(NotionError, match="home page"):
        dest.create_destination(c, store, "meetings")


class Rows:
    def __init__(self):
        self.posts = []

    def send(self, token, method, path, body):
        if method == "GET" and path == f"/v1/data_sources/{ID}":
            return Response(200, {"id": ID, "title": [{"plain_text": "Meetings DB"}], "properties": {
                "Name": {"id": "title", "name": "Name", "type": "title", "title": {}},
                "When": {"id": "w", "name": "When", "type": "date", "date": {}},
                "Minutes": {"id": "m", "name": "Minutes", "type": "number", "number": {}}}})
        if method == "POST" and path == "/v1/pages":
            self.posts.append(body)
            return Response(200, {"id": "row-1", "url": "https://notion.so/row"})
        raise AssertionError(f"unexpected {method} {path}")


def test_destination_mapping_preview_and_row_creation(store):
    rows = Rows()
    c = client(rows)
    with pytest.raises(NotionError, match="Choose a Notion database"):
        dest.preview_row(c, store, "meetings", "T", {})
    with pytest.raises(NotionError):
        dest.set_destination(c, store, "meetings", ID, mp(("date", "Minutes")))
    assert store.destination("meetings") is None, "a bad mapping is never saved"
    with pytest.raises(NotionError):
        dest.set_destination(c, store, "bogus", ID, [])
    d = dest.set_destination(c, store, "meetings", ID, mp(("date", "When"), ("duration_min", "Minutes")))
    assert d["title"] == "Meetings DB"
    values = {"date": "2026-11-19", "duration_min": 12.5, "type": "meeting"}
    p = dest.preview_row(c, store, "meetings", "Sprint", values)
    assert p.properties["When"]["date"]["start"] == "2026-11-19" and rows.posts == []
    made = dest.create_row(c, store, "meetings", "Sprint review", "## Summary\n\nok", values)
    assert (made.id, made.warnings) == ("row-1", [])
    body = rows.posts[0]
    assert body["parent"] == {"type": "data_source_id", "data_source_id": ID} and body["markdown"] == "## Summary\n\nok"
    assert "type" not in body["properties"] and "allow_deleting_content" not in str(body)
    with pytest.raises(NotionError):
        dest.create_row(c, store, "meetings", "  ", "x", {})
    assert len(rows.posts) == 1
