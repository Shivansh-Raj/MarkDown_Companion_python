"""Ask a Notion page or database, quotes naming their section, and saving answers to Notion (mirrors V1)."""
from __future__ import annotations

from types import SimpleNamespace

from conftest import wait_for

from app.ai import ask
from app.notion import askdb
from app.notion.client import Client, PageHit, Response
from app.ui.ask_panel import AskPanel, conversation_html
from app.ui.notion_dialogs import PagePickerDialog

DOC = "# Plan\n\nIntro line.\n\n## Risks\n\nThe vendor may slip by two weeks.\n\n## Owners\n\nPriya owns the migration.\n"


def test_quotes_name_their_section():
    assert ask.heading_for_quote(DOC, "the vendor may slip by two weeks") == "Risks"
    assert ask.heading_for_quote(DOC, "Priya owns the migration") == "Owners"
    assert ask.heading_for_quote(DOC, "nothing like this") is None
    a, src = ask.parse_answer('Priya.\n\nSOURCES:\n- "Priya owns the migration."\n- "invented"', DOC)
    t = ask.with_headings(ask.Turn("Who?", a, src), DOC)
    assert t.headings == {"Priya owns the migration.": "Owners"}
    assert "› Owners" in conversation_html([t], "retro")


def test_archive_keeps_only_verified_quotes_with_section_and_link():
    t = ask.Turn(" Who owns it? ", "Priya.", [("Priya owns the migration.", True), ("made up", False)], headings={"Priya owns the migration.": "Owners"})
    md = ask.archive_markdown(t, "Roadmap", "https://notion.so/r")
    assert "**Question:** Who owns it?" in md and "**Source:** [Roadmap](https://notion.so/r)" in md and "## Answer\n\nPriya." in md
    assert "> Priya owns the migration.\n\n— Owners, [Roadmap](https://notion.so/r)" in md and "made up" not in md
    assert "Verified quotes" not in ask.archive_markdown(ask.Turn("q", "a", [("x", False)]), "notes.md")


def test_database_document_names_page_and_section_and_stays_small():
    pages = [{"title": "Retro", "markdown": "# Retro\n\n## Decisions\n\nWe agreed to ship on Friday.\n\n## Other\n\nCake."},
             {"title": "Sprint", "markdown": "# Sprint\n\nNothing about it."}]
    doc = ask.database_document(pages, "When did we agree to ship?")
    assert "## Retro > Decisions" in doc and "Cake" not in doc
    assert ask.heading_for_quote(doc, "We agreed to ship on Friday") == "Retro > Decisions"
    assert [l for l in ask.database_document(pages, "zzz").split("\n") if l.startswith("## ")] == ["## Retro > Decisions", "## Sprint"]
    big = [{"title": "B", "markdown": "\n".join(f"## S{i}\n\nship {'x' * 1500}" for i in range(30))}]
    assert len(ask.database_document(big, "ship")) <= 10_000 + 200


def row(pid, title):
    return {"object": "page", "id": pid, "url": f"https://notion.so/{pid}", "properties": {"Name": {"type": "title", "title": [{"plain_text": title}]}}}


def test_rank_strip_and_read_only_the_chosen_rows():
    rows = [row(f"r{i}", f"Weekly sync {i}") for i in range(10)] + [row("m", "Migration plan")]
    order = askdb.rank(rows, "Who owns the migration plan?")
    assert order[0] == 10 and len(order) == askdb.MAX_PAGES and order[1:] == [0, 1, 2, 3, 4]
    assert askdb.strip_unknown('a <unknown url="x" alt="f"/> b') == "a  b" and askdb.strip_unknown("broken <unknown url=") == "broken "

    DS = "1429989f-e8ac-4eff-bc8f-57f56486db54"
    reads = []

    class Fake:
        def send(self, token, method, path, body):
            if path == f"/v1/data_sources/{DS}":
                return Response(200, {"id": DS, "title": [{"plain_text": "Meetings"}], "properties": {"Name": {"id": "t", "name": "Name", "type": "title", "title": {}}}})
            if path.endswith("/query"):
                assert body["sorts"][0]["timestamp"] == "last_edited_time"
                return Response(200, {"results": [row("aaaaaaaa-e8ac-4eff-bc8f-57f56486db54", "Retro"), row("bbbbbbbb-e8ac-4eff-bc8f-57f56486db54", "Sprint"),
                                                  row("cccccccc-e8ac-4eff-bc8f-57f56486db54", "Empty")]})
            pid = path.split("/")[3]
            reads.append(pid)
            if pid.startswith("bbbb"):
                return Response(404, {})
            return Response(200, {"markdown": '<unknown url="x"/>' if pid.startswith("cccc") else "# Retro\n\nShip Friday.", "truncated": False})

    got = askdb.pages_for(Client(Fake(), "t", sleep=lambda s: None), DS, "what did the retro agree")
    assert (got["database"], got["rows"], [p["title"] for p in got["pages"]]) == ("Meetings", 3, ["Retro"]) and len(reads) == 3


