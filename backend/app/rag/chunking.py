"""Split knowledge base markdown into sections and chunks.

A document is a markdown file with front matter (`title`, `last_reviewed`) and H2/H3
sections. Each section is identified by its heading path: the H2 heading, or the H2 and
H3 headings joined by `HEADING_SEPARATOR`. A section that fits in the target size is one
chunk; a longer one is packed into chunks of whole sentences or list items, each
starting with the last sentences of the previous chunk as overlap.

Pure functions: no database and no network, apart from tiktoken loading its encoding.
"""

import re
from collections.abc import Callable, Iterable
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path
from typing import Final

import tiktoken

HEADING_SEPARATOR: Final = " \N{SINGLE RIGHT-POINTING ANGLE QUOTATION MARK} "
# The tokenizer of OpenAI's text-embedding-3 models.
TOKEN_ENCODING: Final = "cl100k_base"  # noqa: S105 - an encoding name, not a secret
# Files in the knowledge base directory that are not documents.
NON_DOCUMENTS: Final = frozenset({"README.md"})
REQUIRED_FRONT_MATTER: Final = ("title", "last_reviewed")

_HEADING = re.compile(r"^(#{1,6}) (.+?)\s*$")
_SENTENCE_END = re.compile(r"(?<=[.!?])\s+(?=[A-Z0-9\"'(`*])")
_LIST_ITEM = re.compile(r"^\s*(?:[-*>]|\d+\.)\s")

TokenCounter = Callable[[str], int]


class KbFormatError(ValueError):
    """A knowledge base file does not follow the document format."""


@dataclass(frozen=True)
class Section:
    document: str
    title: str
    heading_path: str
    text: str


@dataclass(frozen=True)
class ChunkingParams:
    target_tokens: int = 500
    overlap_tokens: int = 50
    # Start the embedded text with the title and heading path, so a short section
    # still carries its topic.
    heading_prefix: bool = True


@dataclass(frozen=True)
class Chunk:
    document: str
    title: str
    heading_path: str
    # Position within the document, counting chunks of all its sections.
    chunk_index: int
    content: str
    # What is sent for embedding: the content, with the heading prefix if enabled.
    embed_text: str
    token_count: int


@lru_cache
def _encoding() -> tiktoken.Encoding:
    return tiktoken.get_encoding(TOKEN_ENCODING)


def count_tokens(text: str) -> int:
    return len(_encoding().encode(text))


# --- parsing ---


def _front_matter(document: str, lines: list[str]) -> tuple[dict[str, str], list[str]]:
    if not lines or lines[0].strip() != "---":
        raise KbFormatError(f"{document}: missing front matter")
    try:
        end = lines.index("---", 1)
    except ValueError:
        raise KbFormatError(f"{document}: front matter is not closed") from None
    fields: dict[str, str] = {}
    for line in lines[1:end]:
        key, sep, value = line.partition(":")
        if not sep:
            raise KbFormatError(f"{document}: invalid front matter line {line!r}")
        fields[key.strip()] = value.strip()
    missing = [key for key in REQUIRED_FRONT_MATTER if not fields.get(key)]
    if missing:
        raise KbFormatError(f"{document}: front matter lacks {', '.join(missing)}")
    return fields, lines[end + 1 :]


def parse_document(document: str, markdown: str) -> list[Section]:
    """Sections of one document in order. Sections with no text of their own (an H2
    that only holds H3 sections) are left out."""
    fields, body = _front_matter(document, markdown.splitlines())
    title = fields["title"]
    sections: list[tuple[str, list[str]]] = []
    h2: str | None = None
    for line in body:
        match = _HEADING.match(line)
        if match is None:
            if sections:
                sections[-1][1].append(line)
            elif line.strip():
                raise KbFormatError(f"{document}: text before the first section heading")
            continue
        level, heading = len(match.group(1)), match.group(2)
        if level == 1:
            continue
        if level == 2:
            h2 = heading
            sections.append((heading, []))
        elif level == 3:
            if h2 is None:
                raise KbFormatError(f"{document}: H3 {heading!r} before any H2")
            sections.append((f"{h2}{HEADING_SEPARATOR}{heading}", []))
        else:
            raise KbFormatError(f"{document}: headings deeper than H3 are not supported")

    paths = [path for path, _ in sections]
    duplicates = sorted({path for path in paths if paths.count(path) > 1})
    if duplicates:
        raise KbFormatError(f"{document}: duplicate heading paths {duplicates}")

    result = []
    for path, lines in sections:
        text = "\n".join(lines).strip()
        if text:
            result.append(Section(document, title, path, text))
    return result


