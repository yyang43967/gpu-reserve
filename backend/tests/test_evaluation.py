from pathlib import Path

import pytest

from app.rag import evaluation
from app.rag.chunking import ChunkingParams, chunk_sections, load_sections
from app.rag.embeddings import CachedEmbedder
from app.rag.evaluation import (
    ACCEPTANCE_PATH,
    DEV_PATH,
    NEGATIVES_PATH,
    Outcome,
    Question,
    check_labels,
    choose_threshold,
    load_questions,
    memory_nearest,
    pick_setting,
    summarize,
)
from app.rag.retrieval import PolicyHit
from tests.fakes import FakeEmbedder

KB_DIR = Path(__file__).resolve().parents[2] / "kb"


def policy_hit(document: str, heading: str, score: float) -> PolicyHit:
    return PolicyHit(document, document, heading, "word " * 10, score)


def question(qid: str, *gold: tuple[str, str]) -> Question:
    return Question(qid, f"question {qid}", frozenset(gold))


def test_rank_is_the_first_gold_position() -> None:
    q = question("a", ("d", "X"), ("d", "Y"))
    outcome = Outcome(q, [policy_hit("d", "Z", 0.9), policy_hit("d", "Y", 0.8)])

    assert outcome.rank == 2
    assert not outcome.found_all
    assert Outcome(q, [policy_hit("d", "Z", 0.9)]).rank is None
    assert Outcome(q, []).top_score == -1.0


def test_summary_counts_hits_ranks_and_mrr() -> None:
    q1, q2, q3 = question("1", ("d", "A")), question("2", ("d", "B")), question("3", ("d", "C"))
    outcomes = [
        Outcome(q1, [policy_hit("d", "A", 0.9)]),
        Outcome(
            q2, [policy_hit("d", "A", 0.9), policy_hit("d", "X", 0.8), policy_hit("d", "B", 0.7)]
        ),
        Outcome(q3, [policy_hit("d", "A", 0.9)]),
    ]

    s = summarize(outcomes)

    assert (s.total, s.hits, s.hit_at_1, s.hit_at_3) == (3, 2, 1, 2)
    assert s.mrr == pytest.approx((1 + 1 / 3) / 3)
    assert s.hit_rate == pytest.approx(2 / 3)


def test_threshold_keeps_every_dev_hit() -> None:
    outcomes = [
        Outcome(question("1", ("d", "A")), [policy_hit("d", "A", 0.612)]),
        Outcome(
            question("2", ("d", "B")), [policy_hit("d", "X", 0.7), policy_hit("d", "B", 0.437)]
        ),
        Outcome(question("3", ("d", "C")), [policy_hit("d", "X", 0.2)]),
    ]

    assert choose_threshold(outcomes) == pytest.approx(0.43)


def test_defaults_win_unless_beaten_by_more_than_the_margin() -> None:
    assert pick_setting({"default": 15, "other": 17}, "default") == "default"
    assert pick_setting({"default": 15, "other": 18}, "default") == "other"
    assert pick_setting({"default": 15, "other": 10}, "default") == "default"


async def test_memory_nearest_orders_by_cosine() -> None:
    chunks = chunk_sections(load_sections(KB_DIR))[:3]
    vectors = [[1.0, 0.0], [0.0, 1.0], [0.7, 0.7]]

    hits = await memory_nearest(chunks, vectors)([2.0, 0.1], 2)

    assert [h.heading_path for h in hits] == [chunks[0].heading_path, chunks[2].heading_path]
    assert hits[0].score == pytest.approx(0.99875, abs=1e-4)


def test_every_gold_label_names_a_section() -> None:
    chunks = chunk_sections(load_sections(KB_DIR))
    for path in (ACCEPTANCE_PATH, DEV_PATH):
        assert check_labels(load_questions(path), chunks) == [], path


def test_question_sets_have_the_planned_sizes() -> None:
    acceptance, dev, negatives = (
        load_questions(p) for p in (ACCEPTANCE_PATH, DEV_PATH, NEGATIVES_PATH)
    )

    assert (len(acceptance), len(dev), len(negatives)) == (30, 20, 10)
    assert all(q.gold for q in acceptance + dev)
    assert not any(q.gold for q in negatives)
    ids = [q.id for q in acceptance + dev + negatives]
    assert len(ids) == len(set(ids))


async def test_sweep_runs_on_replayed_embeddings(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """The whole sweep with fake vectors, to exercise the plumbing without the API."""
    fake = FakeEmbedder(dimensions=64)
    cache = CachedEmbedder(fake.model, fake.dimensions, fake)
    await cache.embed(evaluation._all_texts(KB_DIR))
    path = tmp_path / "embeddings.json.gz"
    cache.save(path)
    monkeypatch.setattr(evaluation, "EMBEDDINGS_PATH", path)
    monkeypatch.setattr(evaluation, "SWEEP_TARGETS", (200, 500))

    assert await evaluation.sweep(KB_DIR) == 0

    report = capsys.readouterr().out
    assert "### Section lengths" in report
    assert "500 / 50 / on" in report
    assert "RAG_MIN_SCORE = " in report


def test_chunking_defaults_match_the_evaluation_defaults() -> None:
    assert ChunkingParams() == evaluation.DEFAULT_PARAMS
