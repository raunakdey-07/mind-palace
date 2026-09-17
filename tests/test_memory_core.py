"""Memory core contracts.

DB tests are gated by MEMORY_TEST_DATABASE_URL (or DATABASE_URL). They create a
random schema and random corpora inside ONE rolled-back transaction, apply only
004 against a minimal legacy shape, and never delete/truncate production data.
The database role needs CREATE SCHEMA permission. No embeddings or models needed.
"""

from __future__ import annotations

import hashlib
import importlib.util
import os
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import AsyncMock
from uuid import uuid4

import pytest
from alembic.migration import MigrationContext
from alembic.operations import Operations
from sqlalchemy import text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine
from sqlalchemy.pool import NullPool

from api.services import memory


def payload(value="blue", **extra):
    return {
        "claims": [
            {
                "key": "color",
                "value": value,
                "claim": f"Color is {value}",
                "evidence": value,
                **extra,
            }
        ]
    }


def chunks(value="blue"):
    return [{"text": f"Color is {value}.", "heading_path": "Details", "order_index": 0}]


async def record(db, corpus, value="blue", path="note.md", metadata=None):
    return await memory.record_version(
        db,
        corpus,
        path,
        "live-" + value,
        f"---\ntitle: Color\n---\nColor is {value}.",
        payload(value) if metadata is None else metadata,
        chunks(value),
    )


def test_identity_and_canonicalization():
    assert (
        memory.memory_document_id("corpus", "a:b.md")
        == hashlib.sha256(b"corpus:a:b.md").hexdigest()
    )
    assert memory._hash({"b": 2, "a": 1}) == memory._hash({"a": 1, "b": 2})
    assert memory._hash("a", "b:c") != memory._hash("a:b", "c")
    assert memory._json_value({"date": date(2024, 1, 2)}) == {"date": "2024-01-02"}


@pytest.mark.parametrize("bad", [float("nan"), float("inf"), {1: "bad"}, {"set": {1, 2}}])
def test_reject_lossy_yaml(bad):
    with pytest.raises(ValueError):
        memory._canonical(bad)


@pytest.mark.parametrize(
    "bad",
    [
        {"claims": None},
        {"claims": "blue"},
        {"claims": [{}]},
        {"claims": [{"key": "color", "value": 1, "claim": "x", "evidence": ""}]},
        payload("BLUE"),
        payload("missing"),
        payload(valid_from="yesterday"),
        payload(valid_from="2025-01-02", valid_until="2025-01-01"),
        {"claims": payload()["claims"] * 2},
    ],
)
def test_invalid_claims(bad):
    with pytest.raises(ValueError):
        memory._prepare(bad, chunks())


def test_no_auto_claims_and_unicode_evidence():
    assert memory._prepare({}, chunks())[2] == []
    metadata = payload("café", valid_from=date(2024, 1, 2))
    metadata["claims"][0]["claim"] = "café"
    _, _, claims = memory._prepare(
        metadata,
        [
            {"text": "😀 café", "order_index": 7, "heading_path": None},
            {"text": "café", "order_index": 9},
        ],
    )
    assert claims[0]["start_offset"] == 2
    assert claims[0]["end_offset"] == 6
    assert claims[0]["order_index"] == 7
    assert claims[0]["valid_from"] == datetime(2024, 1, 2, tzinfo=timezone.utc)
    assert claims[0]["valid_until"] is None


@pytest.mark.parametrize(
    "bad",
    [
        [{"text": "x"}],
        [{"text": "x", "order_index": -1}],
        [{"text": "x", "order_index": True}],
        chunks() * 2,
        [{"text": "x", "order_index": 0, "heading_path": []}],
    ],
)
def test_invalid_chunks(bad):
    with pytest.raises(ValueError):
        memory._prepare({}, bad)