def load_sections(kb_dir: Path) -> list[Section]:
    """Every document in the directory, in file name order."""
    if not kb_dir.is_dir():
        raise KbFormatError(f"knowledge base directory {kb_dir} does not exist")
    files = sorted(p for p in kb_dir.glob("*.md") if p.name not in NON_DOCUMENTS)
    if not files:
        raise KbFormatError(f"knowledge base directory {kb_dir} has no documents")
    sections: list[Section] = []
    for path in files:
        sections.extend(parse_document(path.stem, path.read_text(encoding="utf-8")))
    return sections


# --- chunking ---


@dataclass(frozen=True)
class _Unit:
    text: str
    tokens: int
    # Starts a new paragraph or list item, so it is joined with a line break.
    breaks_before: bool


def _units(text: str, count: TokenCounter) -> list[_Unit]:
    """Sentences of prose paragraphs and whole list items, in order."""
    units: list[_Unit] = []
    for paragraph in re.split(r"\n\s*\n", text):
        lines = [line for line in paragraph.splitlines() if line.strip()]
        if not lines:
            continue
        if any(_LIST_ITEM.match(line) for line in lines):
            pieces = lines
            separators = [True] * len(lines)
        else:
            pieces = _SENTENCE_END.split(" ".join(line.strip() for line in lines))
            separators = [True] + [False] * (len(pieces) - 1)
        units.extend(
            _Unit(piece, count(piece), sep) for piece, sep in zip(pieces, separators, strict=True)
        )
    return units


def _join(units: Iterable[_Unit]) -> str:
    parts: list[str] = []
    for i, unit in enumerate(units):
        if i:
            parts.append("\n\n" if unit.breaks_before else " ")
        parts.append(unit.text)
    return "".join(parts)


def _split_long(unit: _Unit, limit: int) -> list[_Unit]:
    """A single sentence longer than the target, cut into token windows."""
    tokens = _encoding().encode(unit.text)
    return [
        _Unit(_encoding().decode(tokens[i : i + limit]), len(tokens[i : i + limit]), i == 0)
        for i in range(0, len(tokens), limit)
    ]


def _pack(units: list[_Unit], params: ChunkingParams) -> list[list[_Unit]]:
    chunks: list[list[_Unit]] = []
    current: list[_Unit] = []
    size = 0
    fresh = 0  # units in `current` that are not overlap from the previous chunk
    for unit in units:
        if size + unit.tokens > params.target_tokens and fresh:
            chunks.append(current)
            overlap: list[_Unit] = []
            carried = 0
            for previous in reversed(current):
                if carried + previous.tokens > params.overlap_tokens:
                    break
                overlap.insert(0, previous)
                carried += previous.tokens
            # Keep room for the next unit: overlap never pushes a chunk past the target.
            while overlap and carried + unit.tokens > params.target_tokens:
                carried -= overlap.pop(0).tokens
            current, size, fresh = overlap, carried, 0
        current.append(unit)
        size += unit.tokens
        fresh += 1
    if fresh:
        chunks.append(current)
    return chunks


def chunk_sections(
    sections: Iterable[Section],
    params: ChunkingParams | None = None,
    count: TokenCounter = count_tokens,
) -> list[Chunk]:
    params = params or ChunkingParams()
    if not 0 <= params.overlap_tokens < params.target_tokens:
        raise ValueError("overlap must be at least 0 and smaller than the target size")
    chunks: list[Chunk] = []
    index: dict[str, int] = {}
    for section in sections:
        units: list[_Unit] = []
        for unit in _units(section.text, count):
            units.extend(
                _split_long(unit, params.target_tokens)
                if unit.tokens > params.target_tokens
                else [unit]
            )
        for group in _pack(units, params):
            content = _join(group)
            embed_text = (
                f"{section.title}{HEADING_SEPARATOR}{section.heading_path}\n\n{content}"
                if params.heading_prefix
                else content
            )
            position = index.get(section.document, 0)
            index[section.document] = position + 1
            chunks.append(
                Chunk(
                    document=section.document,
                    title=section.title,
                    heading_path=section.heading_path,
                    chunk_index=position,
                    content=content,
                    embed_text=embed_text,
                    token_count=count(embed_text),
                )
            )
    return chunks
