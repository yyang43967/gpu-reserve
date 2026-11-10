"""The hand-label sheet as Markdown, and reading the verdicts back.

`hand_labels.md` has one section per answer, under its opaque answer id: the user's
messages, the retrieved sections, the reference facts, the final answer, a list of the
answer's statements with the sections that support them, and one verdict line per
criterion for the labeler to fill in. Nothing names the model that answered.

The statement list is mechanical, not a judgment: each sentence of the answer is shown
with the sections it cites by [n], or, without a citation, the retrieved section that
shares the most words with it, or "no support found". It suggests no verdict.
"""

import json
import re
from collections.abc import Sequence
from dataclasses import dataclass
from typing import Any, Final

from app.agent.evaluation.compare import Run, answer_id

_SENTENCE: Final = re.compile(r"[^.!?\n]+[.!?]?")
_CITATION: Final = re.compile(r"\[(\d{1,3})\]")
_WORD: Final = re.compile(r"[a-z0-9]+(?:[-.][a-z0-9]+)*")
_BULLET: Final = re.compile(r"^\s*(?:[-*•]|\d+[.)])\s+")
_STOP: Final = frozenset(
    (
        "the",
        "and",
        "for",
        "with",
        "that",
        "this",
        "from",
        "your",
        "you",
        "are",
        "can",
        "will",
        "not",
        "have",
        "has",
        "its",
        "into",
        "than",
        "then",
        "they",
        "them",
        "their",
        "there",
        "about",
        "which",
        "when",
        "what",
        "where",
        "also",
        "only",
        "more",
        "most",
        "such",
        "each",
        "other",
        "some",
        "any",
        "all",
        "one",
        "two",
        "may",
        "should",
        "would",
        "could",
        "must",
        "been",
        "being",
        "were",
        "was",
        "does",
        "done",
        "over",
        "under",
        "per",
        "out",
        "off",
        "our",
    )
)
# An uncited sentence counts as close to a section when it shares at least this many
# content words with it, and at least this share of its own content words.
MIN_SHARED: Final = 3
MIN_SHARE: Final = 0.4
VERDICT_LINE: Final = re.compile(r"^- verdict (\w+):\s*(\S*)\s*$")
ANSWER_HEADING: Final = re.compile(r"^## Answer ([0-9a-f]{12})\s*$")


def _content_words(text: str) -> set[str]:
    return {w for w in _WORD.findall(text.lower()) if len(w) > 2 and w not in _STOP}


def statements(answer: str) -> list[str]:
    """The answer's sentences that state something (questions are left out)."""
    found = []
    for line in answer.splitlines():
        line = _BULLET.sub("", line).strip()
        for sentence in _SENTENCE.findall(line):
            sentence = sentence.strip()
            if len(_content_words(sentence)) >= 2 and not sentence.endswith("?"):
                found.append(sentence)
    return found


def support(sentence: str, sections: Sequence[dict[str, Any]]) -> str:
    by_ref = {int(s["ref"]): s for s in sections if s.get("ref") is not None}
    cited = [int(n) for n in _CITATION.findall(sentence)]
    if cited:
        parts = [
            f"[{n}] {by_ref[n]['document']} > {by_ref[n]['heading']}"
            if n in by_ref
            else f"[{n}] (not among the retrieved sections)"
            for n in dict.fromkeys(cited)
        ]
        return "cites " + "; ".join(parts)
    words = _content_words(_CITATION.sub("", sentence))
    best: tuple[int, dict[str, Any]] | None = None
    for section in sections:
        shared = len(words & _content_words(str(section.get("content", ""))))
        if best is None or shared > best[0]:
            best = (shared, section)
    if best and words and best[0] >= MIN_SHARED and best[0] / len(words) >= MIN_SHARE:
        s = best[1]
        return (
            f"not cited; closest section [{s.get('ref')}] {s['document']} > {s['heading']} "
            f"({best[0]} of {len(words)} words shared)"
        )
    return "not cited; no support found"


@dataclass
class Answer:
    answer_id: str
    case_id: str
    question: str
    answer: str
    reference: list[str]
    retrieved: list[dict[str, Any]]
    criteria: list[str]
    verdicts: dict[str, str | None]


