"""M006 multiple evidence contracts; isolated PostgreSQL schemas, no live data changes."""

from __future__ import annotations

import importlib.util
from pathlib import Path
from unittest.mock import AsyncMock

import pytest
from alembic.migration import MigrationContext
from alembic.operations import Operations
from sqlalchemy import text
from sqlalchemy.exc import IntegrityError

from api.models.memory import MemoryRequest, State
from api.services import memory, memory_public

try:
    from tests import test_memory_core as core
except ModuleNotFoundError as exc:
    if exc.name not in {"tests", "tests.test_memory_core"}:
        raise
    import test_memory_core as core

memory_db = core.memory_db


async def migrate(db, direction):
    path = Path(__file__).parents[1] / "migrations/versions/005_multiple_evidence.py"
    spec = importlib.util.spec_from_file_location("multiple_evidence_migration", path)
    migration = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(migration)

    def apply(conn):
        with Operations.context(MigrationContext.configure(conn)):
            getattr(migration, direction)()

    await (await db.connection()).run_sync(apply)


@pytest.fixture
async def multiple_db(memory_db):
    await migrate(memory_db[0], "upgrade")
    return memory_db


def authored(evidence=None):
    return core.payload(evidence=["first", "second"] if evidence is None else evidence)


def supporting_chunks():
    return [
        {"text": "😀 Color is blue. second", "order_index": 9},
        {"text": "Color is blue. first first", "order_index": 2},
    ]


async def record_multiple(db, corpus, **overrides):
    return await memory.record_version(
        db,
        corpus,
        **{
            "path": "multi.md",
            "document_id": "live-multi",
            "content": "Color is blue. first\n😀 Color is blue. second",
            "metadata": authored(),
            "chunks": supporting_chunks(),
            **overrides,
        },
    )


async def insert_reference(db, evidence, **overrides):
    row = {
        **evidence,
        "id": memory._hash("second-reference", evidence["id"]),
        "quote": "Color is blue",
        "start_offset": 0,
        "end_offset": 13,
        **overrides,
    }
    await db.execute(
        text("""
        INSERT INTO memory_evidence
            (corpus_id, version_id, id, claim_id, chunk_id, quote, start_offset, end_offset)
        VALUES (:corpus_id, :version_id, :id, :claim_id, :chunk_id,
                :quote, :start_offset, :end_offset)
        """),
        row,
    )


@pytest.mark.asyncio
async def test_two_valid_same_corpus_same_version_references(multiple_db):
    db, corpus, _, _ = multiple_db
    version = await core.record(db, corpus)
    first = (await memory.evidence(db, corpus, version_id=version["id"]))[0]
    # Both quotes exactly match the same archived supporting chunk. Only 004's
    # UNIQUE(corpus_id, claim_id), not provenance or offsets, blocks this insert.
    await insert_reference(db, first)
    await db.execute(text("SET CONSTRAINTS ALL IMMEDIATE"))
    rows = await memory.evidence(db, corpus, claim_id=first["claim_id"])
    assert len(rows) == 2
    assert {row["quote"] for row in rows} == {"blue", "Color is blue"}
    assert {row["version_id"] for row in rows} == {version["id"]}


@pytest.mark.asyncio
async def test_released_004_still_rejects_second_reference(memory_db):
    db, corpus, _, _ = memory_db
    await core.record(db, corpus)
    first = (await memory.evidence(db, corpus))[0]
    with pytest.raises(IntegrityError, match="memory_evidence_corpus_id_claim_id_key"):
        async with db.begin_nested():
            await insert_reference(db, first)


async def constraints(db):
    return dict((await db.execute(text("""
        SELECT conname, pg_get_constraintdef(oid) FROM pg_constraint
        WHERE conrelid = 'memory_evidence'::regclass
    """))).all())


