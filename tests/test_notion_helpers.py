from app.notion.db import Field, Prop
from app.notion.mapping import fits, sample_values, suggest_mapping
from app.notion.note import prepare_for_notion


def test_plain_markdown_is_unchanged_and_titled_from_the_heading():
    d = prepare_for_notion("# Sprint plan\n\n- [ ] a\n- [x] b\n", "file")
    assert (d.title, d.markdown, d.notes) == ("Sprint plan", "# Sprint plan\n\n- [ ] a\n- [x] b\n", [])
    assert prepare_for_notion("just text", "My file").title == "My file"


def test_frontmatter_becomes_a_visible_code_block():
    d = prepare_for_notion('---\ntitle: "Retro"\ntags: [a, b]\n---\n# Body\n\ntext', "file")
    assert d.title == "Retro" and d.markdown.startswith('```yaml\ntitle: "Retro"\ntags: [a, b]\n```\n\n# Body')
    assert not d.markdown.startswith("---") and "frontmatter" in " ".join(d.notes)
    assert prepare_for_notion("---\ntype: meeting\n---\n# Weekly\n", "f").title == "Weekly"


def test_warns_about_content_notion_shows_differently():
    d = prepare_for_notion("See [[Roadmap]] and [[Plan|the plan]].\n\n![[img.png]]\n\n> [!note] Hi\n> there", "f")
    notes = "\n".join(d.notes)
    assert "2 [[wikilink]]s" in notes and "Embedded" in notes and "Callouts" in notes and "[[Roadmap]]" in d.markdown


def test_mapping_suggestions_fit_by_name_and_type():
    fields = (Field("date", "Date", "date"), Field("duration_min", "Duration (minutes)", "number"), Field("tags", "Tags", "list"), Field("project", "Project", "text"))
    props = (Prop("1", "Name", "title"), Prop("2", "Date", "date"), Prop("3", "Duration", "number"), Prop("4", "Tags", "multi_select"),
             Prop("5", "Project", "select"), Prop("6", "Other", "rich_text"))
    assert suggest_mapping(fields, props) == [{"field": "date", "property": "Date"}, {"field": "duration_min", "property": "Duration"},
                                              {"field": "tags", "property": "Tags"}, {"field": "project", "property": "Project"}]
    assert suggest_mapping((Field("date", "Date", "date"),), (Prop("1", "Date", "number"), Prop("2", "Name", "title"))) == []
    assert fits(Field("date", "Date", "date"), Prop("1", "When", "date")) and not fits(Field("date", "Date", "date"), Prop("1", "N", "title"))
    assert "date" in sample_values("meetings") and "pace_wpm" in sample_values("practice") and "due" in sample_values("tasks")