def collect(rows: Sequence[dict[str, Any]], runs: Sequence[Run]) -> list[Answer]:
    """The label rows grouped by answer, joined to the runs for the retrieved text."""
    by_id: dict[str, dict[str, Any]] = {}
    for run in runs:
        for row in run.rows:
            by_id[answer_id(run.config["run_id"], row["id"])] = row
    answers: dict[str, Answer] = {}
    for label in rows:
        aid = label["answer_id"]
        if aid not in by_id:
            raise ValueError(f"answer {aid} is in no run given")
        entry = answers.setdefault(
            aid,
            Answer(
                aid,
                label["case_id"],
                label["question"],
                label["answer"],
                list(label["reference"]),
                list(by_id[aid].get("retrieved", [])),
                [],
                {},
            ),
        )
        entry.criteria.append(label["criterion"])
        entry.verdicts[label["criterion"]] = label.get("verdict")
    return sorted(answers.values(), key=lambda a: a.answer_id)


def _quote(text: str) -> list[str]:
    return [f"> {line}" if line.strip() else ">" for line in text.strip().splitlines()]


def render(answers: Sequence[Answer]) -> str:
    total = sum(len(a.criteria) for a in answers)
    lines = [
        "# Hand labels",
        "",
        f"{len(answers)} answers, {total} verdicts. For each verdict line, write `pass` or "
        "`fail` after the colon, using the criteria in `rubric.md`. Then run "
        "`python -m app.agent.evaluation label-read`.",
        "",
        "The statement list under each answer is mechanical: sentences with [n] show the "
        "sections they cite; other sentences show the retrieved section sharing the most "
        "words, or 'no support found'. It is a reading aid, not a verdict, and it does not "
        "know which statements are about rules, policies, or hardware.",
        "",
    ]
    for a in answers:
        lines += [f"## Answer {a.answer_id}", "", f"Case: `{a.case_id}`", "", "**User**", ""]
        lines += _quote(a.question)
        lines += ["", "**Retrieved sections**", ""]
        if not a.retrieved:
            lines.append("(none)")
        for s in a.retrieved:
            lines += [f"[{s.get('ref')}] {s.get('document')} > {s.get('heading')}", ""]
            lines += _quote(str(s.get("content", "")))
            lines.append("")
        lines += ["", "**Reference facts**", ""]
        lines += [f"- {fact}" for fact in a.reference] or ["(none)"]
        lines += ["", "**Final answer**", ""]
        lines += _quote(a.answer) or ["(empty)"]
        lines += ["", "**Statements and support**", ""]
        lines += [
            f"- “{sentence}” — {support(sentence, a.retrieved)}"
            for sentence in statements(a.answer)
        ] or ["(no statements)"]
        lines += ["", "**Your verdicts**", ""]
        lines += [f"- verdict {c}: {a.verdicts.get(c) or ''}".rstrip() for c in a.criteria]
        lines.append("")
    return "\n".join(lines) + "\n"


def read_verdicts(markdown: str) -> dict[tuple[str, str], str | None]:
    """(answer id, criterion) -> "pass", "fail", or None when left empty. Anything else
    after the colon is an error."""
    verdicts: dict[tuple[str, str], str | None] = {}
    current: str | None = None
    for number, line in enumerate(markdown.splitlines(), start=1):
        heading = ANSWER_HEADING.match(line)
        if heading:
            current = heading.group(1)
            continue
        match = VERDICT_LINE.match(line)
        if match and current is not None:
            criterion, value = match.group(1), match.group(2).lower()
            if value not in ("", "pass", "fail"):
                raise ValueError(f"line {number}: {value!r} is not pass or fail")
            verdicts[(current, criterion)] = value or None
    return verdicts


def apply_verdicts(
    rows: Sequence[dict[str, Any]], verdicts: dict[tuple[str, str], str | None]
) -> tuple[list[dict[str, Any]], list[str]]:
    """The label rows with the verdicts filled in, and every row still without one."""
    updated, missing = [], []
    for row in rows:
        key = (row["answer_id"], row["criterion"])
        verdict = verdicts.get(key, row.get("verdict"))
        if verdict is None:
            missing.append(f"{row['answer_id']} {row['criterion']} ({row['case_id']})")
        updated.append({**row, "verdict": verdict})
    return updated, missing


def dumps_rows(rows: Sequence[dict[str, Any]]) -> str:
    return "".join(json.dumps(row, ensure_ascii=False) + "\n" for row in rows)