def test_observation_timestamps_require_timezone():
    for value in ("2024-01-01", datetime(2024, 1, 1), date(2024, 1, 1)):
        with pytest.raises(ValueError):
            memory._timestamp(value)
    assert memory._timestamp("2024-01-01T01:00:00+01:00") == datetime(
        2024, 1, 1, tzinfo=timezone.utc
    )


@pytest.mark.asyncio
async def test_invalid_evidence_fails_before_any_archive_write():
    db = AsyncMock()
    with pytest.raises(ValueError, match="exact substring"):
        await memory.record_version(db, "c", "p", "d", "blue", payload("red"), chunks())
    db.execute.assert_not_called()
    db.commit.assert_not_called()
    db.rollback.assert_not_called()


def test_service_never_commits_or_rolls_back():
    import ast

    tree = ast.parse(Path(memory.__file__).read_text())
    assert not [
        node
        for node in ast.walk(tree)
        if isinstance(node, ast.Call)
        and isinstance(node.func, ast.Attribute)
        and node.func.attr in {"commit", "rollback"}
    ]


@pytest.fixture
async def memory_db():
    url = os.getenv("MEMORY_TEST_DATABASE_URL") or os.getenv("DATABASE_URL")
    if not url:
        pytest.skip("set MEMORY_TEST_DATABASE_URL or DATABASE_URL for isolated PostgreSQL tests")
    url = (
        url.replace("postgresql+psycopg://", "postgresql+asyncpg://")
        .replace("postgresql+psycopg2://", "postgresql+asyncpg://")
        .replace("postgresql://", "postgresql+asyncpg://")
    )
    engine = create_async_engine(url, poolclass=NullPool, connect_args={"timeout": 10})
    schema = "memory_test_" + uuid4().hex
    corpora = [hashlib.sha256(uuid4().bytes).hexdigest() for _ in range(2)]
    migration_path = Path(__file__).parents[1] / "migrations/versions/004_memory.py"
    spec = importlib.util.spec_from_file_location("memory_migration", migration_path)
    migration = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(migration)
    try:
        async with engine.connect() as conn:
            transaction = await conn.begin()
            try:
                await conn.execute(text(f'CREATE SCHEMA "{schema}"'))
                await conn.execute(text(f'SET LOCAL search_path TO "{schema}", pg_catalog'))
                await conn.execute(text("""
                    CREATE TABLE corpora (id CHAR(64) PRIMARY KEY, name TEXT NOT NULL UNIQUE)
                """))
                await conn.execute(text("""
                    CREATE TABLE documents (id CHAR(64) PRIMARY KEY,
                        corpus_id CHAR(64) REFERENCES corpora(id), path TEXT NOT NULL)
                """))
                await conn.execute(text("""
                    CREATE TABLE chunks (id TEXT PRIMARY KEY,
                        doc_id CHAR(64) REFERENCES documents(id) ON DELETE CASCADE, text TEXT)
                """))
                for corpus in corpora:
                    await conn.execute(
                        text("INSERT INTO corpora VALUES (:c, :n)"),
                        {"c": corpus, "n": "memory-test-" + corpus},
                    )
                await conn.execute(
                    text("INSERT INTO documents VALUES (:id, :c, 'legacy.md')"),
                    {"id": "legacy", "c": corpora[0]},
                )

                def upgrade(sync_conn):
                    with Operations.context(MigrationContext.configure(sync_conn)):
                        migration.upgrade()

                await conn.run_sync(upgrade)
                async with AsyncSession(bind=conn, expire_on_commit=False) as db:
                    yield db, corpora[0], corpora[1], engine
            finally:
                await transaction.rollback()
    finally:
        await engine.dispose()


@pytest.mark.asyncio
async def test_backfill_identity_only(memory_db):
    db, corpus, _, _ = memory_db
    row = (
        (
            await db.execute(
                text("SELECT * FROM memory_documents WHERE corpus_id = :c"), {"c": corpus}
            )
        )
        .mappings()
        .one()
    )
    assert row["id"] == memory.memory_document_id(corpus, "legacy.md")
    assert await memory.history(db, corpus) == []
    first = await record(db, corpus, path="legacy.md")
    assert first["event"] == "NEW"
    assert first["memory_document_id"] == row["id"]


