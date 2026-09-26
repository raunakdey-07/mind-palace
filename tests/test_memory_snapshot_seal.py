"""Durable snapshot-membership seal contracts in isolated PostgreSQL schemas."""

from __future__ import annotations

import importlib.util
from pathlib import Path

import pytest
from alembic.config import Config
from alembic.migration import MigrationContext
from alembic.operations import Operations
from alembic.script import ScriptDirectory
from sqlalchemy import text
from sqlalchemy.exc import IntegrityError

from api.services import memory

try:
    from tests import test_memory_core as core
except ModuleNotFoundError as exc:
    if exc.name not in {"tests", "tests.test_memory_core"}:
        raise
    import test_memory_core as core


ROOT = Path(__file__).parents[1]
memory_db = core.memory_db


async def migrate(db, name: str, direction: str = "upgrade") -> None:
    path = ROOT / "migrations" / "versions" / name
    spec = importlib.util.spec_from_file_location(path.stem, path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)

    def apply(conn):
        with Operations.context(MigrationContext.configure(conn)):
            getattr(module, direction)()

    await (await db.connection()).run_sync(apply)


@pytest.fixture
async def pre_seal_db(memory_db):
    await migrate(db=memory_db[0], name="005_multiple_evidence.py")
    return memory_db


@pytest.fixture
async def sealed_db(memory_db):
    await migrate(db=memory_db[0], name="005_multiple_evidence.py")
    await migrate(db=memory_db[0], name="006_snapshot_membership_seal.py")
    return memory_db


async def seal_count(db, corpus: str, snapshot_id: str) -> int:
    return (
        await db.execute(
            text("""
            SELECT count(*) FROM memory_snapshot_seals
            WHERE corpus_id = :c AND snapshot_id = :s
        """),
            {"c": corpus, "s": snapshot_id},
        )
    ).scalar_one()


async def insert_membership(db, corpus: str, snapshot_id: str, version_id: str) -> None:
    await db.execute(
        text("""
        INSERT INTO memory_snapshot_versions(corpus_id, snapshot_id, version_id)
        VALUES (:c, :s, :v)
    """),
        {"c": corpus, "s": snapshot_id, "v": version_id},
    )


def test_snapshot_seal_is_applied_but_a_later_migration_may_be_head():
    """The seal is still on the chain; it need not be the newest revision.

    007 adds the claim embedding cache, so pinning head to the seal would make
    this test fail for a correct change. The chain position is what matters.
    """
    script = ScriptDirectory.from_config(Config(str(ROOT / "migrations" / "alembic.ini")))
    head = script.get_current_head()
    assert head == "007_claim_embedding_cache"
    revisions = {r.revision for r in script.walk_revisions("base", head)}
    assert {"004_memory", "005_multiple_evidence", "006_snapshot_membership_seal"} <= revisions


async def test_service_seals_snapshot_and_rejects_late_same_corpus_membership(sealed_db):
    db, corpus, _, _ = sealed_db
    first = await core.record(db, corpus)
    saved = await memory.snapshot(db, corpus, first["observed_at"])
    expected = await memory.replay_snapshot(db, corpus, saved["id"])

    assert saved["version_ids"] == [first["id"]]
    assert await seal_count(db, corpus, saved["id"]) == 1
    assert await memory.snapshot(db, corpus, first["observed_at"]) == saved
    assert await seal_count(db, corpus, saved["id"]) == 1

    with pytest.raises(IntegrityError, match="append-only"):
        async with db.begin_nested():
            await db.execute(
                text("""
                DELETE FROM memory_snapshot_seals
                WHERE corpus_id = :c AND snapshot_id = :s
            """),
                {"c": corpus, "s": saved["id"]},
            )

    later = await core.record(db, corpus, "red", path="later.md")
    with pytest.raises(IntegrityError, match="sealed snapshot"):
        async with db.begin_nested():
            await insert_membership(db, corpus, saved["id"], later["id"])

    assert await memory.get_snapshot(db, corpus, saved["id"]) == saved
    assert await memory.replay_snapshot(db, corpus, saved["id"]) == expected


async def test_migration_backfills_existing_snapshot_and_preserves_replay(pre_seal_db):
    db, corpus, _, _ = pre_seal_db
    first = await core.record(db, corpus)
    saved = await memory.snapshot(db, corpus, first["observed_at"])
    expected = await memory.replay_snapshot(db, corpus, saved["id"])
    assert not (await db.execute(text("""
            SELECT to_regclass(format('%I.memory_snapshot_seals', current_schema())) IS NOT NULL
        """))).scalar_one()

    await migrate(db=db, name="006_snapshot_membership_seal.py")

    assert await seal_count(db, corpus, saved["id"]) == 1
    assert await memory.get_snapshot(db, corpus, saved["id"]) == saved
    later = await core.record(db, corpus, "red", path="later.md")
    with pytest.raises(IntegrityError, match="sealed snapshot"):
        async with db.begin_nested():
            await insert_membership(db, corpus, saved["id"], later["id"])
    assert await memory.replay_snapshot(db, corpus, saved["id"]) == expected


async def test_unsealed_snapshot_cannot_commit(sealed_db):
    db, corpus, _, _ = sealed_db
    observed = (await db.execute(text("SELECT clock_timestamp()"))).scalar_one()
    snapshot_id = memory._hash("snapshot", corpus, observed.isoformat())
    await db.execute(text("SET CONSTRAINTS ALL DEFERRED"))

    with pytest.raises(IntegrityError, match="snapshot requires a seal"):
        async with db.begin_nested():
            await db.execute(
                text("""
                INSERT INTO memory_snapshots(corpus_id, id, observed_at, as_of)
                VALUES (:c, :id, :observed, :at)
            """),
                {"c": corpus, "id": snapshot_id, "observed": observed, "at": observed},
            )
            await db.execute(text("SET CONSTRAINTS ALL IMMEDIATE"))

    assert await memory.get_snapshot(db, corpus, snapshot_id) is None


async def test_downgrade_preserves_snapshot_and_reupgrade_backfills_seal(sealed_db):
    db, corpus, _, _ = sealed_db
    first = await core.record(db, corpus)
    saved = await memory.snapshot(db, corpus, first["observed_at"])

    await migrate(db=db, name="006_snapshot_membership_seal.py", direction="downgrade")

    assert not (await db.execute(text("""
            SELECT to_regclass(format('%I.memory_snapshot_seals', current_schema())) IS NOT NULL
        """))).scalar_one()
    assert await memory.get_snapshot(db, corpus, saved["id"]) == saved

    await migrate(db=db, name="006_snapshot_membership_seal.py")

    assert await seal_count(db, corpus, saved["id"]) == 1
    assert await memory.get_snapshot(db, corpus, saved["id"]) == saved
