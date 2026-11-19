"""Voice export to Notion (rows, task rows, no duplicates) and the Notion tasks window, driven with a fake service."""
from __future__ import annotations

from types import SimpleNamespace

from PySide6.QtWidgets import QCheckBox

from conftest import wait_for

from app.notion.client import NotionError
from app.notion.tasks import NotionTask, TasksView
from app.services.persistence import Database
from app.ui.notion_tasks_window import NotionTasksWindow
from app.ui.voice_panels import ExportDialog
from app.voice import templates as tpl
from app.voice.analyses import ActionItem
from app.voice.session import SessionMeta

HOME = {"page_id": "h", "title": "Markdown Companion", "url": ""}
MEET = {"data_source_id": "m", "title": "Meetings DB", "mapping": []}
TASKS = {"data_source_id": "t", "title": "Tasks DB", "mapping": []}


class FakeNotion:
    def __init__(self, dests=None, home=HOME, connected=True):
        self._dests, self._home, self._connected = dests or {}, home, connected
        self.rows, self.pages, self.fail_titles = [], [], set()

    def connected(self): return self._connected
    def home(self): return self._home
    def destination(self, key): return self._dests.get(key)

    def preview_row(self, key, title, values):
        return SimpleNamespace(properties={"Name": title, **{k: v for k, v in values.items() if k == "date"}}, warnings=[], database="DB")

    def create_row(self, key, title, md, values, file_key=None, content=""):
        if title in self.fail_titles:
            raise NotionError("Notion refused it")
        self.rows.append((key, title, values))
        return SimpleNamespace(id=f"id-{len(self.rows)}", url="https://notion.so/x", linked=False, warnings=[])

    def create_page(self, parent, title, md, file_key=None, content="", section=None):
        self.pages.append((title, section))
        return SimpleNamespace(id="pg", url="https://notion.so/p", linked=False, warnings=[])


def meta(mode="meeting"):
    return SessionMeta("1763553600000", "Sprint", mode, 1_763_553_600_000, 750_000, "stopped")


ITEMS = [ActionItem("Send notes", "Sam", "2026-12-01", "after sync"), ActionItem("Book room", None, "by Friday", None)]


def dialog(qapp, tmp_path, notion, mode="meeting", items=ITEMS, props=None):
    db = Database(tmp_path / "db.sqlite")
    ai = SimpleNamespace(notion=notion, save_dir=lambda: tmp_path, theme_name=lambda: "retro")
    secs = [tpl.Section("summary", "Summary", "summary", "Done."), tpl.Section("actions", "Action items", "actions", "- [ ] x")]
    d = ExportDialog(meta(mode), secs, "meeting notes", ai, db, None, action_items=items, properties=props)
    return d, db


def test_notion_option_only_shows_when_connected(qapp, tmp_path):
    d, _ = dialog(qapp, tmp_path, FakeNotion(connected=False))
    assert d.notion_radio.isHidden()
    d, _ = dialog(qapp, tmp_path, FakeNotion())
    assert not d.notion_radio.isHidden()


def test_a_meeting_becomes_a_row_with_tasks_linked_to_it(qapp, tmp_path):
    fake = FakeNotion({"meetings": MEET, "tasks": TASKS})
    d, db = dialog(qapp, tmp_path, fake)
    d.notion_radio.setChecked(True)
    assert d.notion_box.isVisibleTo(d) and d.save_btn.text() == "Save to Notion" and d.save_btn.isEnabled()
    assert d.preview.toPlainText().startswith("## Summary") and "# " not in d.preview.toPlainText().replace("## ", "")
    assert len(d._task_checks) == 2 and all(c.isChecked() for c in d._task_checks.values())
    d.save()
    assert wait_for(lambda: len(fake.rows) == 3 and d.save_btn.isEnabled())
    key, title, values = fake.rows[0]
    assert (key, title) == ("meetings", "Sprint") and values["type"] == "meeting" and values["duration_min"] == 12.5
    tasks = fake.rows[1:]
    assert [t[0] for t in tasks] == ["tasks", "tasks"]
    assert tasks[0][2] == {"status": "Not started", "due": "2026-12-01", "owner": "Sam", "meeting": "id-1"}
    assert tasks[1][2] == {"status": "Not started", "meeting": "id-1"}, "no invented date or owner"
    assert "2 tasks created" in d.message.text() and not d.open_url_btn.isHidden()
    assert all(not c.isEnabled() for c in d._task_checks.values())


