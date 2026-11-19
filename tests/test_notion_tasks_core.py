"""Notion tasks view, status changes, voice-export values and task rows (mirrors V1's tests)."""
from __future__ import annotations

import pytest

from app.core.settings_manager import SettingsManager
from app.notion import tasks
from app.notion.client import Client, NotionError, Response
from app.notion.store import NotionStore
from app.services.persistence import Database
from app.voice import notion_export as ne
from app.voice.analyses import ActionItem
from app.voice.templates import Section

DS = "1429989f-e8ac-4eff-bc8f-57f56486db54"
PID = "1429989f-e8ac-4eff-bc8f-57f56486db55"
FULL = [{"field": "due", "property": "Due date"}, {"field": "status", "property": "Status"}, {"field": "owner", "property": "Owner"}]


def schema_json(kind="select", with_done=True):
    opts = ([{"name": "Not started"}, {"name": "In progress"}, {"name": "Done"}] if with_done else [{"name": "Open"}, {"name": "Waiting"}])
    return {"id": DS, "title": [{"plain_text": "Tasks"}], "properties": {
        "Name": {"id": "title", "name": "Name", "type": "title", "title": {}},
        "Due date": {"id": "d", "name": "Due date", "type": "date", "date": {}},
        "Status": {"id": "s", "name": "Status", "type": kind, kind: {"options": opts}},
        "Owner": {"id": "o", "name": "Owner", "type": "rich_text", "rich_text": {}}}}


def row(pid, title, due, status, kind="select", parent=DS):
    return {"object": "page", "id": pid, "url": f"https://notion.so/{pid}", "parent": {"type": "data_source_id", "data_source_id": parent},
            "properties": {"Name": {"type": "title", "title": [{"plain_text": title}]},
                           "Due date": {"type": "date", "date": {"start": due} if due else None},
                           "Status": {"type": kind, kind: {"name": status}},
                           "Owner": {"type": "rich_text", "rich_text": [{"plain_text": "Sam"}]}}}


class Fake:
    def __init__(self, rows, kind="select", with_done=True, pages=1):
        self.schema, self.rows, self.pages = schema_json(kind, with_done), rows, pages
        self.queries, self.patches = [], []

    def send(self, token, method, path, body):
        if method == "GET" and path == f"/v1/data_sources/{DS}":
            return Response(200, self.schema)
        if method == "POST" and path.endswith("/query"):
            n = len(self.queries)
            self.queries.append(body)
            more = n + 1 < self.pages
            return Response(200, {"results": self.rows, "has_more": more, "next_cursor": "c" if more else None})
        if method == "GET" and path.startswith("/v1/pages/"):
            r = next((x for x in self.rows if x["id"] == path.rsplit("/", 1)[1]), None)
            return Response(200, r) if r else Response(404, {})
        if method == "PATCH":
            self.patches.append((path, body))
            return Response(200, {})
        raise AssertionError(f"unexpected {method} {path}")


@pytest.fixture
def store_for(qapp, tmp_path):
    def make(mapping=FULL):
        st = NotionStore(SettingsManager(Database(tmp_path / "db.sqlite")), tmp_path / "s")
        st.set_destination("tasks", {"data_source_id": DS, "title": "Tasks", "mapping": mapping})
        return st
    return make


def c(http):
    return Client(http, "t", sleep=lambda s: None)


def test_lists_open_tasks_soonest_first_and_filters_finished_on_the_server(store_for):
    http = Fake([row("b", "later", "2026-12-05", "Not started"), row("a", "soon", "2026-11-20T09:00:00", "In progress"),
                 row("n", "no date", None, "Not started"), row("d", "finished", "2026-11-01", "Done")])
    v = tasks.open_tasks(c(http), store_for())
    assert [r.title for r in v.rows] == ["soon", "later", "no date"] and v.rows[0].due == "2026-11-20"
    assert (v.rows[0].owner, v.rows[0].status) == ("Sam", "In progress") and v.done_status == "Done" and v.read_only_reason is None
    assert http.queries[0]["filter"] == {"property": "Status", "select": {"does_not_equal": "Done"}}


def test_native_status_columns_work(store_for):
    http = Fake([row("a", "t", None, "Not started", "status")], kind="status")
    v = tasks.open_tasks(c(http), store_for())
    assert v.rows[0].status == "Not started" and http.queries[0]["filter"]["status"] == {"does_not_equal": "Done"}


def test_explains_why_ticking_is_unavailable(store_for):
    http = Fake([row("a", "t", None, "Open")], with_done=False)
    v = tasks.open_tasks(c(http), store_for())
    assert v.done_status is None and 'no "Done" option' in v.read_only_reason and "filter" not in http.queries[0]
    v = tasks.open_tasks(c(Fake([row("a", "t", None, "Not started")])), store_for([{"field": "due", "property": "Due date"}]))
    assert "Map a Status column" in v.read_only_reason and len(v.rows) == 1


