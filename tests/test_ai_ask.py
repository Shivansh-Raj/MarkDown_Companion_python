"""Ask-the-file core (ported from V1's vitest cases): grounded prompts, verified quotes, honest limits."""
from app.ai import ask

DOC = "# Launch plan\n\n- [x] Write the **press release**\n- [ ] Book the venue for March 14\n\nOwner: Priya"


def test_prompt_demands_the_file_only_and_explains_checkboxes():
    p = ask.build_system_prompt()
    assert "ONLY what is written" in p and "'- [x]' is a completed task" in p and ask.NOT_FOUND in p


def test_user_prompt_has_document_question_and_recent_turns():
    u = ask.build_user_prompt("Who owns it?", [ask.Turn("What is left?", "Book the venue.")], DOC)
    assert "Q: What is left?" in u and "Document:" in u and "Question: Who owns it?" in u
    assert "Earlier in this conversation" not in ask.build_user_prompt("x", [], DOC)
    many = [ask.Turn(f"q{i}", "a") for i in range(9)]
    assert "q8" in ask.build_user_prompt("x", many, DOC) and "q4" not in ask.build_user_prompt("x", many, DOC)     # only the last four


def test_source_quotes_are_checked_against_the_file_ignoring_markdown_and_punctuation():
    raw = 'The venue is not booked yet.\nSOURCES:\n- "Book the venue for March 14"\n- "Write the press release"\n- "The venue costs $5000"'
    answer, sources = ask.parse_answer(raw, DOC)
    assert answer == "The venue is not booked yet."
    assert sources == [("Book the venue for March 14", True), ("Write the press release", True), ("The venue costs $5000", False)]


def test_replies_without_sources_and_the_not_found_answer():
    assert ask.parse_answer("Priya.", DOC) == ("Priya.", [])
    assert ask.is_not_found("I couldn't find that in this file.") and not ask.is_not_found("Priya.")


def test_at_most_three_unique_sources():
    raw = 'A.\nSOURCES:\n- "Owner: Priya"\n- "Owner: Priya"\n- "Launch plan"\n- "press release"\n- "Book the venue"'
    assert [q for q, _ in ask.parse_answer(raw, DOC)[1]] == ["Owner: Priya", "Launch plan", "press release"]


def test_very_large_files_are_cut_on_a_line_boundary_and_flagged():
    assert ask.limit_doc("short") == ("short", False)
    text, cut = ask.limit_doc("line of text\n" * (ask.MAX_ASK_CHARS // 10))
    assert cut and len(text) <= ask.MAX_ASK_CHARS and text.endswith("line of text")


def test_conversation_shows_verified_quotes_and_warns_when_none_match():
    turns = [ask.Turn("What is left?", "Book the venue.", [("Book the venue for March 14", True), ("invented", False)]),
             ask.Turn("Who pays?", "Priya pays.", [("Priya pays", False)]),
             ask.Turn("Who owns it?", ask.NOT_FOUND, []),
             ask.Turn("Anything?", "Groq request failed", error=True)]
    md = ask.conversation_markdown(turns, "And then?", "Streaming…")
    assert '> “Book the venue for March 14”' in md and "invented" not in md          # unverified quotes are never shown
    assert md.count("No exact quote could be matched") == 1 and "**PROBLEM**" in md
    assert md.count("\n> ") == 1                                                      # each quote is its own blockquote
    two = ask.conversation_markdown([ask.Turn("q", "a", [("one", True), ("two", True)])])
    assert "> “one”\n\n> “two”" in two
    assert md.count("**YOU**") == 5 and "Streaming…" in md and "Reading the file" in ask.conversation_markdown([], "q", "")
