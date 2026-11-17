"""Ask AI about the open file: grounded Q&A with verified quotes, through the real note window (fake Groq, no network)."""
import pytest
from PySide6.QtCore import Qt
from PySide6.QtTest import QTest
from conftest import wait_for

DOC = "# Launch plan\n\n- [x] Write the press release\n- [ ] Book the venue for March 14\n\nOwner: Priya\n"


@pytest.fixture
def ctx(make_app, md, monkeypatch):
    state = {"calls": [], "reply": "The venue is not booked yet.\nSOURCES:\n- \"Book the venue for March 14\"\n- \"The venue costs $5000\"", "error": None, "hang": False}

    def fake_stream(key, system, user, on_text, cancel=None):
        state["calls"].append(user)
        if state["hang"]:                                              # a slow answer that only ends when cancelled
            from app.ai.groq import Cancelled
            import time as _t
            while not cancel.is_set():
                _t.sleep(0.005)
            raise Cancelled()
        if state["error"]:
            from app.ai.groq import GroqError
            raise GroqError(state["error"])
        on_text(state["reply"][:12])
        return state["reply"]

    monkeypatch.setattr("app.ai.worker.stream_chat", fake_stream)
    app = make_app()
    app.windows.ai.set_api_key("gsk_test")
    app.settings.set("animations", False)
    path = md("plan.md", DOC)
    w = app.open_path(str(path))
    return app, w, w.ask_panel, state, path


def ask(panel, q):
    panel.ask(q)
    assert wait_for(lambda: panel._worker is None)


def test_ask_button_opens_the_panel_in_place_of_the_file_and_close_restores_it(ctx):
    app, w, p, state, _ = ctx
    assert w.title_bar.ask_btn is not None and w.title_bar.ask_btn.isEnabled()
    w.title_bar.ask_btn.click()
    assert w._ask_open and p.isVisibleTo(w) and not w.view_box.isVisibleTo(w)
    assert not w.progress.isVisibleTo(w)                                # the progress bar steps aside, as in V1
    p.close_btn.click()
    assert not w._ask_open and not p.isVisibleTo(w) and w.view_box.isVisibleTo(w) and w.progress.isVisibleTo(w)


def test_answers_show_only_quotes_found_in_the_file(ctx):
    app, w, p, state, path = ctx
    w.open_ask()
    ask(p, "What is left?")
    text = p.log.toPlainText()
    assert "YOU" in text and "What is left?" in text and "FILE SAYS" in text and "The venue is not booked yet." in text
    assert "“Book the venue for March 14”" in text                    # verified quote shown
    assert "$5000" not in text                                         # invented quote never shown
    assert "Book the venue for March 14" in state["calls"][0] and "Question: What is left?" in state["calls"][0]
    assert path.read_text() == DOC                                     # asking never changes the file


def test_an_answer_with_no_matching_quote_carries_a_warning(ctx):
    app, w, p, state, _ = ctx
    state["reply"] = "Priya pays.\nSOURCES:\n- \"Priya pays the invoice\""
    w.open_ask()
    ask(p, "Who pays?")
    assert "No exact quote could be matched" in p.log.toPlainText()


def test_not_found_is_shown_plainly_without_a_quote_warning(ctx):
    app, w, p, state, _ = ctx
    state["reply"] = "I couldn't find that in this file."
    w.open_ask()
    ask(p, "What is the weather?")
    t = p.log.toPlainText()
    assert "I couldn't find that in this file." in t and "No exact quote" not in t


def test_follow_up_questions_carry_the_conversation_and_clear_resets_it(ctx):
    app, w, p, state, _ = ctx
    w.open_ask()
    assert p.examples.isVisibleTo(p)
    ask(p, "What is left?")
    assert not p.examples.isVisibleTo(p) and p.clear_btn.isVisibleTo(p)
    ask(p, "And who owns it?")
    assert "Q: What is left?" in state["calls"][1] and "A: The venue is not booked yet." in state["calls"][1]
    p.clear()
    assert p.turns == [] and p.examples.isVisibleTo(p) and "What is left?" not in p.log.toPlainText()


