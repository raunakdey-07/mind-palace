"""Real DB query parity across REST, SDK, MCP and CLI, with offline unit vectors.

Reuse the rollback-only benchmark workload and exact-wire comparison harness.
Only inference is faked; these are interface contracts, not relevance-quality tests.
"""

from pathlib import Path

import pytest

from api.models.memory import MemoryRequest
from api.services import corpora, memory_public
from api.services.memory_benchmark import _claims

try:
    from tests import test_memory_benchmark_interfaces as benchmark
except ModuleNotFoundError as exc:
    if exc.name not in {"tests", "tests.test_memory_benchmark_interfaces"}:
        raise
    import test_memory_benchmark_interfaces as benchmark

workload = benchmark.workload
interfaces = benchmark.interfaces
compare = benchmark.compare

QUESTION = "Which architecture decisions apply?"


@pytest.fixture(autouse=True)
def offline_embedder(monkeypatch):
    from api.services import embedder

    class FakeEmbedder:
        def __init__(self):
            self.calls = []

        def embed(self, texts):
            # Equal unit vectors keep every authored key relevant, deterministically.
            self.calls.append(list(texts))
            return [[1.0, 0.0] for _ in texts]

    fake = FakeEmbedder()
    monkeypatch.setenv("HF_HUB_OFFLINE", "1")
    monkeypatch.setenv("TRANSFORMERS_OFFLINE", "1")
    monkeypatch.setattr(embedder, "Embedder", lambda: fake)
    return fake


async def test_query_intents_and_budgets_across_interfaces(workload, interfaces, offline_embedder):
    for stage in "ABCD":
        await workload.apply_stage(stage)
    cutoff = workload.cutoffs["D"]
    for intent in ("auto", "current", "historical", "temporal", "change", "conflict", "provenance"):
        for budget in (1000, 8000, 128000):
            request = MemoryRequest(
                corpus=workload.corpus,
                query=QUESTION,
                as_of=cutoff,
                valid_at=cutoff,
                intent=intent,
                budget=budget,
            )
            responses = await compare(workload, interfaces, "query", request)
            for response in responses.values():
                assert len(response.canonical_json()) <= budget
                assert response.query == QUESTION
                assert response.state.as_of == response.state.valid_at == cutoff
                if budget == 1000:
                    assert response.truncated
                if budget == 128000:
                    assert response.evidence
                    if intent in {"historical", "change", "provenance"}:
                        assert response.historical_memories
    assert offline_embedder.calls
    assert all(call[0] == QUESTION for call in offline_embedder.calls)


async def test_query_fixed_cutoff_and_snapshot_after_later_stages(workload, interfaces):
    await workload.apply_stage("A")
    cutoff = workload.cutoffs["A"]
    request = MemoryRequest(corpus=workload.corpus, query=QUESTION, as_of=cutoff, budget=128000)
    assert request.intent == "auto"
    before = await compare(workload, interfaces, "query", request)
    for stage in "BCDEFG":
        await workload.apply_stage(stage)
    await compare(workload, interfaces, "query", request, expected=before["REST"])
    snapshots = await compare(
        workload,
        interfaces,
        "query",
        MemoryRequest(
            corpus=workload.corpus,
            query=QUESTION,
            snapshot_id=workload.snapshots["A"].id,
            budget=128000,
        ),
    )
    for response in snapshots.values():
        assert response.evidence == before["REST"].evidence
        assert response.current_memories == before["REST"].current_memories
        assert response.state.snapshot == workload.snapshots["A"].id
        assert response.state.as_of == response.state.valid_at == cutoff
        assert len(response.canonical_json()) <= 128000


async def test_query_foreign_corpus_isolation_across_interfaces(workload, interfaces):
    own = await workload.apply_stage("A")
    foreign_name = workload.corpus + "-foreign"
    async with workload.sessions() as db:
        foreign = await corpora.create_corpus(db, foreign_name)
    path = "architecture/streaming.md"
    content = (Path(workload.spec["stages"][0]["directory"]) / path).read_text(encoding="utf-8")
    for foreign_path in (path, "foreign-only.md"):
        async with workload.sessions() as db:
            result = await workload.ingestion._ingest_content(
                db, content, foreign_path, foreign["id"]
            )
        assert result["success"], result
    foreign_snapshot = await memory_public.execute("snapshot", MemoryRequest(corpus=foreign_name))
    own_ids = {c.id for c in _claims(own)}
    foreign_ids = {c.id for c in _claims(foreign_snapshot)}
    assert own_ids and foreign_ids and own_ids.isdisjoint(foreign_ids)

    for name, saved, included, excluded in (
        (workload.corpus, own, own_ids, foreign_ids),
        (foreign_name, foreign_snapshot, foreign_ids, own_ids),
    ):
        responses = await compare(
            workload,
            interfaces,
            "query",
            MemoryRequest(corpus=name, query=QUESTION, as_of=saved.snapshot.as_of, budget=128000),
        )
        for response in responses.values():
            assert response.corpus == name
            assert response.evidence
            assert {c.id for c in _claims(response)} <= included
            assert not any(cid in response.canonical_json() for cid in excluded)
            assert response.state.as_of == response.state.valid_at == saved.snapshot.as_of
            assert len(response.canonical_json()) <= 128000
            if name == workload.corpus:
                assert "foreign-only.md" not in response.canonical_json()

    for name, other in ((workload.corpus, foreign_snapshot), (foreign_name, own)):
        await benchmark.assert_not_found(
            workload,
            interfaces,
            "query",
            MemoryRequest(corpus=name, query=QUESTION, snapshot_id=other.snapshot.id),
            "snapshot_not_found",
        )
    for name, fields, code in (
        (workload.corpus, {"path": "foreign-only.md"}, "document_not_found"),
        (workload.corpus + "-missing", {}, "corpus_not_found"),
    ):
        await benchmark.assert_not_found(
            workload,
            interfaces,
            "query",
            MemoryRequest(corpus=name, query=QUESTION, as_of=own.snapshot.as_of, **fields),
            code,
        )
