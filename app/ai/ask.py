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


def build_system_prompt() -> str:
    return ("You answer questions about ONE Markdown document, using ONLY what is written in it. "
            "In the document, '- [x]' is a completed task and '- [ ]' is a task that is not done yet. "
            f"If the document does not contain the answer, reply exactly: {NOT_FOUND} Never use outside knowledge, never guess, and never invent details. "
            "Keep the answer short and direct, in the language of the question. Plain text or simple Markdown lists are fine. "
            "After the answer, add a line 'SOURCES:' followed by up to 3 lines, each '- \"<exact quote from the document>\"', copied character for character "
            "from the document. If you answered that it was not found, omit SOURCES.")


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


def conversation_markdown(turns: list[Turn], live_question: str | None = None, live_answer: str = "") -> str:
    """The whole conversation as Markdown: YOU / FILE SAYS, then the checked quotes under each answer."""
    out: list[str] = []
    for t in turns:
        out += [f"**YOU**  {t.question}", "", "**FILE SAYS**" if not t.error else "**PROBLEM**", "", t.answer, ""]
        if not t.error and not is_not_found(t.answer):
            verified = [q for q, ok in (t.sources or []) if ok]
            if verified:
                out += ["**Quotes from the file**", "", *[f"- *“{q}”*" for q in verified], ""]
            else:
                out += ["_No exact quote could be matched in the file, so double-check this answer._", ""]
    if live_question is not None:
        out += [f"**YOU**  {live_question}", "", "**FILE SAYS**", "", live_answer or "_Reading the file…_", ""]
    return "\n".join(out)
