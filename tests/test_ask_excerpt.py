from app.ai.ask import ASK_BUDGET_CHARS, excerpt_for_ask

def section(n, body): return f"## Section {n}\n\n{body}\n\n"
def filler(n): return f"Filler sentence {n} about nothing in particular. " * 40


def test_a_short_file_is_sent_whole():
    e = excerpt_for_ask("# T\n\nshort", "anything")
    assert (e.text, e.partial) == ("# T\n\nshort", False)


def test_a_long_file_stays_under_budget_and_keeps_the_matching_parts():
    doc = "# Big\n\n" + "".join(section(i, "The deadline for the migration is 14 March, owned by Priya." if i == 41 else filler(i)) for i in range(60))
    assert len(doc) > ASK_BUDGET_CHARS * 3
    e = excerpt_for_ask(doc, "Who owns the migration deadline?")
    assert e.partial and len(e.text) <= ASK_BUDGET_CHARS and "14 March, owned by Priya" in e.text


def test_a_general_question_spreads_over_the_whole_file():
    doc = "# Big\n\n" + "".join(section(i, f"Unique-{i} " + filler(i)) for i in range(40))
    e = excerpt_for_ask(doc, "Summarise this file in three bullets.")
    assert e.partial and len(e.text) <= ASK_BUDGET_CHARS
    assert "Unique-0 " in e.text and "Unique-39 " in e.text
    assert e.text.index("Unique-3 ") < e.text.index("Unique-30 ")


def test_one_huge_paragraph_without_headings_still_fits():
    e = excerpt_for_ask("word " * 20_000, "word")
    assert e.partial and len(e.text) <= ASK_BUDGET_CHARS
