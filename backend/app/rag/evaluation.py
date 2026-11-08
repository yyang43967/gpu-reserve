"""Retrieval evaluation: labeled questions, hit rate, the parameter sweep, recording.

Commands, from backend/:

    uv run python -m app.rag.evaluation record      # embed every text the evaluation needs
    uv run python -m app.rag.evaluation sweep       # parameter sweep on the dev set
    uv run python -m app.rag.evaluation acceptance  # the acceptance run, through pgvector

`record` is the only command that calls the embeddings API (it needs OPENAI_API_KEY).
It writes evals/rag/embeddings.json.gz; the other commands and the hit-rate test replay
from that file and fail on any text it does not contain.

Every path runs the production pipeline (`retrieval.retrieve`: nearest chunks, merge by
section, minimum score, cut to k). The sweep swaps only the nearest-neighbour step for
an exact in-memory scan, so it never writes to the database.
"""

import argparse
import asyncio
import itertools
import json
import math
import statistics
import sys
from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Final

from app.rag.chunking import Chunk, ChunkingParams, chunk_sections, count_tokens, load_sections
from app.rag.embeddings import CachedEmbedder, Embedder, cache_key
from app.rag.retrieval import ChunkHit, NearestChunks, PolicyHit, retrieve

REPO_ROOT: Final = Path(__file__).resolve().parents[3]
EVALS_DIR: Final = REPO_ROOT / "evals" / "rag"
ACCEPTANCE_PATH: Final = EVALS_DIR / "acceptance.jsonl"
DEV_PATH: Final = EVALS_DIR / "dev.jsonl"
NEGATIVES_PATH: Final = EVALS_DIR / "negatives.jsonl"
EMBEDDINGS_PATH: Final = EVALS_DIR / "embeddings.json.gz"

DEFAULT_PARAMS: Final = ChunkingParams()
DEFAULT_K: Final = 5
# Acceptance: at least this share of the questions must find a gold section.
REQUIRED_HIT_RATE: Final = 0.9
# The sweep keeps the defaults unless another setting finds more than this many extra
# dev questions; smaller differences are noise with 20 questions.
SWEEP_MARGIN: Final = 2
SWEEP_TARGETS: Final = (200, 350, 500, 800)
SWEEP_OVERLAPS: Final = (0, 50)
SWEEP_PREFIXES: Final = (True, False)
K_CURVE: Final = tuple(range(1, 11))
# Thresholds are reported and chosen at this resolution, rounded down.
SCORE_STEP: Final = 0.01


# --- datasets ---


@dataclass(frozen=True)
class Question:
    id: str
    text: str
    # (document, heading path) pairs; finding any one of them is a hit.
    gold: frozenset[tuple[str, str]]
    category: str = ""


def load_questions(path: Path) -> list[Question]:
    questions = []
    for line in path.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        row = json.loads(line)
        gold = frozenset((g["document"], g["heading_path"]) for g in row.get("gold", []))
        questions.append(Question(row["id"], row["question"], gold, row.get("category", "")))
    return questions


def check_labels(questions: Iterable[Question], chunks: Iterable[Chunk]) -> list[str]:
    """Gold sections that no chunk belongs to."""
    sections = {(c.document, c.heading_path) for c in chunks}
    return [
        f"{q.id}: {doc} / {heading}"
        for q in questions
        for doc, heading in sorted(q.gold)
        if (doc, heading) not in sections
    ]


# --- scoring ---


@dataclass(frozen=True)
class Outcome:
    question: Question
    hits: list[PolicyHit]

    @property
    def rank(self) -> int | None:
        """1-based position of the first gold section among the results."""
        for position, hit in enumerate(self.hits, start=1):
            if (hit.document, hit.heading_path) in self.question.gold:
                return position
        return None

    @property
    def found_all(self) -> bool:
        returned = {(h.document, h.heading_path) for h in self.hits}
        return self.question.gold <= returned

    @property
    def top_score(self) -> float:
        return self.hits[0].score if self.hits else -1.0


@dataclass(frozen=True)
class Summary:
    total: int
    hits: int
    hit_at_1: int
    hit_at_3: int
    mrr: float
    tokens_per_query: float

    @property
    def hit_rate(self) -> float:
        return self.hits / self.total if self.total else 0.0


