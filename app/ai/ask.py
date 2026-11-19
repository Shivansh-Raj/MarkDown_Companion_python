"""Ask a question about one Markdown file; the answer must come from the file itself (port of V1's aiAsk.ts)."""
from __future__ import annotations

import re
from dataclasses import dataclass

from ..voice.coach import in_transcript, squash

MAX_ASK_CHARS = 80_000
NOT_FOUND = "I couldn't find that in this file."


@dataclass
class Turn:
    question: str
    answer: str
    sources: list[tuple[str, bool]] | None = None      # (quote, verified against the file)
    error: bool = False
    headings: dict[str, str] | None = None             # verified quote -> the section it is in


def build_system_prompt() -> str:
    return ("You answer questions about ONE Markdown document, using ONLY what is written in it. "
            "In the document, '- [x]' is a completed task and '- [ ]' is a task that is not done yet. "
            f"If the document does not contain the answer, reply exactly: {NOT_FOUND} Never use outside knowledge, never guess, and never invent details. "
            "Keep the answer short and direct, in the language of the question. Plain text or simple Markdown lists are fine. "
            "After the answer, add a line 'SOURCES:' followed by up to 3 lines, each '- \"<exact quote from the document>\"', copied character for character "
            "from the document. If you answered that it was not found, omit SOURCES.")


# Groq's free tier allows about 8,000 tokens a minute for the default model, answer included. About 14,000 characters of
# document plus the prompt stays safely under that.
ASK_BUDGET_CHARS = 14_000
_CHUNK_MAX = 1_500
_GAP = "\n\n[…]\n\n"
_FILLER = set(("the and what this that file with for from about summarise summarize summary tell give list bullet bullets three two four five "
               "main points who how why when where which does did are was were has have had can could would should into than then them they "
               "their there here please").split())


@dataclass
class Excerpt:
    text: str
    partial: bool
    sent: int = 1
    total: int = 1


def _chunk_doc(content: str) -> list[tuple[int, int, str]]:
    """(index, section, text) pieces: split at headings, and long sections at blank lines."""
    out: list[tuple[int, int, str]] = []
    section, buf = -1, []

    def push(t: str) -> None:
        out.append((len(out), section, t))

    def flush() -> None:
        nonlocal buf
        text = "\n".join(buf).strip()
        buf = []
        if not text:
            return
        if len(text) <= _CHUNK_MAX:
            push(text)
            return
        cur = ""
        for para in re.split(r"\n\s*\n", text):
            if len(para) > _CHUNK_MAX:
                rest = para
                if cur:                                  # whatever came before it (a heading) stays with the first piece
                    room = max(0, _CHUNK_MAX - len(cur) - 2)
                    push(f"{cur}\n\n{rest[:room]}")
                    rest, cur = rest[room:], ""
                for i in range(0, len(rest), _CHUNK_MAX):
                    push(rest[i:i + _CHUNK_MAX])
                continue
            if cur and len(cur) + len(para) + 2 > _CHUNK_MAX:
                push(cur)
                cur = ""
            cur = f"{cur}\n\n{para}" if cur else para
        if cur:
            push(cur)
    for line in content.split("\n"):
        if re.match(r"^#{1,6}\s", line):
            flush()
            section += 1
        buf.append(line)
    flush()
    return out