def test_follows_the_cursor_up_to_three_pages_and_says_so(store_for):
    http = Fake([row("a", "t", None, "Not started")], pages=5)
    v = tasks.open_tasks(c(http), store_for())
    assert len(http.queries) == 3 and v.truncated


def test_needs_a_tasks_database(qapp, tmp_path):
    st = NotionStore(SettingsManager(Database(tmp_path / "db.sqlite")), tmp_path / "s")
    with pytest.raises(NotionError, match="Set up a Tasks database"):
        tasks.open_tasks(c(Fake([])), st)


def test_ticking_writes_only_status_and_only_if_unchanged(store_for):
    http = Fake([row(PID, "t", "2026-11-20", "In progress")])
    st = store_for()
    assert tasks.set_status(c(http), st, PID, "In progress", "Done") == "done"
    assert http.patches == [(f"/v1/pages/{PID}", {"properties": {"Status": {"select": {"name": "Done"}}}})]


def test_changed_or_foreign_tasks_are_never_written(store_for):
    http = Fake([row(PID, "t", None, "Done")])
    st = store_for()
    assert tasks.set_status(c(http), st, PID, "In progress", "Done") == "stale"            # already finished in Notion
    http.rows = [row(PID, "t", None, "In progress", parent="aaaaaaaa-e8ac-4eff-bc8f-57f56486db54")]
    assert tasks.set_status(c(http), st, PID, "In progress", "Done") == "stale"            # a page from another database
    with pytest.raises(NotionError):
        tasks.set_status(c(http), st, PID, "In progress", "Banana")                         # not a real option
    assert http.patches == []


def test_undo_sets_the_previous_status_back(store_for):
    http = Fake([row(PID, "t", None, "Done")])
    assert tasks.set_status(c(http), store_for(), PID, "Done", "In progress") == "done"
    assert http.patches[0][1]["properties"]["Status"]["select"]["name"] == "In progress"


# --- voice export values and task rows -------------------------------------------------------------------------
class Meta:
    def __init__(self, mode):
        self.mode, self.created_at_ms, self.duration_ms, self.title = mode, 1_763_553_600_000, 750_000, "Sprint"


def test_targets_and_values():
    assert [ne.notion_target_for(Meta(m)).key for m in ("meeting", "practice", "note")] == ["meetings", "practice", "notes"]
    v = ne.notion_values(Meta("practice"), {"words": 620})
    assert v["duration_min"] == 12.5 and v["tags"] == ["voice", "practice"] and v["words"] == 620 and v["source"] == "Markdown Companion"
    assert len(v["date"]) == 10


def test_body_has_only_the_sections():
    md = ne.body_from_sections([Section("s", "Summary", "summary", "Done."), Section("a", "Actions", "actions", "- [ ] x", "short")])
    assert md.startswith("## Summary\n\nDone.") and "> Note: short" in md and "\n# " not in md


def test_task_rows_never_invent_dates_or_owners():
    rows = ne.task_rows_for([ActionItem("Send notes", "Sam", "2026-12-01", "after sync"), ActionItem("Book room", None, "by Friday", None),
                             ActionItem("  ", None, None, None), ActionItem("Plain\nwrapped", " ", None, None)], "page-1")
    assert len(rows) == 3
    assert rows[0].values == {"status": "Not started", "due": "2026-12-01", "owner": "Sam", "meeting": "page-1"} and rows[0].body == "Context: after sync"
    assert rows[1].values == {"status": "Not started", "meeting": "page-1"} and rows[1].body == "Due: by Friday"
    assert rows[2].title == "Plain wrapped" and "owner" not in rows[2].values
    assert ne.task_rows_for([ActionItem("x", None, None, None)], None)[0].values == {"status": "Not started"}
    assert ne.task_rows_for([ActionItem("Send  Notes", None, None, None)], None)[0].key == ne.task_rows_for([ActionItem("send notes ", None, None, None)], None)[0].key


def test_tasks_are_grouped_like_local_ones_and_filtered():
    rows = [tasks.NotionTask("1", "later", "2026-12-05", "Not started", None, ""), tasks.NotionTask("2", "late", "2026-11-01", "Not started", None, ""),
            tasks.NotionTask("3", "today", "2026-11-20", "Not started", "Sam", ""), tasks.NotionTask("4", "someday", None, "Not started", None, "")]
    got = [(g, [r.title for r in rs]) for g, rs in tasks.group_tasks(rows, "2026-11-20")]
    assert got == [("overdue", ["late"]), ("today", ["today"]), ("upcoming", ["later"]), ("none", ["someday"])]
    assert [r.id for _, rs in tasks.group_tasks(rows, "2026-11-20", "sam") for r in rs] == ["3"]
    assert tasks.group_tasks(rows, "2026-11-20", "zzz") == []


