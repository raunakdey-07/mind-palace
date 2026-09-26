"""The context product: authority decides, retrieval only adds raw material.

These run against real PostgreSQL with real archive rows, so a pack can only say
what the archive actually supports. No embedding model is loaded on the paths
that assert authority, which is the same condition the degradation probe uses.
"""

from __future__ import annotations

from datetime import datetime

import pytest

from api.services import context_service
from api.services.context_packer import (
    CONFLICTING,
    NO_RELEVANT_MEMORY,
    RESOLVED,
    pack_context,
)

try:
    from tests import test_memory_public as public
except ModuleNotFoundError as exc:  # pragma: no cover - import shim
    if exc.name not in {"tests", "tests.test_memory_public"}:
        raise
    import test_memory_public as public

memory_db = public.memory_db
public_db = public.public_db


@pytest.fixture(autouse=True)
def no_retrieval(monkeypatch):
    """Retrieval is off by default so authority is the only thing under test."""

    async def never(*args, **kwargs):
        return []

    monkeypatch.setattr(context_service, "_retrieve", never)


async def build(store, query, **kwargs):
    kwargs.setdefault("budget_tokens", 4096)
    pack, trace = await context_service.build_context(store.db, store.name, query, **kwargs)
    return pack, trace


async def authored(store, path, key, sentence, **validity):
    """Author one claim whose text reads like a real decision record.

    The shared public fixture appends a unicode/backslashing tail to exercise
    encoding. That tail dominates the embedding and pushes relevant claims under
    the relevance floor, which would test the threshold instead of the pack.
    """
    from api.services import memory

    return await memory.record_version(
        store.db,
        store.corpus,
        path,
        "live-" + path,
        sentence + "\n",
        {
            "claims": [
                {"key": key, "value": sentence, "claim": sentence, "evidence": sentence, **validity}
            ]
        },
        [{"text": sentence, "heading_path": "Decision", "order_index": 0}],
    )


@pytest.fixture
async def corpus(public_db):
    """A small but realistic corpus: the subject word is not in every claim.

    Relevance drops terms that appear in every candidate, because they cannot
    distinguish one. With a corpus of only database claims the word "database"
    would be dropped everywhere and every question would look irrelevant. A real
    corpus has unrelated material, so these tests seed one.
    """
    await authored(
        public_db,
        "docs/storage.md",
        "architecture.database",
        "The primary database is PostgreSQL.",
    )
    await authored(
        public_db,
        "docs/why.md",
        "decision.database.reason",
        "PostgreSQL was chosen for JSONB and advisory locks.",
    )
    await authored(
        public_db,
        "docs/deploy.md",
        "constraint.deploy",
        "Migrations run as a single writer job.",
    )
    return public_db


@pytest.fixture
async def superseded(public_db):
    """A corpus where one key really was superseded, plus unrelated material.

    Supersession requires two versions of the SAME path with the same claim key.
    Two different paths are two documents, not a change.
    """
    await authored(
        public_db,
        "docs/why.md",
        "decision.database.reason",
        "PostgreSQL was chosen for JSONB and advisory locks.",
    )
    await authored(
        public_db, "docs/deploy.md", "constraint.deploy", "Migrations run as a writer job."
    )
    await authored(
        public_db, "docs/db.md", "architecture.database", "The primary database is MySQL."
    )
    await authored(
        public_db, "docs/db.md", "architecture.database", "The primary database is PostgreSQL."
    )
    return public_db


async def test_current_question_returns_a_memory_with_exact_evidence(corpus):
    pack, _ = await build(corpus, "What is the current database?")

    assert pack.status == RESOLVED
    assert [m.key for m in pack.memories] == ["architecture.database"]
    memory = pack.memories[0]
    assert memory.value == "The primary database is PostgreSQL."
    assert memory.status == "CURRENT"
    assert memory.path == "docs/storage.md"
    assert memory.evidence, "a memory must carry its evidence"
    quote = memory.evidence[0]
    assert "PostgreSQL" in quote.quote
    assert quote.end_offset - quote.start_offset == len(quote.quote)
    assert "PostgreSQL" in pack.context
    assert "CURRENT" in pack.context


async def test_conflict_is_returned_as_a_conflict_not_flattened(corpus):
    await authored(
        corpus,
        "adrs/adr-004.md",
        "architecture.database",
        "The primary database is SQLite for the edge tier.",
        valid_from="2024-01-01T00:00:00+00:00",
    )

    pack, _ = await build(corpus, "What sources disagree about the database?")

    assert pack.status == CONFLICTING
    assert len(pack.conflicts) == 1
    group = pack.conflicts[0]
    assert group.key == "architecture.database"
    assert len(group.options) == 2
    for option in group.options:
        assert option.evidence, "each side of a conflict needs its own evidence"
    assert "CONFLICT" in pack.context
    assert "PostgreSQL" in pack.context
    assert "SQLite" in pack.context


async def test_supersession_is_reported_as_a_change(superseded):
    pack, _ = await build(superseded, "When did the database change?")

    assert pack.status == RESOLVED
    assert pack.changes
    relationship = pack.changes[0].relationship
    assert relationship in {"SUPERSEDES", "LIFECYCLE"}
    assert "CHANGES" in pack.context
    assert "MySQL" in pack.context
    assert "PostgreSQL" in pack.context