@pytest.mark.asyncio
async def test_migration_preserves_rows_fks_and_safe_round_trip(memory_db):
    db, corpus, _, _ = memory_db
    await core.record(db, corpus)
    await db.execute(text("SET CONSTRAINTS ALL IMMEDIATE"))
    original = await constraints(db)
    rows = await memory.evidence(db, corpus)
    for _ in range(2):
        await migrate(db, "upgrade")
        upgraded = await constraints(db)
        assert "memory_evidence_corpus_id_claim_id_key" not in upgraded
        assert upgraded["uq_memory_evidence_reference"] == (
            "UNIQUE (corpus_id, claim_id, chunk_id, start_offset, end_offset)"
        )
        fks = {k: v for k, v in original.items() if v.startswith("FOREIGN KEY")}
        assert len(fks) == 2
        assert {k: v for k, v in upgraded.items() if v.startswith("FOREIGN KEY")} == fks
        assert any("FOREIGN KEY (corpus_id, version_id, claim_id)" in v for v in fks.values())
        assert any("FOREIGN KEY (corpus_id, version_id, chunk_id)" in v for v in fks.values())
        index = (await db.execute(text("""
            SELECT indexdef FROM pg_indexes WHERE schemaname = current_schema()
                AND indexname = 'idx_memory_evidence_claim'
        """))).scalar_one()
        assert "UNIQUE" not in index and "(corpus_id, claim_id)" in index
        assert await memory.evidence(db, corpus) == rows
        await migrate(db, "downgrade")
        assert await constraints(db) == original
        assert await memory.evidence(db, corpus) == rows
        with pytest.raises(IntegrityError, match="memory_evidence_corpus_id_claim_id_key"):
            async with db.begin_nested():
                await insert_reference(db, rows[0])
    await migrate(db, "upgrade")
    await insert_reference(db, rows[0])
    assert len(await memory.evidence(db, corpus)) == 2


@pytest.mark.asyncio
async def test_downgrade_refuses_multiple_without_losing_rows(multiple_db):
    db, corpus, _, _ = multiple_db
    await record_multiple(db, corpus)
    await db.execute(text("SET CONSTRAINTS ALL IMMEDIATE"))
    before = await memory.history(db, corpus)
    schema = await constraints(db)
    with pytest.raises(IntegrityError, match="cannot downgrade: multiple evidence"):
        async with db.begin_nested():
            await migrate(db, "downgrade")
    assert await memory.history(db, corpus) == before
    assert await constraints(db) == schema
    # The failed downgrade also leaves snapshot protection installed.
    await memory.snapshot(db, corpus)
    with pytest.raises(IntegrityError, match="snapshotted version"):
        async with db.begin_nested():
            await insert_reference(
                db,
                (await memory.evidence(db, corpus))[0],
                quote="Color",
                start_offset=2,
                end_offset=7,
                chunk_id=before[0]["chunks"][1]["id"],
            )


@pytest.mark.asyncio
async def test_reference_uniqueness_and_scoped_foreign_keys(multiple_db):
    db, corpus, foreign, _ = multiple_db
    await core.record(db, corpus)
    await core.record(db, foreign)
    local = (await memory.evidence(db, corpus))[0]
    other = (await memory.evidence(db, foreign))[0]
    with pytest.raises(IntegrityError, match="uq_memory_evidence_reference"):
        async with db.begin_nested():
            await insert_reference(db, local, quote="blue", start_offset=9, end_offset=13)
    # Valid local quote/chunk, but a claim from another corpus: the claim FK rejects it.
    with pytest.raises(IntegrityError, match="foreign key constraint"):
        async with db.begin_nested():
            await insert_reference(db, local, claim_id=other["claim_id"])
    for overrides in (
        {"chunk_id": other["chunk_id"]},
        {"corpus_id": foreign},
        {"version_id": other["version_id"]},
    ):
        with pytest.raises(IntegrityError):
            async with db.begin_nested():
                await insert_reference(db, local, **overrides)
    await core.record(db, corpus, "red")
    newer = (await memory.history(db, corpus))[-1]
    with pytest.raises(IntegrityError, match="foreign key constraint"):
        async with db.begin_nested():
            await insert_reference(db, local, claim_id=newer["claims"][0]["id"])
    assert await memory.get_evidence(db, foreign, local["id"]) is None
    assert await memory.evidence(db, foreign, claim_id=local["claim_id"]) == []