def test_example_buttons_ask_straight_away(ctx):
    app, w, p, state, _ = ctx
    w.open_ask()
    p.example_btns[0].click()
    assert wait_for(lambda: p._worker is None) and "What is still left to do?" in state["calls"][0]


def test_enter_sends_and_shift_enter_does_not(ctx):
    app, w, p, state, _ = ctx
    w.open_ask()
    p.input.setPlainText("Who owns it?")
    QTest.keyClick(p.input, Qt.Key.Key_Return, Qt.KeyboardModifier.ShiftModifier)
    assert state["calls"] == []
    QTest.keyClick(p.input, Qt.Key.Key_Return)
    assert wait_for(lambda: p._worker is None) and len(state["calls"]) == 1 and p.input.toPlainText() == ""


def test_missing_key_explains_what_to_do_and_asks_nothing(ctx):
    app, w, p, state, _ = ctx
    app.windows.ai.set_api_key("")
    w.open_ask()
    p.ask("What is left?")
    assert "No Groq API key" in p.error.text() and p._worker is None and state["calls"] == []


def test_a_failed_request_is_shown_and_not_fed_back_as_history(ctx):
    app, w, p, state, _ = ctx
    state["error"] = "model unavailable"
    w.open_ask()
    ask(p, "What is left?")
    assert "PROBLEM" in p.log.toPlainText() and "model unavailable" in p.log.toPlainText()
    state["error"] = None
    ask(p, "Try again")
    assert "Earlier in this conversation" not in state["calls"][1]     # the failed turn is not sent as context


def test_stop_puts_the_question_back_so_it_can_be_edited(ctx):
    app, w, p, state, _ = ctx
    state["hang"] = True
    w.open_ask()
    p.ask("What is left?")
    assert p.busy and p.stop_btn.isVisibleTo(p) and not p.ask_btn.isVisibleTo(p)
    p.stop()
    assert wait_for(lambda: p._worker is None)
    assert p.input.toPlainText() == "What is left?" and p.turns == [] and p.ask_btn.isVisibleTo(p)


def test_while_editing_the_unsaved_draft_is_what_gets_searched(ctx):
    app, w, p, state, path = ctx
    w.enter_edit(DOC, "digest")
    w.editor.setPlainText(DOC + "\n- [ ] Order the cake\n")
    w.open_ask()
    ask(p, "What about cake?")
    assert "Order the cake" in state["calls"][0]
    assert path.read_text() == DOC
    p.close_panel()
    assert w.editor.isVisibleTo(w) and w.editbar.isVisibleTo(w)        # back to the editor, draft intact
    assert "Order the cake" in w.editor.toPlainText()


def test_ask_and_ai_edit_panels_never_show_together(ctx):
    app, w, p, state, _ = ctx
    w.open_ask()
    w.open_ai()
    assert w._ai_open and not w._ask_open and not p.isVisibleTo(w)
    w.open_ask()
    assert w._ask_open and not w._ai_open and not w.ai_panel.isVisibleTo(w)


def test_a_file_change_on_disk_updates_what_later_questions_search(ctx):
    app, w, p, state, path = ctx
    w.open_ask()
    w.update_markdown(DOC + "- [ ] Hire a caterer\n")
    ask(p, "Anything about catering?")
    assert "Hire a caterer" in state["calls"][0]


def test_escape_stops_a_running_answer_then_closes_the_panel(ctx):
    app, w, p, state, _ = ctx
    state["hang"] = True
    w.open_ask()
    p.ask("What is left?")
    w._ask_escape()                                                    # first Esc: stop
    assert wait_for(lambda: p._worker is None) and w._ask_open
    w._ask_escape()                                                    # second Esc: close
    assert not w._ask_open


def test_a_huge_file_is_flagged_as_only_partly_searched(ctx):
    app, w, p, state, _ = ctx
    w.open_ask()
    p.set_source("line of text\n" * 20_000)
    assert p.cut.isVisibleTo(p) is True or p.cut.isHidden() is False
    p.set_source("small")
    assert p.cut.isHidden()
