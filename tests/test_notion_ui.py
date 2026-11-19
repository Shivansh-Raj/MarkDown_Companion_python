"""Notion screens: the note-window panel, the Settings block and the mapping dialog, driven with a fake service."""
from __future__ import annotations

from dataclasses import dataclass

from conftest import wait_for

from app.notion.client import NotionError
from app.notion.db import Prop, Schema
from app.notion.sync import LinkInfo, PushPlan, Refreshed
from app.notion.service import Sent
from app.ui.notion_dialogs import MappingDialog
from app.ui.notion_panel import NotionPanel
from app.ui.notion_settings import NotionSettings


@dataclass
class Tpl:
    title: str
    columns: list


class FakeNotion:
    """Stands in for NotionService: records what the UI asked for."""

    def __init__(self, connected=True, home=None, dests=None, link=None):
        self._connected, self._home, self.dests, self._link = connected, home, dict(dests or {}), link
        self.calls = []
        self.plan = PushPlan("ready", old_str="- [ ] a", new_str="- [x] a", local_hash="h1")
        self.fail = None

    def connected(self): return self._connected
    def home(self): return self._home
    def destination(self, key): return self.dests.get(key)
    def link_info(self, key, path): return self._link

    def _do(self, name, *args, result=None):
        self.calls.append((name, *args))
        if self.fail:
            raise NotionError(self.fail)
        return result

    def create_page(self, parent, title, md, file_key=None, content="", section=None):
        self._link = LinkInfo("p1", "https://notion.so/p1", title, 0, False) if file_key else None
        return self._do("create_page", parent, title, section, result=Sent("p1", "https://notion.so/p1", True, []))

    def create_row(self, key, title, md, values, file_key=None, content=""):
        self._link = LinkInfo("p2", "https://notion.so/p2", title, 0, False)
        return self._do("create_row", key, title, values, result=Sent("p2", "https://notion.so/p2", True, ["Date left empty"]))

    def refresh(self, key, path): return self._do("refresh", result=Refreshed("remote_changed", True))
    def plan_push(self, key, path): return self._do("plan_push", result=self.plan)
    def push(self, key, path, h):
        self._link = LinkInfo("p1", "u", "T", 0, False)
        return self._do("push", h, result=[])

    def connect(self, raw):
        self._connected = True
        return self._do("connect", raw, result=("MC", "Acme"))

    def disconnect(self): self._connected = False; self.calls.append(("disconnect",))
    def test(self): return ("MC", "Acme")
    def clear_home(self): self._home = None
    def template(self, key): return Tpl("Meetings", [("Name", "title"), ("Date", "date")])
    def clear_destination(self, key): self.dests.pop(key, None)


HOME = {"page_id": "h", "title": "Markdown Companion", "url": ""}


def panel(qapp, fake, text="# Plan\n\n- [ ] a\n"):
    p = NotionPanel(fake)
    p.open_for("k", "/tmp/plan.md", text)
    assert wait_for(lambda: not p.busy)
    return p


def test_not_connected_shows_how_to_connect(qapp):
    p = panel(qapp, FakeNotion(connected=False))
    assert "not connected" in p.info.text() and not p.send_box.isVisibleTo(p) and not p.linked_box.isVisibleTo(p)


def test_send_defaults_to_the_home_notes_section_and_links_the_note(qapp):
    fake = FakeNotion(home=HOME)
    p = panel(qapp, fake)
    assert p.send_box.isVisibleTo(p) and p.title_edit.text() == "Plan"
    assert p.dest.itemText(0) == "Markdown Companion > Notes"
    p.send()
    assert wait_for(lambda: p.linked_box.isVisibleTo(p))
    assert fake.calls[0][:3] == ("create_page", "", "Plan") and fake.calls[0][3] == "notes"
    assert "Created in Notion and linked" in p.msg.text() and "Linked to the Notion page" in p.link_label.text()


def test_send_can_add_a_row_to_a_database(qapp):
    fake = FakeNotion(home=HOME, dests={"meetings": {"data_source_id": "d", "title": "Meetings DB", "mapping": []}})
    p = panel(qapp, fake)
    idx = next(i for i in range(p.dest.count()) if p.dest.itemData(i) == ("row", "meetings"))
    assert idx > 0
    p.dest.setCurrentIndex(idx)
    p.send()
    assert wait_for(lambda: p.linked_box.isVisibleTo(p))
    name, key, title, values = fake.calls[0]
    assert (name, key, title) == ("create_row", "meetings", "Plan") and values["source"] == "Markdown Companion" and "date" in values
    assert "Date left empty" in p.msg.text()


def test_an_untitled_send_is_refused_and_errors_are_shown(qapp):
    fake = FakeNotion(home=HOME)
    p = panel(qapp, fake)
    p.title_edit.setText("   ")
    p.send()
    assert "title" in p.msg.text() and fake.calls == []
    p.title_edit.setText("T")
    fake.fail = "Notion is busy"
    p.send()
    assert wait_for(lambda: "Notion is busy" in p.msg.text()) and p.send_box.isVisibleTo(p)