@pytest.mark.parametrize(
    "quotes",
    [
        [],
        ["first", ""],
        ["first", " "],
        ["first", 1],
        ["first", None],
        ["first", ["second"]],
        ["first", "first"],
        ["first", "missing"],
        ["first", "SECOND"],
        1,
        {"quote": "first"},
    ],
)
@pytest.mark.asyncio
async def test_list_validation_before_any_write(quotes):
    db = AsyncMock()
    with pytest.raises(memory.ClaimValidationError):
        await record_multiple(db, "corpus", metadata=authored(quotes))
    db.execute.assert_not_called()
    db.commit.assert_not_called()
    db.rollback.assert_not_called()


@pytest.mark.asyncio
async def test_each_quote_chunk_must_support_claim():
    db = AsyncMock()
    with pytest.raises(memory.ClaimValidationError, match="text must appear"):
        await record_multiple(
            db, "corpus", chunks=[supporting_chunks()[1], {"text": "second", "order_index": 9}]
        )
    db.execute.assert_not_called()


def test_list_resolution_is_deterministic_and_duplicates_are_rejected():
    metadata, archived, claims = memory._prepare(authored(["second", "first"]), supporting_chunks())
    assert metadata == authored(["second", "first"])
    assert [c["order_index"] for c in archived] == [2, 9]
    assert claims[0]["evidence"] == [
        {"quote": "first", "order_index": 2, "start_offset": 15, "end_offset": 20},
        {"quote": "second", "order_index": 9, "start_offset": 17, "end_offset": 23},
    ]
    assert memory._prepare(authored(), list(reversed(supporting_chunks())))[2] == claims
    with pytest.raises(ValueError, match="distinct quotes"):
        memory._prepare(authored(["first", "first"]), supporting_chunks())
    # Preserve the existing first-matching-chunk rule, even if a later chunk supports it.
    with pytest.raises(ValueError, match="text must appear"):
        memory._prepare(authored(), [{"text": "first", "order_index": 0}, *supporting_chunks()])


@pytest.mark.asyncio
async def test_single_string_prepared_shape_and_ids_unchanged(multiple_db):
    db, corpus, _, _ = multiple_db
    metadata, archived, claims = memory._prepare(core.payload(), core.chunks())
    assert claims == [
        {
            "key": "color",
            "value": "blue",
            "claim": "Color is blue",
            "valid_from": None,
            "valid_until": None,
            "quote": "blue",
            "order_index": 0,
            "start_offset": 9,
            "end_offset": 13,
        }
    ]
    version = await core.record(db, corpus)
    fingerprint = memory._hash("---\ntitle: Color\n---\nColor is blue.", metadata)
    identity = memory.memory_document_id(corpus, "note.md")
    vid = memory._hash("version", identity, None, "NEW", fingerprint)
    cid = memory._hash("claim", vid, "color")
    row = (await memory.evidence(db, corpus))[0]
    assert version["fingerprint"] == fingerprint and version["id"] == vid
    assert row["claim_id"] == cid and row["id"] == memory._hash("evidence", cid)
    assert row["chunk_id"] == memory._hash("chunk", vid, archived[0])
    # Shorthand string claims still use the legacy evidence ID path, too.
    shorthand = await core.record(db, corpus, path="shorthand.md", metadata={"claims": ["blue"]})
    row = (await memory.evidence(db, corpus, version_id=shorthand["id"]))[0]
    assert row["id"] == memory._hash("evidence", row["claim_id"])


@pytest.mark.asyncio
async def test_authored_references_public_projection_and_snapshot_no_drift(multiple_db):
    db, corpus, _, _ = multiple_db
    first = await record_multiple(db, corpus)
    await db.execute(text("SET CONSTRAINTS ALL IMMEDIATE"))
    await db.execute(text("SET CONSTRAINTS ALL DEFERRED"))
    rows = await memory.evidence(db, corpus)
    assert len(rows) == 2 and len({r["id"] for r in rows}) == 2
    assert len({r["chunk_id"] for r in rows}) == 2
    assert {r["quote"] for r in rows} == {"first", "second"}
    for row in rows:
        assert row["chunk"]["text"][row["start_offset"] : row["end_offset"]] == row["quote"]
        assert "Color is blue" in row["chunk"]["text"]
    versions = await memory.history(db, corpus)
    response = memory_public.project(
        versions,
        MemoryRequest(corpus="test"),
        "current",
        State(as_of=first["observed_at"]),
    )
    assert len(response.current_memories) == 1
    assert response.current_memories[0].evidence_ids == sorted(r["id"] for r in rows)
    assert len(response.evidence) == 2
    saved = await memory.snapshot(db, corpus, first["observed_at"])
    expected = await memory.replay_snapshot(db, corpus, saved["id"])
    # Even a valid direct SQL late insert cannot change an existing snapshot.
    with pytest.raises(IntegrityError, match="snapshotted version"):
        async with db.begin_nested():
            await insert_reference(db, rows[0], chunk_id=versions[0]["chunks"][0]["id"])
    skipped = await record_multiple(db, corpus, chunks=list(reversed(supporting_chunks())))
    assert skipped["skipped"] and skipped["id"] == first["id"]
    await record_multiple(db, corpus, metadata=authored(["first"]))
    await memory.record_deletion(db, corpus, "multi.md")
    assert await memory.snapshot(db, corpus, first["observed_at"]) == saved
    assert await memory.replay_snapshot(db, corpus, saved["id"]) == expected
    assert len(expected["evidence"]) == 2


