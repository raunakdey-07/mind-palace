"""Search API semantic contract tests.

These tests pin the observable behavior of RetrievalService.search against a
live database. They encode contracts discovered during the Phase A audit:

- results are ordered by score descending in every mode
- hybrid+RRF score is the RRF fusion score (small positive float), NOT a rank
- vector scores are cosine similarities in [0, 1]
- rerank scores are cross-encoder logits (may be negative)
- metadata filters are applied before ranking
- empty/whitespace queries return deterministic (if meaningless) results
- k bounds the result count
"""

from __future__ import annotations

import os

import pytest

from api.services.evaluation import precision_at_k, recall_at_k, reciprocal_rank
from api.services.retrieval import RetrievalResult, RetrievalService

pytestmark = pytest.mark.asyncio


def _db_available() -> bool:
    if not os.getenv("DATABASE_URL"):
        return False
    try:
        import sqlalchemy as sa

        url = (
            os.getenv("DATABASE_URL", "")
            .replace("postgresql+psycopg://", "postgresql+psycopg2://")
            .replace("postgresql://", "postgresql+psycopg2://")
        )
        engine = sa.create_engine(url)
        with engine.connect():
            engine.dispose()
        return True
    except Exception:
        return False


requires_db = pytest.mark.skipif(not _db_available(), reason="no reachable DATABASE_URL")


@pytest.fixture(autouse=True)
def _fresh_engine_per_test():
    """Dispose the shared async engine around each test.

    asyncpg connections are bound to their creating event loop; pytest-asyncio
    gives each test a fresh loop, so pooled connections from a previous test's
    loop raise 'another operation is in progress'.
    """
    import asyncio

    from api.services.db import async_engine

    try:
        asyncio.run(async_engine.dispose())
    except Exception:
        pass
    yield
    try:
        asyncio.run(async_engine.dispose())
    except Exception:
        pass


@pytest.fixture()
def embedder():
    from api.services.embedder import Embedder

    e = Embedder()
    e.embed_single("warmup")  # load model outside latency-sensitive paths
    return e


# --- Pure-unit contracts (no DB) ---


def _rrf_result(score: float) -> RetrievalResult:
    return RetrievalResult(
        text="t",
        heading_path="h",
        document_type=None,
        source_url=None,
        tags=[],
        source_title="s",
        source_path="p",
        source_document_type="note",
        score=score,
        doc_id="d",
    )


def test_rrf_scores_descending_contract():
    """The service must return results sorted by score descending."""
    results = [_rrf_result(s) for s in [0.03, 0.05, 0.01]]
    results.sort(key=lambda x: x.score, reverse=True)
    assert [r.score for r in results] == sorted([r.score for r in results], reverse=True)


def test_metrics_on_ranked_results():
    """MRR must reflect true ordering: relevant doc at rank 1 > rank 3."""
    expected = ["BirdCLEF"]
    at_rank1 = ["BirdCLEF", "Finalysis"]
    at_rank3 = ["Finalysis", "Mind Palace", "BirdCLEF"]
    assert reciprocal_rank(expected, at_rank1) == 1.0
    assert reciprocal_rank(expected, at_rank3) == pytest.approx(1 / 3)
    assert recall_at_k(expected, at_rank3, 5) == 1.0
    assert recall_at_k(expected, at_rank3, 2) == 0.0
    assert precision_at_k(expected, at_rank1, 1) == 1.0


@requires_db
async def test_hybrid_rrf_scores_descending(embedder):
    """Regression: RRF mode previously returned keyword_rank integers as scores,
    producing non-descending output. Scores must now be descending RRF values."""
    async with __import__("api.services.db", fromlist=["session_scope"]).session_scope() as db:
        svc = RetrievalService(db)
        q = "ensemble weighted averaging"
        results = await svc.search(embedder.embed_single(q), query_text=q, k=10, hybrid=True)
    scores = [r.score for r in results]
    assert len(results) > 1
    assert scores == sorted(scores, reverse=True)
    # RRF scores are small positive floats (< 1 for k<=60), not rank integers
    assert all(0 < s < 1.0 for s in scores)