class FakeNotion:
    def __init__(self):
        self.created, self.db_calls = [], []

    def connected(self): return True
    def home(self): return {"page_id": "h", "title": "MC", "url": ""}

    def create_page(self, parent, title, md, file_key=None, content="", section=None):
        self.created.append((title, section, md))
        return SimpleNamespace(url="https://notion.so/a")

    def db_pages(self, ds, q):
        self.db_calls.append((ds, q))
        return {"database": "Meetings", "rows": 2, "pages": [{"title": "Retro", "url": "u", "markdown": "## Decisions\n\nShip Friday."}]}


def panel(qapp, monkeypatch, reply='Friday.\n\nSOURCES:\n- "Ship Friday."'):
    import app.ui.ask_panel as ap
    sent = {}

    class W:
        def __init__(self, key, system, user, parent=None):
            sent["user"] = user
            from PySide6.QtCore import QObject, Signal

            class S(QObject):
                text, done, stopped, failed, finished = Signal(str), Signal(str), Signal(str), Signal(str), Signal()
            self.s = S()
            self.text, self.done, self.stopped, self.failed, self.finished = self.s.text, self.s.done, self.s.stopped, self.s.failed, self.s.finished

        def start(self):
            self.done.emit(reply)
            self.finished.emit()

        def cancel(self): pass
        def wait(self, *_): pass
        def deleteLater(self): pass
    monkeypatch.setattr(ap, "StreamWorker", W)
    notion = FakeNotion()
    ai = SimpleNamespace(api_key=lambda: "k", theme_name=lambda: "retro", notion=notion)
    return AskPanel(ai), notion, sent


def test_asking_a_notion_page_and_saving_the_answer(qapp, monkeypatch):
    p, notion, sent = panel(qapp, monkeypatch, 'Priya.\n\nSOURCES:\n- "Priya owns the migration."')
    p.open_notion_page("Roadmap", "https://notion.so/r", DOC, partial=True)
    assert "ROADMAP" in p.head.text() and "only the part Notion returned" in p.note.text()
    p.ask("Who owns it?")
    assert wait_for(lambda: p.turns and not p.busy)
    assert p.turns[0].headings == {"Priya owns the migration.": "Owners"} and 'href="save:0"' in p.log.toHtml()
    p.save_to_notion(0)
    assert wait_for(lambda: notion.created)
    title, section, md = notion.created[0]
    assert (title, section) == ("Who owns it?", "answers") and "[Roadmap](https://notion.so/r)" in md and "— Owners" in md
    assert wait_for(lambda: "Saved to Notion" in p.log.toPlainText())
    p.save_to_notion(0)
    assert len(notion.created) == 1, "the same answer is never saved twice"


def test_asking_a_database_reads_rows_per_question(qapp, monkeypatch):
    p, notion, sent = panel(qapp, monkeypatch)
    p.open_database(PageHit("ds1", "Meetings", "https://notion.so/db"))
    p.ask("What ships?")
    assert wait_for(lambda: p.turns and not p.busy)
    assert notion.db_calls == [("ds1", "What ships?")] and "## Retro > Decisions" in sent["user"]
    assert p.turns[0].headings == {"Ship Friday.": "Retro > Decisions"} and "Read 1 of the 2" in p.note.text()


def test_the_home_picker_offers_ask_and_ask_a_database(qapp):
    d = PagePickerDialog(SimpleNamespace(), "page")
    assert d.ask_btn.isHidden() and d.askdb_btn.isHidden()
    d.offer_ask()
    assert not d.ask_btn.isHidden() and not d.askdb_btn.isHidden() and d.choose_btn.text() == "Open as note"
    d.askdb_btn.click()
    assert d.action == "askdb"