def test_tasks_are_not_created_twice_and_one_failure_does_not_stop_the_rest(qapp, tmp_path):
    fake = FakeNotion({"meetings": MEET, "tasks": TASKS})
    fake.fail_titles = {"Send notes"}
    d, db = dialog(qapp, tmp_path, fake)
    d.notion_radio.setChecked(True)
    d.save()
    assert wait_for(lambda: d.save_btn.isEnabled() and "created" in d.message.text())
    assert "1 task created" in d.message.text() and "Not created" in d.message.text() and "Notion refused it" in d.message.text()
    d2, _ = dialog(qapp, tmp_path, fake)                     # reopening the export for the same recording
    assert d2._sent == {"book room"}
    assert not d2._task_checks["book room"].isEnabled() and not d2._task_checks["book room"].isChecked()
    assert d2._task_checks["send notes"].isChecked()


def test_without_databases_it_makes_a_page_in_the_home_section_and_offers_no_tasks(qapp, tmp_path):
    fake = FakeNotion()
    d, _ = dialog(qapp, tmp_path, fake)
    d.notion_radio.setChecked(True)
    assert "Markdown Companion > Meetings" in d.notion_info.text() and "Meetings database" in d.tasks_intro.text()
    d.save()
    assert wait_for(lambda: fake.pages and d.save_btn.isEnabled())
    assert fake.pages == [("Sprint", "meetings")] and fake.rows == []


def test_practice_goes_to_its_database_with_the_speaking_numbers(qapp, tmp_path):
    fake = FakeNotion({"practice": {"data_source_id": "p", "title": "Practice DB", "mapping": []}})
    d, _ = dialog(qapp, tmp_path, fake, mode="practice", items=[], props={"words": 620, "pace_wpm": 138})
    d.notion_radio.setChecked(True)
    d.save()
    assert wait_for(lambda: fake.rows and d.save_btn.isEnabled())
    key, _, values = fake.rows[0]
    assert key == "practice" and values["words"] == 620 and values["pace_wpm"] == 138 and values["tags"] == ["voice", "practice"]


def test_an_untitled_notion_export_cannot_be_saved(qapp, tmp_path):
    d, _ = dialog(qapp, tmp_path, FakeNotion({"meetings": MEET}))
    d.notion_radio.setChecked(True)
    d.notion_title.setText("   ")
    d._update()
    assert not d.save_btn.isEnabled()


# --- tasks window --------------------------------------------------------------------------------------------
class TasksFake:
    def __init__(self, view):
        self.view, self.calls, self.outcome = view, [], "done"

    def open_tasks(self): return self.view

    def set_task_status(self, page, expect, to):
        self.calls.append((page, expect, to))
        if self.outcome == "error":
            raise NotionError("Notion is busy")
        return self.outcome


def tview(done="Done", reason=None):
    rows = [NotionTask("a", "soon", "2000-01-01", "In progress", "Sam", "https://notion.so/a"), NotionTask("b", "later", "2999-01-01", "Not started", None, ""),
            NotionTask("c", "undated", None, "Not started", None, "")]
    return TasksView("Tasks DB", rows, False, done, reason)


def test_tasks_window_lists_groups_and_ticks_with_a_check(qapp):
    fake = TasksFake(tview())
    w = NotionTasksWindow(fake)
    w.reload()
    assert wait_for(lambda: w.view is not None)
    labels = [c.text() for c in w.findChildren(QCheckBox)]
    assert labels == ["soon", "later", "undated"] and not w.overdue.isHidden() and "1 overdue" in w.overdue.text()
    first = w.findChildren(QCheckBox)[0]
    first.click()
    assert wait_for(lambda: fake.calls and not w.undo_bar.isHidden())
    assert fake.calls[0] == ("a", "In progress", "Done") and "Done: soon" in w.undo_label.text()
    w.undo_btn.click()
    assert wait_for(lambda: len(fake.calls) == 2)
    assert fake.calls[1] == ("a", "Done", "In progress") and wait_for(lambda: w.undo_bar.isHidden())


def test_tasks_window_disables_ticking_with_a_reason_and_reports_stale_or_errors(qapp):
    fake = TasksFake(tview(done=None, reason="Map a Status column"))
    w = NotionTasksWindow(fake)
    w.reload()
    assert wait_for(lambda: w.view is not None)
    assert all(not c.isEnabled() for c in w.findChildren(QCheckBox)) and "Map a Status column" in w.reason.text()

    fake = TasksFake(tview())
    w = NotionTasksWindow(fake)
    w.reload()
    assert wait_for(lambda: w.view is not None)
    fake.outcome = "stale"
    w.findChildren(QCheckBox)[0].click()
    assert wait_for(lambda: "changed in Notion" in w.message.text()) and w.undo_bar.isHidden()
    fake.outcome = "error"
    w.findChildren(QCheckBox)[0].click()
    assert wait_for(lambda: "Notion is busy" in w.message.text())


def test_the_home_screen_has_notion_task_and_open_buttons(make_app):
    app = make_app()
    assert app.home.notion_btn is not None and app.home.notion_tasks_btn is not None