async def test_history_is_separate_from_current(superseded):
    pack, _ = await build(superseded, "What database was used before?")

    assert "HISTORY" in pack.context
    assert "MySQL" in pack.context
    # The superseded statement must be labelled, not presented as the current one.
    current_claims = pack.memories
    assert [m.status for m in current_claims] == ["CURRENT"]
    assert all("MySQL" not in m.value for m in current_claims)
    assert "MySQL" in pack.context.split("HISTORY", 1)[1]


async def test_unanswerable_question_abstains_and_is_never_padded(corpus):

    # Retrieval would happily return the storage chunk. The archive says the
    # question is unanswerable, and that decision wins.
    async def returns_chunks(*args, **kwargs):
        return [
            type(
                "R",
                (),
                {
                    "text": "The primary database is PostgreSQL.",
                    "score": 0.9,
                    "source_title": "storage",
                    "source_path": "docs/storage.md",
                    "doc_id": "live-docs/storage.md",
                    "heading_path": None,
                    "source_document_type": "note",
                },
            )()
        ]

    original = context_service._retrieve
    context_service._retrieve = returns_chunks
    try:
        pack, trace = await build(corpus, "What payroll provider does the company use?")
    finally:
        context_service._retrieve = original

    assert pack.status == NO_RELEVANT_MEMORY
    assert pack.context == ""
    assert pack.chunks == []
    assert pack.memories == []
    assert trace["status"] == NO_RELEVANT_MEMORY


async def test_retrieval_never_promotes_a_chunk_into_a_memory(corpus):

    async def unrelated_chunk(*args, **kwargs):
        return [
            type(
                "R",
                (),
                {
                    "text": "An unrelated paragraph about coffee brewing.",
                    "score": 0.4,
                    "source_title": "notes",
                    "source_path": "notes/coffee.md",
                    "doc_id": "live-notes/coffee.md",
                    "heading_path": None,
                    "source_document_type": "note",
                },
            )()
        ]

    original = context_service._retrieve
    context_service._retrieve = unrelated_chunk
    try:
        pack, _ = await build(corpus, "What is the current database?")
    finally:
        context_service._retrieve = original

    assert [m.value for m in pack.memories] == ["The primary database is PostgreSQL."]
    assert "coffee" not in pack.context
    # It may appear as raw material, attributed, but never as a memory.
    assert all("coffee" not in m.claim for m in pack.memories)


async def test_as_of_resolves_the_past_without_touching_current(public_db):
    first = await authored(
        public_db, "docs/db.md", "architecture.database", "The primary database is MySQL."
    )
    await authored(
        public_db,
        "docs/deploy.md",
        "constraint.deploy",
        "Migrations run as a single writer job.",
    )
    await authored(
        public_db, "docs/db.md", "architecture.database", "The primary database is PostgreSQL."
    )

    past = datetime.fromisoformat(first["observed_at"].isoformat())
    pack, _ = await build(public_db, "What is the current database?", as_of=past)

    assert "MySQL" in pack.context
    assert "PostgreSQL" not in pack.context
    assert pack.as_of is not None


async def test_budget_truncates_and_says_so(public_db):
    for i in range(6):
        await authored(
            public_db,
            f"ops/incident-{i}.md",
            f"incident.{i}",
            f"Incident {i} was resolved by restarting the queue worker.",
        )
    await authored(
        public_db, "docs/db.md", "architecture.database", "The primary database is PostgreSQL."
    )

    pack, _ = await build(public_db, "Which incidents were resolved?", budget_tokens=40)

    assert pack.truncated is True
    assert pack.token_estimate <= 40
    assert pack.context


async def test_context_answers_without_a_model(public_db, monkeypatch):
    """The authority rung must not depend on a model being loadable."""
    await authored(
        public_db,
        "docs/db.md",
        "architecture.database",
        "The primary database is PostgreSQL.",
    )
    await authored(
        public_db, "docs/deploy.md", "constraint.deploy", "Migrations run as a writer job."
    )

    from api.services import embedder as embedder_mod

    def broken(*args, **kwargs):
        raise OSError("missing local model")

    # The embedder the query path builds on demand.
    monkeypatch.setattr(embedder_mod, "Embedder", broken)

    pack, _ = await build(public_db, "What is the current database?")

    assert pack.status == RESOLVED
    assert [m.value for m in pack.memories] == ["The primary database is PostgreSQL."]
    assert pack.memories[0].evidence


async def test_pack_context_legacy_mode_reports_empty_corpus():
    pack = pack_context("q", [], budget_tokens=100)
    assert pack.status == "empty_corpus"
    assert pack.context == ""
    assert pack.memories == []


async def test_every_memory_in_a_pack_is_grounded(corpus):
    pack, _ = await build(corpus, "What is the current database?")

    for item in pack.memories:
        assert item.claim
        assert item.observed_at
        assert item.evidence
        for quote in item.evidence:
            assert quote.path
            assert quote.version_id
            assert quote.end_offset > quote.start_offset