# --- briefing --------------------------------------------------------------------------------------------------
from app.notion import briefing as br

MEET = "aaaaaaaa-e8ac-4eff-bc8f-57f56486db54"
TASKS = "bbbbbbbb-e8ac-4eff-bc8f-57f56486db54"
M1, M2 = "11111111-e8ac-4eff-bc8f-57f56486db54", "22222222-e8ac-4eff-bc8f-57f56486db54"


def mrow(i, title, project, date):
    return {"object": "page", "id": i, "url": f"https://notion.so/{i}", "created_time": "2026-01-01T00:00:00Z", "properties": {
        "Name": {"type": "title", "title": [{"plain_text": title}]}, "Project": {"type": "rich_text", "rich_text": [{"plain_text": project}]},
        "Date": {"type": "date", "date": {"start": date}}}}


def trow(i, title, meeting):
    return {"object": "page", "id": i, "url": "u", "properties": {"Name": {"type": "title", "title": [{"plain_text": title}]},
            "Status": {"type": "select", "select": {"name": "Not started"}},
            "Meeting": {"type": "relation", "relation": [{"id": meeting}] if meeting else []}}}


class Brief:
    def send(self, token, method, path, body):
        if path == f"/v1/data_sources/{TASKS}":
            return Response(200, {"id": TASKS, "title": [{"plain_text": "Tasks"}], "properties": {
                "Name": {"id": "t", "name": "Name", "type": "title", "title": {}},
                "Status": {"id": "s", "name": "Status", "type": "select", "select": {"options": [{"name": "Not started"}, {"name": "Done"}]}},
                "Meeting": {"id": "m", "name": "Meeting", "type": "relation", "relation": {}}}})
        if path == f"/v1/data_sources/{MEET}/query":
            return Response(200, {"results": [mrow(M2, "Budget review", "Finance", "2026-11-10"), mrow(M1, "Apollo sync", "Apollo", "2026-11-03")]})
        if path == f"/v1/data_sources/{TASKS}/query":
            return Response(200, {"results": [trow("cccccccc-e8ac-4eff-bc8f-57f56486db54", "Send notes", M1), trow("dddddddd-e8ac-4eff-bc8f-57f56486db54", "Pay invoice", M2),
                                              trow("eeeeeeee-e8ac-4eff-bc8f-57f56486db54", "Loose", None)]})
        if path.endswith("/markdown?markdown_version=v2"):
            return Response(200, {"markdown": "## Decisions\n\n- Ship on Friday\n", "truncated": False})
        raise AssertionError(path)


def brief_store(qapp, tmp_path, link=True):
    st = NotionStore(SettingsManager(Database(tmp_path / "db.sqlite")), tmp_path / "s")
    st.set_destination("meetings", {"data_source_id": MEET, "title": "Meetings", "mapping": [{"field": "project", "property": "Project"}, {"field": "date", "property": "Date"}]})
    st.set_destination("tasks", {"data_source_id": TASKS, "title": "Tasks", "mapping": [{"field": "status", "property": "Status"}] + ([{"field": "meeting", "property": "Meeting"}] if link else [])})
    return st


def test_decisions_come_from_the_usual_headings_only():
    md = "## Summary\n\n- not this\n\n## Key takeaways\n\n- Ship on Friday\n- [ ] Priya owns it\n\n## Other\n\n- nor this\n\n## Decisions made\n\n1. Use Postgres\n"
    assert br.extract_decisions(md) == ["Ship on Friday", "Priya owns it", "Use Postgres"] and br.extract_decisions("- a bullet") == []


def test_briefing_picks_matching_meetings_with_decisions_and_linked_tasks(qapp, tmp_path):
    b = br.briefing(c(Brief()), brief_store(qapp, tmp_path), "Apollo roadmap")
    assert [(m.title, m.date, m.decisions) for m in b.meetings] == [("Apollo sync", "2026-11-03", ["Ship on Friday"])] and not b.latest_instead
    assert [t.title for t in b.tasks] == ["Send notes"] and b.tasks_note is None, "only tasks linked to the chosen meetings"


def test_briefing_falls_back_to_the_latest_meetings_and_says_so(qapp, tmp_path):
    st = brief_store(qapp, tmp_path)
    b = br.briefing(c(Brief()), st, "quantum computing")
    assert b.latest_instead and len(b.meetings) == 2 and len(b.tasks) == 2
    none = br.briefing(c(Brief()), st, "")
    assert not none.latest_instead and len(none.meetings) == 2


def test_briefing_without_a_meeting_column_shows_open_tasks_with_a_note(qapp, tmp_path):
    b = br.briefing(c(Brief()), brief_store(qapp, tmp_path, link=False), "Apollo")
    assert len(b.tasks) == 3 and "not linked to meetings" in b.tasks_note
