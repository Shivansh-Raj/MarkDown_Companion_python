"""Briefing box and Notion tasks in the follow-up check (V2), driven with a fake Notion."""
from __future__ import annotations

from types import SimpleNamespace

from conftest import wait_for

from app.notion.briefing import BriefMeeting, Briefing
from app.notion.tasks import NotionTask
from app.ui.briefing_box import BriefingBox
from app.voice import followups as fu


class FakeNotion:
    def __init__(self, dests=("meetings",), connected=True):
        self._dests, self._connected, self.calls = set(dests), connected, []

    def connected(self): return self._connected
    def destination(self, key): return {"title": key} if key in self._dests else None

    def briefing(self, topic):
        self.calls.append(topic)
        t = NotionTask("t1", "Send notes", "2026-12-01", "Not started", "Sam", "https://notion.so/t1")
        return Briefing("Meetings", [BriefMeeting("Apollo sync", "2026-11-03", "https://notion.so/m1", ["Ship on Friday"])], False, [t], None)


def test_the_briefing_box_shows_only_for_meetings_with_a_meetings_database(qapp):
    b = BriefingBox(FakeNotion())
    b.setup("meeting", "")
    assert not b.isHidden()
    for mode in ("practice", "note"):
        b.setup(mode, "")
        assert b.isHidden()
    for notion in (FakeNotion(dests=()), FakeNotion(connected=False), None):
        box = BriefingBox(notion)
        box.setup("meeting", "")
        assert box.isHidden()


def test_the_briefing_lists_meetings_decisions_and_tasks(qapp):
    fake = FakeNotion()
    b = BriefingBox(fake)
    b.setup("meeting", "Apollo")
    b.show()
    b.open_card()
    assert wait_for(lambda: "Apollo sync" in b.view.toPlainText())
    text = b.view.toPlainText()
    assert fake.calls == ["Apollo"] and "Ship on Friday" in text and "Send notes" in text and "OPEN TASKS (1)" in text and "Sam" in text
    b.close_card()
    assert b.card.isHidden() and not b.btn.isHidden()


def test_a_notion_item_survives_the_saved_report():
    item = fu.PriorItem("notion:t1", "Send notes", "Sam", "2026-12-01", "Notion: Tasks", "2026-12-01", "open",
                        notion={"page": "t1", "status": "Not started", "doneStatus": "Done", "url": "u"})
    report = fu.FollowUpReport(1, [item], [fu.FollowUp("notion:t1", "resolved", "we sent the notes", "")], [], 0)
    again = fu.FollowUpReport.from_json(report.to_json())
    assert again.items[0].notion == {"page": "t1", "status": "Not started", "doneStatus": "Done", "url": "u"}