def test_linked_note_refreshes_and_pushes_after_a_preview(qapp):
    fake = FakeNotion(link=LinkInfo("p1", "https://notion.so/p1", "Team notes", 0, True))
    p = panel(qapp, fake)
    assert p.linked_box.isVisibleTo(p) and p.push_btn.isEnabled() and "not in Notion" in p.link_label.text()
    p.refresh()
    assert wait_for(lambda: "Updated this note" in p.msg.text())

    p.preview_push()
    assert wait_for(lambda: p.diff_box.isVisibleTo(p))
    assert p.old_view.toPlainText() == "- [ ] a" and p.new_view.toPlainText() == "- [x] a"
    assert [c[0] for c in fake.calls] == ["refresh", "plan_push"], "previewing must not send anything"
    p.push()
    assert wait_for(lambda: "Sent to Notion" in p.msg.text())
    assert fake.calls[-1] == ("push", "h1") and p.linked_box.isVisibleTo(p)


def test_push_is_blocked_when_notion_changed_and_cancel_returns(qapp):
    fake = FakeNotion(link=LinkInfo("p1", "u", "T", 0, True))
    p = panel(qapp, fake)
    fake.plan = PushPlan("remote_changed", both=True)
    p.preview_push()
    assert wait_for(lambda: "Nothing was sent" in p.msg.text()) and not p.diff_box.isVisibleTo(p)
    fake.plan = PushPlan("ready", old_str="a", new_str="b", local_hash="h")
    p.preview_push()
    assert wait_for(lambda: p.diff_box.isVisibleTo(p))
    p.cancel_btn.click()
    assert p.linked_box.isVisibleTo(p) and all(c[0] != "push" for c in fake.calls)


def test_push_button_is_disabled_without_unsent_edits(qapp):
    p = panel(qapp, FakeNotion(link=LinkInfo("p1", "u", "T", 0, False)))
    assert not p.push_btn.isEnabled() and "No unsent edits" in p.link_label.text()


def test_settings_block_follows_the_connection_state(qapp):
    fake = FakeNotion(connected=False)
    s = NotionSettings(fake)
    assert s.token.isVisibleTo(s) and not s.lower.isVisibleTo(s)
    s.token.setText("ntn_x")
    s.connect_token()
    assert wait_for(lambda: s.lower.isVisibleTo(s) and s.isEnabled())
    assert fake.calls[0] == ("connect", "ntn_x") and "Acme" in s.msg.text() and s.token.text() == ""
    assert not s.rows["meetings"]["create"].isEnabled(), "creating a database needs a home page first"
    fake._home = HOME
    s.refresh()
    assert s.rows["meetings"]["create"].isEnabled() and s.rows["meetings"]["label"].text() == "Not set up"
    fake.dests["meetings"] = {"data_source_id": "d", "title": "Meetings DB", "mapping": [{"field": "date", "property": "When"}]}
    s.refresh()
    assert "Meetings DB" in s.rows["meetings"]["label"].text() and s.rows["meetings"]["edit"].isVisibleTo(s) and not s.rows["meetings"]["create"].isVisibleTo(s)
    s.disconnect()
    assert not s.lower.isVisibleTo(s) and ("disconnect",) in fake.calls


def test_a_failed_connection_shows_the_message_and_stays_disconnected(qapp):
    fake = FakeNotion(connected=False)
    fake.fail = "Notion rejected the token."
    s = NotionSettings(fake)
    s.token.setText("ntn_bad")
    s.connect_token()
    assert wait_for(lambda: "rejected" in s.msg.text() and s.isEnabled()) and not s.lower.isVisibleTo(s)


def test_mapping_dialog_offers_only_compatible_properties_and_suggests(qapp):
    schema = Schema("ds", "Meetings DB", (Prop("1", "Name", "title"), Prop("2", "Date", "date"), Prop("3", "Minutes", "number"),
                                          Prop("4", "Labels", "multi_select"), Prop("5", "Notes", "rich_text")))
    d = MappingDialog(FakeNotion(), "meetings", schema)
    assert d.mapping() == [{"field": "date", "property": "Date"}, {"field": "duration_min", "property": "Minutes"}]   # name matches only
    assert [d.combos["date"].itemData(i) for i in range(d.combos["date"].count())] == ["", "Date", "Notes"]
    assert "Name" not in [d.combos["type"].itemData(i) for i in range(d.combos["type"].count())]
    d.combos["tags"].setCurrentIndex(d.combos["tags"].findData("Labels"))
    assert {"field": "tags", "property": "Labels"} in d.mapping()


def test_the_note_window_has_a_notion_button_that_opens_and_closes_the_panel(make_app, md):
    app = make_app()
    win = app.open_path(str(md("n.md")))
    assert win.notion_panel is not None and win.title_bar.notion_btn is not None
    win.title_bar.notion_btn.click()
    assert win._notion_open and win.notion_panel.isVisibleTo(win) and not win.view_box.isVisibleTo(win)
    win.close_notion()
    assert not win._notion_open and win.view_box.isVisibleTo(win)
    win.open_notion()
    win.open_ask()
    assert not win._notion_open and win._ask_open, "only one side panel is open at a time"