@pytest.mark.asyncio
async def test_lifecycle_metadata_restore_and_history(memory_db):
    db, corpus, _, _ = memory_db
    first = await record(db, corpus)
    unchanged = await record(db, corpus)
    assert unchanged["id"] == first["id"] and unchanged["skipped"]
    modified = await record(db, corpus, metadata={**payload(), "title": "Metadata only"})
    assert modified["event"] == "MODIFIED" and modified["id"] != first["id"]
    assert modified["predecessor_id"] == first["id"]
    assert await memory.record_deletion(db, corpus, "note.md")
    assert not await memory.record_deletion(db, corpus, "note.md")
    assert not await memory.record_deletion(db, corpus, "unknown.md")
    deleted_view = await memory.query(db, corpus)
    assert not deleted_view["current_memories"]
    assert len(deleted_view["historical_memories"]) == 2
    restored = await record(db, corpus)
    assert restored["event"] == "RESTORED"
    assert restored["id"] != first["id"]
    assert restored["memory_document_id"] == first["memory_document_id"]
    history = await memory.history(db, corpus, "note.md")
    assert [v["event"] for v in history] == ["NEW", "MODIFIED", "DELETED", "RESTORED"]
    assert [v["version_number"] for v in history] == [1, 2, 3, 4]
    assert history[0]["content"].startswith("---\ntitle:")
    assert history[1]["metadata"]["title"] == "Metadata only"
    assert history[1]["claims"][0]["supersedes_id"] == history[0]["claims"][0]["id"]
    # Immediate predecessor is a tombstone: no invented link across that gap.
    assert history[3]["claims"][0]["supersedes_id"] is None
    events = await memory.changes(db, corpus, "note.md")
    assert events[1]["previous_claims"] == history[0]["claims"]
    assert events[1]["new_claims"] == history[1]["claims"]
    assert await memory.history(db, corpus, "absent.md") == []
    assert [v["observed_at"] for v in history] == sorted(v["observed_at"] for v in history)


@pytest.mark.asyncio
async def test_raw_content_and_metadata_change_detection(memory_db):
    db, corpus, _, _ = memory_db
    first = await record(db, corpus)
    changed = await memory.record_version(
        db, corpus, "note.md", "new-live-id", "new raw source", payload(), chunks()
    )
    assert changed["event"] == "MODIFIED"
    # Live identifiers/rechunking are not authored changes.
    skipped = await memory.record_version(
        db,
        corpus,
        "note.md",
        "another-live-id",
        "new raw source",
        payload(),
        [{"text": "Color is blue.", "order_index": 8}],
    )
    assert skipped["skipped"] and skipped["id"] == changed["id"]
    assert first["id"] != changed["id"]


@pytest.mark.asyncio
async def test_current_conflicts_supersession_and_as_of(memory_db):
    db, corpus, _, _ = memory_db
    first = await record(db, corpus)
    second = await record(db, corpus, "red")
    await record(db, corpus, "blue", path="other.md")
    current = await memory.query(db, corpus)
    assert current["current_memories"] == []
    assert all(c["status"] == "CONFLICTING" for c in current["conflicts"][0]["claims"])
    assert len(current["historical_memories"]) == 1
    assert len(current["superseded_memories"]) == 1
    assert len(current["conflicts"]) == 1
    assert len(current["evidence"]) == 3
    assert all(
        c["valid_from"] is None and c["valid_until"] is None for c in current["current_memories"]
    )
    assert current["conflicts"] == (await memory.query(db, corpus))["conflicts"]
    past = await memory.query(db, corpus, as_of=first["observed_at"])
    assert [c["value"] for c in past["current_memories"]] == ["blue"]
    assert not past["historical_memories"] and not past["conflicts"]
    past2 = await memory.query(db, corpus, as_of=second["observed_at"].isoformat())
    assert [c["value"] for c in past2["current_memories"]] == ["red"]
    assert len(past2["superseded_memories"]) == 1
    assert not (await memory.query(db, corpus, "nonexistent"))["evidence"]
    filtered = await memory.query(db, corpus, "RED")
    assert filtered["current_memories"] == []
    # Both sides of a matching conflict remain grounded.
    assert len(filtered["conflicts"]) == 1 and len(filtered["evidence"]) == 2
    await memory.record_deletion(db, corpus, "other.md")
    assert not (await memory.query(db, corpus))["conflicts"]