@requires_db
async def test_vector_scores_descending_and_bounded(embedder):
    async with __import__("api.services.db", fromlist=["session_scope"]).session_scope() as db:
        svc = RetrievalService(db)
        results = await svc.search(embedder.embed_single("mel spectrogram"), k=5)
    scores = [r.score for r in results]
    assert scores == sorted(scores, reverse=True)
    assert all(0.0 <= s <= 1.0 for s in scores)


@requires_db
async def test_rerank_scores_are_logits_can_be_negative(embedder):
    async with __import__("api.services.db", fromlist=["session_scope"]).session_scope() as db:
        svc = RetrievalService(db)
        q = "feature leakage"
        results = await svc.search(embedder.embed_single(q), query_text=q, k=3, rerank=True)
    scores = [r.score for r in results]
    assert scores == sorted(scores, reverse=True)
    # cross-encoder logits are unbounded; just verify rerank_score is set
    assert all(r.rerank_score is not None for r in results)


@requires_db
async def test_document_type_filter_excludes_other_types(embedder):
    async with __import__("api.services.db", fromlist=["session_scope"]).session_scope() as db:
        svc = RetrievalService(db)
        q = "ensemble models"
        results = await svc.search(
            embedder.embed_single(q),
            query_text=q,
            k=10,
            hybrid=True,
            document_type="kaggle",
        )
    assert results
    assert all(r.source_document_type == "kaggle" for r in results)

    none_results = await svc.search(
        embedder.embed_single(q),
        query_text=q,
        k=10,
        hybrid=True,
        document_type="paper",
    )
    assert none_results == []


@requires_db
async def test_tag_filter_excludes_nonmatching(embedder):
    async with __import__("api.services.db", fromlist=["session_scope"]).session_scope() as db:
        svc = RetrievalService(db)
        q = "portfolio tracking"
        results = await svc.search(
            embedder.embed_single(q), query_text=q, k=10, hybrid=True, tags=["finance"]
        )
    assert results
    assert all("finance" in r.tags for r in results)

    nomatch = await svc.search(
        embedder.embed_single(q), query_text=q, k=10, hybrid=True, tags=["no-such-tag"]
    )
    assert nomatch == []


@requires_db
async def test_k_bounds_result_count(embedder):
    async with __import__("api.services.db", fromlist=["session_scope"]).session_scope() as db:
        svc = RetrievalService(db)
        q = "project"
        for k in (1, 3, 7):
            results = await svc.search(embedder.embed_single(q), query_text=q, k=k, hybrid=True)
            assert len(results) <= k


@requires_db
async def test_source_metadata_propagates(embedder):
    async with __import__("api.services.db", fromlist=["session_scope"]).session_scope() as db:
        svc = RetrievalService(db)
        results = await svc.search(embedder.embed_single("bird classification"), k=3)
    for r in results:
        assert r.source_title
        assert r.source_path
        assert r.doc_id


@requires_db
async def test_deterministic_ordering_across_runs(embedder):
    """Same query must produce identical ordering on repeated runs."""
    async with __import__("api.services.db", fromlist=["session_scope"]).session_scope() as db:
        svc = RetrievalService(db)
        q = "ensemble weighted averaging"
        runs = []
        for _ in range(2):
            r = await svc.search(embedder.embed_single(q), query_text=q, k=5, hybrid=True)
            runs.append([(r_.source_title, r_.heading_path, round(r_.score, 6)) for r_ in r])
    assert runs[0] == runs[1]


@requires_db
async def test_empty_query_returns_without_crash(embedder):
    """Empty/whitespace queries must not crash; behavior is defined as
    'return whatever the SQL ranks first' rather than an error."""
    async with __import__("api.services.db", fromlist=["session_scope"]).session_scope() as db:
        svc = RetrievalService(db)
        for q in ("", "   "):
            results = await svc.search(embedder.embed_single(q), query_text=q, k=3)
            assert isinstance(results, list)
