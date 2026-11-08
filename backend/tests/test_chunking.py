from pathlib import Path

import pytest

from app.rag.chunking import (
    HEADING_SEPARATOR,
    ChunkingParams,
    KbFormatError,
    Section,
    chunk_sections,
    count_tokens,
    load_sections,
    parse_document,
)

S = HEADING_SEPARATOR
KB_DIR = Path(__file__).resolve().parents[2] / "kb"


def words(text: str) -> int:
    """A token counter that counts words, so sizes in tests are easy to read."""
    return len(text.split())


def doc(body: str, title: str = "Peak hours") -> str:
    return f"---\ntitle: {title}\nlast_reviewed: 2026-11-08\n---\n\n# {title}\n\n{body}"


# --- parsing ---


def test_sections_carry_heading_paths() -> None:
    sections = parse_document(
        "peak-hours",
        doc("## Limit\n\nIntro.\n\n### Examples\n\nOne.\n\n## Why\n\nBecause."),
    )

    assert [s.heading_path for s in sections] == [
        "Limit",
        f"Limit{S}Examples",
        "Why",
    ]
    assert sections[1] == Section("peak-hours", "Peak hours", f"Limit{S}Examples", "One.")


def test_h2_without_own_text_is_left_out() -> None:
    sections = parse_document("d", doc("## Limit\n\n### Examples\n\nOne."))

    assert [s.heading_path for s in sections] == [f"Limit{S}Examples"]


@pytest.mark.parametrize(
    ("markdown", "message"),
    [
        ("# T\n\n## A\n\ntext", "missing front matter"),
        ("---\ntitle: T\n\n## A\n\ntext", "not closed"),
        ("---\ntitle: T\n---\n\n## A\n\ntext", "lacks last_reviewed"),
        (doc("Intro.\n\n## A\n\ntext"), "text before the first section heading"),
        (doc("### A\n\ntext"), "before any H2"),
        (doc("## A\n\n#### Deep\n\ntext"), "deeper than H3"),
        (doc("## A\n\none\n\n## A\n\ntwo"), "duplicate heading paths"),
        (doc("## A\n\n### B\n\none\n\n### B\n\ntwo"), "duplicate heading paths"),
    ],
)
def test_format_errors(markdown: str, message: str) -> None:
    with pytest.raises(KbFormatError, match=message):
        parse_document("d", markdown)


def test_same_h3_under_different_h2_is_allowed() -> None:
    sections = parse_document("d", doc("## A\n\n### X\n\none\n\n## B\n\n### X\n\ntwo"))

    assert [s.heading_path for s in sections] == [f"A{S}X", f"B{S}X"]


def test_missing_or_empty_directory_is_refused(tmp_path: Path) -> None:
    with pytest.raises(KbFormatError, match="does not exist"):
        load_sections(tmp_path / "missing")
    (tmp_path / "README.md").write_text("# Not a document")
    with pytest.raises(KbFormatError, match="no documents"):
        load_sections(tmp_path)


# --- chunking ---


def test_short_section_is_one_chunk_with_prefix() -> None:
    sections = [Section("peak-hours", "Peak hours", f"Limit{S}Examples", "One. Two.")]

    [chunk] = chunk_sections(sections, ChunkingParams(10, 2), count=words)

    assert chunk.content == "One. Two."
    assert chunk.embed_text == f"Peak hours{S}Limit{S}Examples\n\nOne. Two."
    assert chunk.chunk_index == 0


def test_prefix_can_be_turned_off() -> None:
    sections = [Section("d", "T", "A", "One.")]

    [chunk] = chunk_sections(sections, ChunkingParams(10, 2, heading_prefix=False), count=words)

    assert chunk.embed_text == "One."


def test_long_section_is_packed_with_sentence_overlap() -> None:
    text = "Aa bb cc. Dd ee ff. Gg hh ii. Jj kk ll."
    sections = [Section("d", "T", "A", text)]

    chunks = chunk_sections(sections, ChunkingParams(6, 3), count=words)

    assert [c.content for c in chunks] == [
        "Aa bb cc. Dd ee ff.",
        "Dd ee ff. Gg hh ii.",
        "Gg hh ii. Jj kk ll.",
    ]
    assert [c.chunk_index for c in chunks] == [0, 1, 2]


def test_no_overlap() -> None:
    sections = [Section("d", "T", "A", "Aa bb cc. Dd ee ff. Gg hh ii.")]

    chunks = chunk_sections(sections, ChunkingParams(6, 0), count=words)

    assert [c.content for c in chunks] == ["Aa bb cc. Dd ee ff.", "Gg hh ii."]


def test_list_items_and_paragraphs_stay_whole() -> None:
    text = "Intro line here.\n\n- first item\n- second item\n- third item"
    sections = [Section("d", "T", "A", text)]

    chunks = chunk_sections(sections, ChunkingParams(6, 0), count=words)

    assert [c.content for c in chunks] == [
        "Intro line here.\n\n- first item",
        "- second item\n\n- third item",
    ]


def test_sentence_longer_than_target_is_cut() -> None:
    sentence = " ".join(["word"] * 30) + "."
    sections = [Section("d", "T", "A", sentence)]

    chunks = chunk_sections(sections, ChunkingParams(10, 0, heading_prefix=False))

    assert len(chunks) > 1
    assert all(count_tokens(c.content) <= 10 for c in chunks)
    assert "".join(c.content for c in chunks) == sentence


def test_chunk_index_counts_per_document() -> None:
    sections = [
        Section("a", "A", "X", "One."),
        Section("a", "A", "Y", "Two."),
        Section("b", "B", "X", "Three."),
    ]

    chunks = chunk_sections(sections, ChunkingParams(10, 0), count=words)

    assert [(c.document, c.chunk_index) for c in chunks] == [("a", 0), ("a", 1), ("b", 0)]


def test_overlap_must_be_smaller_than_target() -> None:
    with pytest.raises(ValueError, match="overlap"):
        chunk_sections([], ChunkingParams(10, 10))


def test_token_count_uses_the_embedding_tokenizer() -> None:
    assert count_tokens("hello world") == 2


# --- the real knowledge base ---


def test_knowledge_base_parses() -> None:
    sections = load_sections(KB_DIR)

    assert len({s.document for s in sections}) >= 12
    assert all(s.title and s.text for s in sections)


def test_heading_paths_are_unique_per_document() -> None:
    seen: dict[str, set[str]] = {}
    for section in load_sections(KB_DIR):
        paths = seen.setdefault(section.document, set())
        assert section.heading_path not in paths, section
        paths.add(section.heading_path)