@pytest.mark.asyncio
async def test_equal_values_no_conflict_and_no_inferred_claims(memory_db):
    db, corpus, _, _ = memory_db
    await record(db, corpus)
    await record(db, corpus, path="same.md")
    await record(db, corpus, "red", path="unclaimed.md", metadata={})
    view = await memory.query(db, corpus)
    assert len(view["current_memories"]) == 2
    assert not view["conflicts"]
    assert not (await memory.history(db, corpus, "unclaimed.md"))[0]["claims"]
    # Dropping authored claims retires the old ones but does not invent supersession.
    await record(db, corpus, metadata={})
    view = await memory.query(db, corpus)
    assert len(view["current_memories"]) == 1
    assert len(view["historical_memories"]) == 1
    assert not view["superseded_memories"]


@pytest.mark.asyncio
async def test_validity_is_not_observation_time(memory_db):
    db, corpus, _, _ = memory_db
    first = await record(
        db, corpus, metadata=payload(valid_from="1999-01-01", valid_until="1999-02-01")
    )
    view = await memory.query(db, corpus, as_of=first["observed_at"])
    assert view["current_memories"] == []
    claim = view["uncertain_memories"][0]
    assert claim["status"] == "UNCERTAIN"
    assert claim["valid_from"].startswith("1999-01-01")
    before = await memory.query(db, corpus, as_of=first["observed_at"] - timedelta(microseconds=1))
    assert not before["current_memories"]


@pytest.mark.asyncio
async def test_live_physical_deletion_keeps_archive_and_evidence(memory_db):
    db, corpus, _, _ = memory_db
    version = await record(db, corpus)
    await db.execute(
        text("INSERT INTO documents VALUES ('live-blue', :c, 'note.md')"), {"c": corpus}
    )
    await db.execute(
        text("INSERT INTO chunks VALUES ('live-chunk', 'live-blue', 'Color is blue.')")
    )
    assert await memory.record_deletion(db, corpus, "note.md")
    await db.execute(
        text("DELETE FROM documents WHERE corpus_id = :c AND path = 'note.md'"), {"c": corpus}
    )
    assert (await db.execute(text("SELECT count(*) FROM chunks"))).scalar_one() == 0
    rows = await memory.evidence(db, corpus, version_id=version["id"])
    assert len(rows) == 1 and rows[0]["chunk"]["text"] == "Color is blue."
    assert rows[0]["quote"] == "blue"
    assert await memory.get_evidence(db, corpus, rows[0]["id"]) == rows[0]
    assert await memory.evidence(db, corpus, claim_id=rows[0]["claim_id"]) == rows


@pytest.mark.asyncio
async def test_invalid_claim_rolls_back_live_and_archive_work(memory_db):
    db, corpus, _, _ = memory_db
    with pytest.raises(ValueError):
        async with db.begin_nested():
            await record(db, corpus)
            await db.execute(
                text("INSERT INTO documents VALUES ('bad-live', :c, 'bad.md')"), {"c": corpus}
            )
            await record(db, corpus, metadata=payload("not in chunk"), path="bad.md")
    assert await memory.history(db, corpus) == []
    assert not (await db.execute(text("SELECT 1 FROM documents WHERE id = 'bad-live'"))).first()
    # Rolled back predecessors do not perturb deterministic retry IDs.
    async with db.begin_nested() as savepoint:
        first = await record(db, corpus)
        second = await record(db, corpus, "red")
        await savepoint.rollback()
    retry_first = await record(db, corpus)
    retry_second = await record(db, corpus, "red")
    assert first["id"] == retry_first["id"] and second["id"] == retry_second["id"]