def summarize(outcomes: Sequence[Outcome]) -> Summary:
    ranks = [o.rank for o in outcomes]
    tokens = [sum(count_tokens(h.content) for h in o.hits) for o in outcomes]
    return Summary(
        total=len(outcomes),
        hits=sum(r is not None for r in ranks),
        hit_at_1=sum(r == 1 for r in ranks),
        hit_at_3=sum(r is not None and r <= 3 for r in ranks),
        mrr=sum(1 / r for r in ranks if r is not None) / len(outcomes) if outcomes else 0.0,
        tokens_per_query=statistics.fmean(tokens) if tokens else 0.0,
    )


async def run_questions(
    questions: Sequence[Question],
    embedder: Embedder,
    nearest: NearestChunks,
    *,
    k: int,
    min_score: float,
) -> list[Outcome]:
    vectors = await embedder.embed([q.text for q in questions])
    return [
        Outcome(q, await retrieve(v, nearest, k=k, min_score=min_score))
        for q, v in zip(questions, vectors, strict=True)
    ]


# --- the in-memory nearest-neighbour step used by the sweep ---


def _norm(vector: Sequence[float]) -> float:
    return math.sqrt(sum(x * x for x in vector))


def memory_nearest(chunks: Sequence[Chunk], vectors: Sequence[Sequence[float]]) -> NearestChunks:
    """Exact cosine top-n over chunks held in memory."""
    norms = [_norm(v) for v in vectors]

    async def nearest(query: Sequence[float], n: int) -> list[ChunkHit]:
        q_norm = _norm(query)
        scored = [
            (sum(a * b for a, b in zip(query, vector, strict=True)) / (q_norm * norm), i)
            for i, (vector, norm) in enumerate(zip(vectors, norms, strict=True))
        ]
        scored.sort(key=lambda item: (-item[0], item[1]))
        return [
            ChunkHit(
                chunks[i].document,
                chunks[i].title,
                chunks[i].heading_path,
                chunks[i].chunk_index,
                chunks[i].content,
                score,
            )
            for score, i in scored[:n]
        ]

    return nearest


async def memory_index(
    kb_dir: Path, embedder: Embedder, params: ChunkingParams
) -> tuple[list[Chunk], NearestChunks]:
    chunks = chunk_sections(load_sections(kb_dir), params)
    vectors = await embedder.embed([c.embed_text for c in chunks])
    return chunks, memory_nearest(chunks, vectors)


# --- choosing parameters ---


def floor_score(value: float) -> float:
    return math.floor(value / SCORE_STEP) * SCORE_STEP


def choose_threshold(dev: Sequence[Outcome]) -> float:
    """The highest threshold that keeps every dev hit: the lowest score at which a dev
    question's first gold section was found, rounded down."""
    gold_scores = [o.hits[o.rank - 1].score for o in dev if o.rank is not None]
    return floor_score(min(gold_scores)) if gold_scores else 0.0


def pick_setting[T](results: dict[T, int], default: T, margin: int = SWEEP_MARGIN) -> T:
    """The default, unless another setting beats it by more than `margin`."""
    best = max(results, key=lambda key: results[key])
    return best if results[best] - results[default] > margin else default


# --- commands ---


def _all_texts(kb_dir: Path) -> list[str]:
    texts: list[str] = []
    sections = load_sections(kb_dir)
    for target, overlap, prefix in itertools.product(SWEEP_TARGETS, SWEEP_OVERLAPS, SWEEP_PREFIXES):
        texts += [
            c.embed_text for c in chunk_sections(sections, ChunkingParams(target, overlap, prefix))
        ]
    for path in (ACCEPTANCE_PATH, DEV_PATH, NEGATIVES_PATH):
        texts += [q.text for q in load_questions(path)]
    return list(dict.fromkeys(texts))


async def record(kb_dir: Path) -> int:
    from app.core.config import get_settings
    from app.rag.embeddings import build_embedder

    settings = get_settings()
    client = build_embedder(settings)
    if client is None:
        print("OPENAI_API_KEY is not set.", file=sys.stderr)
        return 2
    cache = (
        CachedEmbedder.load(EMBEDDINGS_PATH, client)
        if EMBEDDINGS_PATH.exists()
        else CachedEmbedder(client.model, client.dimensions, client)
    )
    if (cache.model, cache.dimensions) != (client.model, client.dimensions):
        cache = CachedEmbedder(client.model, client.dimensions, client)
    texts = _all_texts(kb_dir)
    await cache.embed(texts)
    keep = {cache_key(cache.model, cache.dimensions, t) for t in texts}
    cache.save(EMBEDDINGS_PATH, keep=keep)
    print(
        f"{len(keep)} texts recorded, {cache.misses} newly embedded, "
        f"{client.tokens_used} tokens billed ({cache.model}, {cache.dimensions} dimensions)"
    )
    return 0