def excerpt_for_ask(content: str, question: str, budget: int = ASK_BUDGET_CHARS) -> Excerpt:
    """A file that fits the budget is sent whole. A longer one is cut to the parts most likely to answer the question (by shared
    words); for a general question ("summarise this") the start of every section is taken instead. Pieces stay in file order."""
    if len(content) <= budget:
        return Excerpt(content, False)
    chunks = _chunk_doc(content)
    words = sorted({w for w in re.findall(r"[\w]{3,}", question.lower()) if w not in _FILLER})
    scored = [(c, sum(1 for w in words if w in c[2].lower())) for c in chunks]
    picked: list[tuple[int, int, str]] = []
    if words and any(s > 0 for _, s in scored):
        used = 0
        for c, s in sorted((x for x in scored if x[1] > 0), key=lambda x: (-x[1], x[0][0])):
            cost = len(c[2]) + len(_GAP)
            if used + cost <= budget:
                picked.append(c)
                used += cost
    else:
        firsts, seen = [], set()
        for c in chunks:
            if c[1] not in seen:
                seen.add(c[1])
                firsts.append(c)
        keep = max(1, min(len(firsts), (budget - 100) // (250 + len(_GAP))))
        chosen = [firsts[int(i * len(firsts) / keep)] for i in range(keep)]
        per = (budget - 100) // len(chosen) - len(_GAP)
        for idx, sec, text in chosen:
            cut = re.sub(r"\s+\S*$", "", text[:per]) if len(text) > per else text
            picked.append((idx, sec, cut or text[:per]))
    picked.sort(key=lambda c: c[0])
    text = ""
    for i, c in enumerate(picked):
        text += (("\n\n" if c[0] == picked[i - 1][0] + 1 else _GAP) if i else "") + c[2]
    if picked and picked[0][0] > 0:
        text = "[…]\n\n" + text
    return Excerpt(text[:budget], True, len(picked), len(chunks))


def limit_doc(content: str) -> tuple[str, bool]:
    if len(content) <= MAX_ASK_CHARS:
        return content, False
    cut = content[:MAX_ASK_CHARS]
    nl = cut.rfind("\n")
    return (cut[:nl] if nl > 0 else cut), True


def build_user_prompt(question: str, history: list[Turn], doc: str) -> str:
    """Earlier turns are included (briefly) so follow-ups like "and who owns it?" make sense."""
    prior = "\n\n".join(f"Q: {t.question}\nA: {t.answer[:600]}" for t in history[-4:])
    return f'{"Earlier in this conversation:" + chr(10) + prior + chr(10) + chr(10) if prior else ""}Document:\n"""\n{doc}\n"""\n\nQuestion: {question}'


# A "sources" label at the start of a line, in any of the shapes models produce: SOURCES:, **Sources:**, ### Sources, Source: "..."
_LABEL = re.compile(r"^[ \t#>*_-]*(?:sources?|quotes?|references?|citations?)[ \t*_]*(?::[ \t*_]*|$)", re.I | re.M)


def answer_part(raw: str) -> str:
    """The reply without its sources block (used while the answer is still streaming)."""
    m = _LABEL.search(raw)
    return (raw[:m.start()] if m else raw).strip()


def parse_answer(raw: str, doc: str) -> tuple[str, list[tuple[str, bool]]]:
    """Split the reply into the answer and its sources, marking which quotes really appear in the file."""
    m = _LABEL.search(raw)
    answer = (raw[:m.start()] if m else raw).strip()
    tail = raw[m.end():] if m else ""
    squashed = squash(doc)
    sources: list[tuple[str, bool]] = []
    for line in tail.split("\n"):
        # strip list markers, numbering, blockquote marks, emphasis and the quotation marks around the quote
        mm = re.match(r"^[ \t>*_-]*(?:\d+[.)][ \t]+)?[ \t*_]*[\"“”']?(.+?)[\"“”']?[ \t*_]*$", line)
        quote = mm.group(1).strip() if mm else ""
        if len(quote) < 3 or any(q == quote for q, _ in sources):
            continue
        sources.append((quote, in_transcript(quote, squashed)))
        if len(sources) == 3:
            break
    return answer, sources


def is_not_found(answer: str) -> bool:
    return answer.strip().lower().startswith("i couldn't find that in this file")


def visible_quotes(turn: Turn) -> list[str]:
    """The quotes worth showing under an answer: only those found in the file (never invented ones)."""
    if turn.error or is_not_found(turn.answer):
        return []
    return [q for q, ok in (turn.sources or []) if ok]


def needs_quote_warning(turn: Turn) -> bool:
    """A real answer none of whose quotes could be matched in the file: the user should double-check it."""
    return not turn.error and not is_not_found(turn.answer) and not visible_quotes(turn)


def heading_for_quote(doc: str, quote: str) -> str | None:
    """The heading of the section a quote is in (the nearest '#' heading above it), or None when it is before any heading or
    spans two sections. Matching ignores case and punctuation, like the quote check itself."""
    sections: list[tuple[str | None, list[str]]] = [(None, [])]
    for line in doc.split("\n"):
        m = re.match(r"^#{1,6}\s+(.*?)\s*#*\s*$", line)
        if m:
            sections.append((m.group(1), []))
        else:
            sections[-1][1].append(line)
    for heading, lines in sections:
        if in_transcript(quote, squash(" ".join(lines))):
            return heading
    return None


def with_headings(turn: Turn, doc: str) -> Turn:
    turn.headings = {q: h for q, ok in (turn.sources or []) if ok and (h := heading_for_quote(doc, q))}
    return turn


def archive_markdown(turn: Turn, source_name: str, source_url: str | None = None) -> str:
    """An approved answer as a Notion page body: question, answer, and only the verified quotes, each naming its section."""
    where = f"[{source_name}]({source_url})" if source_url else source_name
    quotes = [f"> {' '.join(q.split())}\n\n— {(turn.headings or {}).get(q) + ', ' if (turn.headings or {}).get(q) else ''}{where}"
              for q in visible_quotes(turn)]
    parts = [f"**Question:** {turn.question.strip()}", f"**Source:** {where}", "## Answer", turn.answer.strip()]
    if quotes:
        parts += ["## Verified quotes", *quotes]
    return "\n\n".join(parts) + "\n"


def database_document(pages: list[dict], question: str, budget: int = 10_000) -> str:
    """Several Notion pages as one document for the single-document Ask: each page's best sections under '## <page> > <section>'
    headings, so a verified quote names its page and section. At most 3 sections per page and `budget` characters in all."""
    words = {w for w in re.findall(r"[\w]{3,}", question.lower()) if w not in _FILLER}
    cands = []
    for pi, pg in enumerate(pages):
        heading, buf = "", []

        def push():
            text = "\n".join(buf).strip()[:1200]
            if text:
                cands.append((sum(1 for w in words if w in text.lower()), pi, len(cands), pg["title"], heading, text))
        for line in pg["markdown"].split("\n"):
            m = re.match(r"^#{1,6}\s+(.*)$", line)
            if m:
                push()
                buf, heading = [], m.group(1).strip()
                continue
            buf.append(line)
        push()
    hit = any(c[0] > 0 for c in cands)
    ranked = sorted((c for c in cands if c[0] > 0), key=lambda c: (-c[0], c[1], c[2])) if hit else sorted(cands, key=lambda c: (c[1], c[2]))
    out, per, total = [], {}, 0
    for score, pi, _, title, heading, text in ranked:
        if per.get(pi, 0) >= (3 if hit else 1) or total + len(text) > budget:
            continue
        per[pi] = per.get(pi, 0) + 1
        total += len(text)
        same = heading.strip().lower() == title.strip().lower()          # "# Sprint" inside the page "Sprint" adds nothing
        out.append(f"## {title}{' > ' + heading if heading and not same else ''}\n\n{text}")
    return "\n\n".join(out)