@pytest.mark.asyncio
async def test_snapshots_and_corpus_isolation(memory_db):
    db, corpus, other, _ = memory_db
    empty = await memory.snapshot(db, corpus)
    assert empty["version_ids"] == []
    first = await record(db, corpus)
    foreign = await record(db, other)
    assert first["id"] != foreign["id"]
    assert first["memory_document_id"] != foreign["memory_document_id"]
    snap = await memory.snapshot(db, corpus)
    assert snap["version_ids"] == [first["id"]]
    await record(db, corpus, "red")
    await memory.record_deletion(db, corpus, "note.md")
    assert await memory.get_snapshot(db, corpus, snap["id"]) == snap
    historical = await memory.snapshot(db, corpus, as_of=first["observed_at"])
    assert historical["version_ids"] == [first["id"]]
    all_history = await memory.snapshot(db, corpus)
    assert len(all_history["version_ids"]) == 3
    assert await memory.get_snapshot(db, other, snap["id"]) is None
    ev = (await memory.evidence(db, corpus))[0]
    assert await memory.get_evidence(db, other, ev["id"]) is None
    assert await memory.evidence(db, other, version_id=first["id"]) == []
    assert len(await memory.history(db, other)) == 1
    with pytest.raises(ValueError, match="future"):
        await memory.snapshot(db, corpus, as_of=datetime.now(timezone.utc) + timedelta(days=1))


@pytest.mark.asyncio
async def test_database_immutability_and_scoped_provenance(memory_db):
    db, corpus, other, _ = memory_db
    first = await record(db, corpus)
    foreign = await record(db, other)
    for table in (
        "memory_documents",
        "memory_versions",
        "memory_chunks",
        "memory_claims",
        "memory_evidence",
        "memory_snapshots",
        "memory_snapshot_versions",
    ):
        with pytest.raises(IntegrityError, match="append-only"):
            async with db.begin_nested():
                await db.execute(text(f"DELETE FROM {table} WHERE corpus_id = :c"), {"c": corpus})
    with pytest.raises(IntegrityError, match="append-only"):
        async with db.begin_nested():
            await db.execute(
                text("UPDATE memory_versions SET content = 'tampered' WHERE id = :id"),
                {"id": first["id"]},
            )
    snap = await memory.snapshot(db, corpus)
    with pytest.raises(IntegrityError):
        async with db.begin_nested():
            await db.execute(
                text("""
                INSERT INTO memory_snapshot_versions VALUES (:c, :s, :v)
            """),
                {"c": corpus, "s": snap["id"], "v": foreign["id"]},
            )
    # Cross-version evidence is rejected even within a corpus.
    second = await record(db, corpus, "red")
    old_evidence = (await memory.evidence(db, corpus, version_id=first["id"]))[0]
    second_history = (await memory.history(db, corpus))[-1]
    with pytest.raises(IntegrityError):
        async with db.begin_nested():
            await db.execute(
                text("""
                INSERT INTO memory_evidence VALUES (:c, :v, :id, :claim, :chunk, 'blue', 9, 13)
            """),
                {
                    "c": corpus,
                    "v": second["id"],
                    "id": "f" * 64,
                    "claim": second_history["claims"][0]["id"],
                    "chunk": old_evidence["chunk_id"],
                },
            )
    with pytest.raises(IntegrityError, match="exactly match"):
        async with db.begin_nested():
            await db.execute(
                text("""
                INSERT INTO memory_evidence VALUES (:c, :v, :id, :claim, :chunk, 'wrong', 0, 5)
            """),
                {
                    "c": corpus,
                    "v": first["id"],
                    "id": "e" * 64,
                    "claim": old_evidence["claim_id"],
                    "chunk": old_evidence["chunk_id"],
                },
            )