def _row(name: str, s: Summary, total: int) -> str:
    return (
        f"| {name} | {s.hits}/{total} | {s.hit_at_1} | {s.hit_at_3} | {s.mrr:.3f} "
        f"| {s.tokens_per_query:.0f} |"
    )


async def sweep(kb_dir: Path) -> int:
    replay = CachedEmbedder.load(EMBEDDINGS_PATH)
    dev = load_questions(DEV_PATH)
    negatives = load_questions(NEGATIVES_PATH)
    sections = load_sections(kb_dir)
    out: list[str] = []

    lengths = sorted(count_tokens(s.text) for s in sections)
    out += [
        "### Section lengths",
        f"{len(lengths)} sections; tokens min {lengths[0]}, median "
        f"{statistics.median(lengths):.0f}, 90th percentile "
        f"{lengths[int(len(lengths) * 0.9)]}, max {lengths[-1]}, total {sum(lengths)}.",
        "",
        "| target | overlap | chunks |",
        "|---|---|---|",
    ]
    for target, overlap in itertools.product(SWEEP_TARGETS, SWEEP_OVERLAPS):
        count = len(chunk_sections(sections, ChunkingParams(target, overlap)))
        out.append(f"| {target} | {overlap} | {count} |")

    missing = check_labels(dev, chunk_sections(sections, DEFAULT_PARAMS))
    if missing:
        print("dev labels without a section:", *missing, sep="\n  ", file=sys.stderr)
        return 1

    out += [
        "",
        f"### Chunking grid (dev, k = {DEFAULT_K}, no minimum score)",
        "| target / overlap / prefix | hits | hit@1 | hit@3 | MRR | tokens per query |",
        "|---|---|---|---|---|---|",
    ]
    grid: dict[ChunkingParams, int] = {}
    for target, overlap, prefix in itertools.product(SWEEP_TARGETS, SWEEP_OVERLAPS, SWEEP_PREFIXES):
        params = ChunkingParams(target, overlap, prefix)
        _, nearest = await memory_index(kb_dir, replay, params)
        summary = summarize(await run_questions(dev, replay, nearest, k=DEFAULT_K, min_score=-1.0))
        grid[params] = summary.hits
        name = f"{target} / {overlap} / {'on' if prefix else 'off'}"
        out.append(_row(name, summary, len(dev)))
    chosen = pick_setting(grid, DEFAULT_PARAMS)

    _, nearest = await memory_index(kb_dir, replay, chosen)
    out += [
        "",
        f"### k (dev, {chosen.target_tokens} / {chosen.overlap_tokens} / "
        f"{'on' if chosen.heading_prefix else 'off'}, no minimum score)",
        "| k | hits | hit@1 | hit@3 | MRR | tokens per query |",
        "|---|---|---|---|---|---|",
    ]
    by_k: dict[int, int] = {}
    for k in K_CURVE:
        summary = summarize(await run_questions(dev, replay, nearest, k=k, min_score=-1.0))
        by_k[k] = summary.hits
        out.append(_row(str(k), summary, len(dev)))
    k_chosen = pick_setting(by_k, DEFAULT_K)

    dev_outcomes = await run_questions(dev, replay, nearest, k=k_chosen, min_score=-1.0)
    neg_outcomes = await run_questions(negatives, replay, nearest, k=k_chosen, min_score=-1.0)
    threshold = choose_threshold(dev_outcomes)
    passing = [o.question.id for o in neg_outcomes if o.top_score >= threshold]
    out += [
        "",
        "### Minimum score",
        "Top-result score per question (cosine similarity):",
        "",
        "- dev: " + ", ".join(f"{o.question.id} {o.top_score:.3f}" for o in dev_outcomes),
        "- gold section score of dev hits: "
        + ", ".join(
            f"{o.question.id} {o.hits[o.rank - 1].score:.3f}"
            for o in dev_outcomes
            if o.rank is not None
        ),
        "- negatives: " + ", ".join(f"{o.question.id} {o.top_score:.3f}" for o in neg_outcomes),
        "",
        f"Threshold that keeps every dev hit: {threshold:.2f}. "
        f"Negatives at or above it: {len(passing)} of {len(neg_outcomes)}"
        + (f" ({', '.join(passing)})." if passing else "."),
        "",
        "### Chosen",
        f"- chunking: target {chosen.target_tokens}, overlap {chosen.overlap_tokens}, "
        f"heading prefix {'on' if chosen.heading_prefix else 'off'}"
        + (" (defaults kept)" if chosen == DEFAULT_PARAMS else ""),
        f"- k = {k_chosen}" + (" (default kept)" if k_chosen == DEFAULT_K else ""),
        f"- RAG_MIN_SCORE = {threshold:.2f}",
    ]
    print("\n".join(out))
    return 0


