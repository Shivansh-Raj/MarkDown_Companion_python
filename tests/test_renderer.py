from app.markdown.renderer import render_markdown as r


def test_headings_emphasis_strike():
    h = r("# H1\n###### H6\n\n**b** *i* ~~s~~")
    assert "<h1" in h and "<h6" in h and "<strong>b</strong>" in h and "<em>i</em>" in h and "<s>s</s>" in h


def test_lists_nested_and_ordered():
    h = r("1. a\n2. b\n\n- x\n  - y\n")
    assert "<ol>" in h and h.count("<ul>") == 2


def test_table_blockquote_hr_link_image():
    h = r("| a | b |\n|---|---|\n| 1 | 2 |\n\n> q\n\n---\n\n[l](https://x.io) ![i](pic.png)")
    assert "<table>" in h and "<blockquote>" in h and "<hr" in h
    assert 'href="https://x.io"' in h and '<img src="pic.png"' in h


def test_code_highlighting():
    h = r("`inline`\n\n```python\ndef f(): pass\n```\n")
    assert "<code>inline</code>" in h and 'class="codehilite"' in h and 'class="k"' in h


def test_unknown_language_does_not_fail():
    assert "codehilite" in r("```nope-lang\nx\n```")


def test_task_list_has_line_addressed_links():
    h = r("intro\n\n- [ ] a\n- [x] b\n  - [ ] nested\n")
    assert 'href="https://mdc.invalid/toggle/2/0"' in h
    assert 'href="https://mdc.invalid/toggle/3/1"' in h
    assert 'href="https://mdc.invalid/toggle/4/0"' in h and "task-list" in h


def test_checkbox_syntax_inside_code_is_not_a_task():
    assert "https://mdc.invalid/" not in r("```\n- [ ] not a task\n```\n")


def test_raw_html_is_escaped():
    h = r("<script>alert(1)</script>")
    assert "<script>" not in h and "&lt;script&gt;" in h


def test_math_is_rendered_as_styled_span():
    assert 'class="math inline"' in r("Energy $E=mc^2$ here")


def test_malformed_input_never_raises():
    assert r("\x00 [unclosed ](( ``` \n\n|||") is not None