@pytest.mark.asyncio
async def test_corpus_lock_is_transaction_scoped_and_clock_is_not_transaction_start(memory_db):
    db, corpus, other, engine = memory_db
    transaction_start = (await db.execute(text("SELECT transaction_timestamp()"))).scalar_one()
    await memory.lock_corpus(db, corpus)
    await memory.lock_corpus(db, corpus)
    lock_id = int.from_bytes(
        hashlib.sha256(f"memory:{corpus}".encode()).digest()[:8], "big", signed=True
    )
    async with engine.connect() as contender:
        acquired = (
            await contender.execute(text("SELECT pg_try_advisory_xact_lock(:id)"), {"id": lock_id})
        ).scalar_one()
        assert not acquired
        other_lock = int.from_bytes(
            hashlib.sha256(f"memory:{other}".encode()).digest()[:8], "big", signed=True
        )
        assert (
            await contender.execute(
                text("SELECT pg_try_advisory_xact_lock(:id)"), {"id": other_lock}
            )
        ).scalar_one()
    version = await record(db, corpus)
    assert version["observed_at"] > transaction_start
    with pytest.raises(ValueError, match="unknown corpus"):
        await memory.lock_corpus(db, "0" * 64)


@pytest.mark.asyncio
async def test_claim_without_evidence_rejected_at_constraint_boundary(memory_db):
    db, corpus, _, _ = memory_db
    version = await record(db, corpus, metadata={})
    with pytest.raises(IntegrityError, match="requires evidence"):
        async with db.begin_nested():
            await db.execute(
                text("""
                INSERT INTO memory_claims(corpus_id, memory_document_id, version_id,
                    id, key, value, claim)
                VALUES (:c, :d, :v, :id, 'unsupported', '"value"', 'unsupported')
            """),
                {
                    "c": corpus,
                    "d": version["memory_document_id"],
                    "v": version["id"],
                    "id": "f" * 64,
                },
            )
            await db.execute(text("SET CONSTRAINTS ALL IMMEDIATE"))


@pytest.mark.asyncio
async def test_self_supersession_and_future_edge_rejected(memory_db):
    db, corpus, _, _ = memory_db
    version = await record(db, corpus)
    for predecessor in ("f" * 64, (await memory.history(db, corpus))[0]["claims"][0]["id"]):
        with pytest.raises(IntegrityError):
            async with db.begin_nested():
                await db.execute(
                    text("""
                    INSERT INTO memory_claims(corpus_id, memory_document_id, version_id, id,
                        key, value, claim, supersedes_id, supersession_basis)
                    VALUES (:c, :d, :v, :id, 'other', '"value"', 'unsupported',
                        :old, 'same_document_key')
                """),
                    {
                        "c": corpus,
                        "d": version["memory_document_id"],
                        "v": version["id"],
                        "id": "f" * 64,
                        "old": predecessor,
                    },
                )


@pytest.mark.asyncio
async def test_snapshot_replay_and_repeat_cutoff(memory_db):
    db, corpus, _, _ = memory_db
    first = await record(db, corpus)
    saved = await memory.snapshot(db, corpus, first["observed_at"])
    expected = await memory.replay_snapshot(db, corpus, saved["id"])
    await record(db, corpus, "red")
    await memory.record_deletion(db, corpus, "note.md")
    assert await memory.replay_snapshot(db, corpus, saved["id"]) == expected
    assert await memory.snapshot(db, corpus, first["observed_at"]) == saved
    assert expected["current_memories"][0]["value"] == "blue"


def test_reject_claim_text_not_supported_by_quote_chunk():
    metadata = payload()
    metadata["claims"][0]["claim"] = "The color is red."
    with pytest.raises(memory.ClaimValidationError) as error:
        memory.validate_source("colors.md", "Color is blue.", metadata, chunks())
    assert error.value.path == "colors.md"
    assert len(error.value.version_id) == 64
    assert "claim[0]" in error.value.reason