async def acceptance(kb_dir: Path) -> int:
    """Ingest with recorded embeddings into the configured database, then answer every
    acceptance question through search_policies, exactly as the tool will."""
    from app.core.config import get_settings
    from app.db.session import create_engine, create_session_factory
    from app.rag.ingest import format_report, ingest
    from app.rag.retrieval import pgvector_nearest

    settings = get_settings()
    replay = CachedEmbedder.load(EMBEDDINGS_PATH)
    questions = load_questions(ACCEPTANCE_PATH)
    engine = create_engine(settings)
    factory = create_session_factory(engine)
    try:
        report = await ingest(factory, replay, kb_dir)
        async with factory() as session:
            outcomes = await run_questions(
                questions,
                replay,
                pgvector_nearest(session),
                k=settings.rag_top_k,
                min_score=settings.rag_min_score,
            )
    finally:
        await engine.dispose()
    print(format_acceptance(outcomes, k=settings.rag_top_k, min_score=settings.rag_min_score))
    print(f"\n(ingestion: {format_report(report)})")
    return 0


def format_acceptance(outcomes: Sequence[Outcome], *, k: int, min_score: float) -> str:
    s = summarize(outcomes)
    cross = [o for o in outcomes if len(o.question.gold) > 1]
    verdict = "pass" if s.hit_rate >= REQUIRED_HIT_RATE else "FAIL"
    lines = [
        f"k = {k} sections, minimum score {min_score:.2f}",
        "",
        f"- hit rate: {s.hits}/{s.total} = {s.hit_rate:.1%} "
        f"(required {REQUIRED_HIT_RATE:.0%}: {verdict})",
        f"- hit@1: {s.hit_at_1}/{s.total}, hit@3: {s.hit_at_3}/{s.total}, MRR {s.mrr:.3f}",
        f"- questions with two gold sections where both were returned: "
        f"{sum(o.found_all for o in cross)}/{len(cross)} (reported, not gated)",
        f"- tokens returned per question: {s.tokens_per_query:.0f}",
        "",
        "| id | rank | top result |",
        "|---|---|---|",
    ]
    for o in outcomes:
        top = (
            f"{o.hits[0].document} / {o.hits[0].heading_path} ({o.hits[0].score:.3f})"
            if o.hits
            else "none"
        )
        lines.append(f"| {o.question.id} | {o.rank or 'miss'} | {top} |")
    misses = [o for o in outcomes if o.rank is None]
    if misses:
        lines += ["", "Misses:"]
        for o in misses:
            returned = "; ".join(f"{h.document} / {h.heading_path}" for h in o.hits) or "nothing"
            gold = "; ".join(f"{d} / {h}" for d, h in sorted(o.question.gold))
            lines.append(f'- {o.question.id} "{o.question.text}" wanted {gold}; got {returned}')
    return "\n".join(lines)


def main() -> None:
    from app.core.config import get_settings

    parser = argparse.ArgumentParser(description="Retrieval evaluation.")
    parser.add_argument("command", choices=["record", "sweep", "acceptance"])
    args = parser.parse_args()
    kb_dir = get_settings().kb_dir
    command = {"record": record, "sweep": sweep, "acceptance": acceptance}[args.command]
    sys.exit(asyncio.run(command(kb_dir)))


if __name__ == "__main__":
    main()