@pytest.mark.asyncio
async def test_multiple_evidence_rolls_back_atomically_and_retries_stably(multiple_db):
    db, corpus, _, _ = multiple_db
    async with db.begin_nested() as transaction:
        first = await record_multiple(db, corpus)
        rows = await memory.evidence(db, corpus)
        assert len(rows) == 2
        await db.execute(text("SET CONSTRAINTS ALL IMMEDIATE"))
        await transaction.rollback()
    assert await memory.history(db, corpus) == []
    assert await memory.evidence(db, corpus) == []
    retry = await record_multiple(db, corpus)
    assert retry["id"] == first["id"]
    assert [r["id"] for r in await memory.evidence(db, corpus)] == [r["id"] for r in rows]
    with pytest.raises(memory.ClaimValidationError):
        async with db.begin_nested():
            await db.execute(
                text("INSERT INTO documents VALUES ('bad-live', :c, 'bad.md')"), {"c": corpus}
            )
            await record_multiple(db, corpus, path="bad.md", metadata=authored(["first", "absent"]))
    assert not (await db.execute(text("SELECT 1 FROM documents WHERE id = 'bad-live'"))).first()
    assert len(await memory.history(db, corpus)) == 1


@pytest.mark.asyncio
async def test_snapshot_created_under_004_remains_frozen_after_upgrade(memory_db):
    db, corpus, _, _ = memory_db
    await core.record(db, corpus)
    saved = await memory.snapshot(db, corpus)
    expected = await memory.replay_snapshot(db, corpus, saved["id"])
    await db.execute(text("SET CONSTRAINTS ALL IMMEDIATE"))
    await migrate(db, "upgrade")
    first = (await memory.evidence(db, corpus))[0]
    with pytest.raises(IntegrityError, match="snapshotted version"):
        async with db.begin_nested():
            await insert_reference(db, first)
    assert await memory.replay_snapshot(db, corpus, saved["id"]) == expected
    assert await memory.snapshot(db, corpus, saved["as_of"]) == saved


@pytest.mark.asyncio
async def test_second_reference_insert_failure_rolls_back_whole_version(multiple_db, monkeypatch):
    db, corpus, _, _ = multiple_db
    execute = db.execute
    inserted = 0

    async def fail_second(statement, *args, **kwargs):
        nonlocal inserted
        if "INSERT INTO memory_evidence" in str(statement):
            inserted += 1
            if inserted == 2:
                raise RuntimeError("second reference failed")
        return await execute(statement, *args, **kwargs)

    with monkeypatch.context() as patch:
        patch.setattr(db, "execute", fail_second)
        with pytest.raises(RuntimeError, match="second reference failed"):
            async with db.begin_nested():
                await db.execute(
                    text("INSERT INTO documents VALUES ('pending-live', :c, 'multi.md')"),
                    {"c": corpus},
                )
                await record_multiple(db, corpus)
    assert inserted == 2
    assert await memory.history(db, corpus) == []
    assert await memory.evidence(db, corpus) == []
    assert not (await db.execute(text("SELECT 1 FROM documents WHERE id = 'pending-live'"))).first()
    await record_multiple(db, corpus)
    assert len(await memory.evidence(db, corpus)) == 2
