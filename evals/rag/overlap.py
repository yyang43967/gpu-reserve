"""Lexical overlap between labeled questions and their gold sections.

A question that repeats the words of its gold heading measures keyword matching, not
retrieval. This report flags those questions so they can be rewritten before the set is
frozen.

With --body, it also reports the share of each question's words found anywhere in its
gold section (heading and text). That number is informational: a section is supposed to
answer the question, so some shared vocabulary is expected.

Usage, from the repository root:
    python3 evals/rag/overlap.py evals/rag/acceptance.jsonl [--body]
"""

import argparse
import json
import re
import sys
from collections import Counter
from pathlib import Path

KB_DIR = Path(__file__).resolve().parents[2] / "kb"
HEADING_SEPARATOR = " › "
# A question is flagged when it contains at least this share of its gold heading's words.
FLAG_SHARE = 0.5
STEM_LENGTH = 5
SUFFIXES = ("ations", "ation", "ings", "ing", "ed", "es", "s", "ly")
STOPWORDS = frozenset(
    """a an and are as at be before by can do does for from has have how i if in is it
    its my of on or our so that the their them then there this to up was what when where
    which who why will with you your more than about after into out over per""".split()
)
HEADING = re.compile(r"^(#{2,3}) (.+?)\s*$")


def stem(word: str) -> str:
    for suffix in SUFFIXES:
        if word.endswith(suffix) and len(word) - len(suffix) >= 3:
            word = word[: -len(suffix)]
            break
    return word[:STEM_LENGTH]


def content_words(text: str) -> set[str]:
    return {stem(w) for w in re.findall(r"[a-z0-9]+", text.lower()) if w not in STOPWORDS}


def section_texts(path: Path) -> dict[str, str]:
    """Heading path -> body text of that section (without its subsections)."""
    sections: dict[str, list[str]] = {}
    h2 = None
    current = None
    for line in path.read_text(encoding="utf-8").splitlines():
        match = HEADING.match(line)
        if match:
            level, text = match.groups()
            if level == "##":
                h2 = current = text
            elif h2 is not None:
                current = f"{h2}{HEADING_SEPARATOR}{text}"
            sections[current] = []
        elif current is not None:
            sections[current].append(line)
    return {heading: "\n".join(lines) for heading, lines in sections.items()}


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("dataset", type=Path)
    parser.add_argument("--body", action="store_true", help="also report body overlap")
    args = parser.parse_args()

    sections = {
        p.stem: section_texts(p) for p in sorted(KB_DIR.glob("*.md")) if p.name != "README.md"
    }
    body_shares: list[float] = []
    rows = [json.loads(line) for line in args.dataset.read_text().splitlines() if line.strip()]

    missing: list[str] = []
    flagged: list[str] = []
    shares: list[float] = []
    for row in rows:
        words = content_words(row["question"])
        for gold in row["gold"]:
            doc, heading = gold["document"], gold["heading_path"]
            if heading not in sections.get(doc, {}):
                missing.append(f"{row['id']}: {doc} / {heading}")
                continue
            heading_words = content_words(heading)
            shared = words & heading_words
            share = len(shared) / len(heading_words) if heading_words else 0.0
            shares.append(share)
            mark = "FLAG" if share >= FLAG_SHARE else "    "
            if share >= FLAG_SHARE:
                flagged.append(row["id"])
            body = ""
            if args.body and words:
                body_words = content_words(f"{heading} {sections[doc][heading]}")
                body_share = len(words & body_words) / len(words)
                body_shares.append(body_share)
                body = f" body {body_share:4.2f}"
            print(f"{mark} {row['id']} {share:4.2f}{body} {sorted(shared)!s:30} {doc} / {heading}")

    shares.sort()
    median = shares[len(shares) // 2] if shares else 0.0
    print()
    print(f"questions: {len(rows)}  labels: {len(shares)}  median heading share: {median:.2f}")
    if body_shares:
        body_shares.sort()
        print(f"median body share: {body_shares[len(body_shares) // 2]:.2f}  "
              f"max: {body_shares[-1]:.2f}")
    print("categories:", dict(Counter(r["category"] for r in rows)))
    print("authors:", dict(Counter(r["author"] for r in rows)))
    if missing:
        print("labels without a matching section:", *missing, sep="\n  ")
    if flagged:
        print("flagged:", ", ".join(sorted(set(flagged))))
    return 1 if missing or flagged else 0


if __name__ == "__main__":
    sys.exit(main())
