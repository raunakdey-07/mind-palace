"""Real PostgreSQL ingestion slice, isolated in a rolled-back test schema.

Embeddings are deterministic test doubles; parsing, ingestion, migrations,
transactions, live SQL retrieval and memory/evidence persistence are real.
"""

import hashlib
import importlib.util
import os
from contextlib import asynccontextmanager
from pathlib import Path
from uuid import uuid4

import pytest
from alembic.migration import MigrationContext
from alembic.operations import Operations
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine
from sqlalchemy.pool import NullPool

from api.services import ingestion, memory
from api.services.rehydrate import rehydrate_corpus
from api.services.retrieval import RetrievalService


def source(value):
    return f"""---
title: Storage Architecture
claims:
  - key: architecture.database
    value: {value}
    claim: The primary database is {value}.
    evidence: The primary database is {value}.
---
# Storage

The primary database is {value}.
"""


class FixedEmbedder:
    model_name = "test-only"
    dimension = 384

    def embed(self, texts):
        return [[1.0] + [0.0] * 383 for _ in texts]


@pytest.fixture
async def memory_ingestion_db(monkeypatch):
    url = os.getenv("MEMORY_TEST_DATABASE_URL") or os.getenv("DATABASE_URL")
    if not url:
        pytest.skip("requires MEMORY_TEST_DATABASE_URL or DATABASE_URL")
    url = (
        url.replace("postgresql+psycopg://", "postgresql+asyncpg://")
        .replace("postgresql+psycopg2://", "postgresql+asyncpg://")
        .replace("postgresql://", "postgresql+asyncpg://")
    )
    engine = create_async_engine(url, poolclass=NullPool)
    schema = "ingestion_memory_" + uuid4().hex
    corpus = hashlib.sha256(uuid4().bytes).hexdigest()
    try:
        async with engine.connect() as conn:
            outer = await conn.begin()
            try:
                await conn.execute(text(f'CREATE SCHEMA "{schema}"'))
                await conn.execute(text(f'SET LOCAL search_path TO "{schema}", public'))

                def migrate(sync_conn):
                    for path in sorted(
                        (Path(__file__).parents[1] / "migrations/versions").glob("*.py")
                    ):
                        spec = importlib.util.spec_from_file_location(path.stem, path)
                        module = importlib.util.module_from_spec(spec)
                        spec.loader.exec_module(module)
                        with Operations.context(MigrationContext.configure(sync_conn)):
                            module.upgrade()

                await conn.run_sync(migrate)
                await conn.execute(
                    text("INSERT INTO corpora(id, name) VALUES (:id, :name)"),
                    {"id": corpus, "name": schema},
                )

                @asynccontextmanager
                async def sessions():
                    async with AsyncSession(
                        bind=conn, join_transaction_mode="create_savepoint", expire_on_commit=False
                    ) as db:
                        yield db

                monkeypatch.setattr(ingestion, "session_scope", sessions)
                monkeypatch.setattr(ingestion, "Embedder", FixedEmbedder)
                yield sessions, corpus
            finally:
                await outer.rollback()
    finally:
        await engine.dispose()


@pytest.mark.asyncio
async def test_ingestion_a_to_b_preserves_history_and_replaces_live_chunks(memory_ingestion_db):
    sessions, corpus = memory_ingestion_db
    service = ingestion.IngestionService(memory_enabled=True)
    first = await service.ingest_file(source("Redis"), "architecture.md", corpus)
    assert first["success"] and first["chunk_count"] == 1
    async with sessions() as db:
        versions_a = await memory.history(db, corpus, "architecture.md")
    assert len(versions_a) == 1
    first_version = versions_a[0]

    second = await service.ingest_file(source("PostgreSQL"), "architecture.md", corpus)
    assert second["success"] and second["document_id"] == first["document_id"]
    async with sessions() as db:
        live = await RetrievalService(db).search(
            FixedEmbedder().embed(["database"])[0], corpus_id=corpus, k=10
        )
        assert [r.text for r in live] == ["The primary database is PostgreSQL."]
        versions = await memory.history(db, corpus, "architecture.md")
        current = await memory.query(db, corpus)
        past = await memory.query(db, corpus, as_of=first_version["observed_at"])
        evidence = await memory.evidence(db, corpus)

    assert [v["event"] for v in versions] == ["NEW", "MODIFIED"]
    assert [v["content"] for v in versions] == [source("Redis"), source("PostgreSQL")]
    assert versions[1]["predecessor_id"] == versions[0]["id"]
    assert [c["value"] for c in current["current_memories"]] == ["PostgreSQL"]
    assert [c["value"] for c in current["historical_memories"]] == ["Redis"]
    assert [c["value"] for c in past["current_memories"]] == ["Redis"]
    assert past["historical_memories"] == []
    old = versions[0]["claims"][0]
    new = versions[1]["claims"][0]
    assert new["supersedes_id"] == old["id"]
    assert [c["id"] for c in current["superseded_memories"]] == [old["id"]]
    assert len(evidence) == 2
    for version, claim in ((versions[0], old), (versions[1], new)):
        proof = next(e for e in evidence if e["claim_id"] == claim["id"])
        assert proof["version_id"] == version["id"]
        assert proof["chunk_id"] == version["chunks"][0]["id"]
        assert proof["quote"] == claim["claim"]
        assert proof["quote"] in proof["chunk"]["text"]

    unchanged = await service.ingest_file(source("PostgreSQL"), "architecture.md", corpus)
    assert unchanged["chunk_count"] == 0
    async with sessions() as db:
        assert len(await memory.history(db, corpus, "architecture.md")) == 2


@pytest.mark.asyncio
async def test_memory_sync_deletion_restore_and_metadata_only(memory_ingestion_db, tmp_path):
    sessions, corpus = memory_ingestion_db
    service = ingestion.IngestionService(memory_enabled=True)
    path = tmp_path / "architecture.md"
    path.write_text(source("Redis"))
    assert (await service.sync_repo(str(tmp_path), corpus))["added"] == 1
    assert (await service.sync_repo(str(tmp_path), corpus))["unchanged"] == 1
    path.write_text(source("Redis").replace("Storage Architecture", "Storage Decision"))
    assert (await service.sync_repo(str(tmp_path), corpus))["changed"] == 1
    path.unlink()
    assert (await service.sync_repo(str(tmp_path), corpus))["deleted"] == 1
    assert (await service.sync_repo(str(tmp_path), corpus))["deleted"] == 0
    async with sessions() as db:
        assert not (await memory.query(db, corpus))["current_memories"]
        assert len(await memory.evidence(db, corpus)) == 2
        assert not await RetrievalService(db).search(
            FixedEmbedder().embed(["database"])[0], corpus_id=corpus
        )
    path.write_text(source("PostgreSQL"))
    assert (await service.sync_repo(str(tmp_path), corpus))["added"] == 1
    async with sessions() as db:
        versions = await memory.history(db, corpus)
        assert [v["event"] for v in versions] == ["NEW", "MODIFIED", "DELETED", "RESTORED"]
        assert len({v["memory_document_id"] for v in versions}) == 1
        assert [c["value"] for c in (await memory.query(db, corpus))["current_memories"]] == [
            "PostgreSQL"
        ]


@pytest.mark.asyncio
async def test_rehydrate_rebuilds_live_projection_without_new_archive_version(
    memory_ingestion_db, monkeypatch
):
    sessions, corpus = memory_ingestion_db
    service = ingestion.IngestionService(memory_enabled=True)
    first = await service.ingest_file(source("Redis"), "architecture.md", corpus)

    async with sessions() as db:
        await db.execute(text("DELETE FROM ingestion_manifest WHERE corpus_id = :c"), {"c": corpus})
        await db.execute(text("DELETE FROM documents WHERE corpus_id = :c"), {"c": corpus})
        await db.commit()

    async def fail_record(*args, **kwargs):
        raise AssertionError("rehydration must not append an archive version")

    monkeypatch.setattr(memory, "record_version", fail_record)
    async with sessions() as db, db.begin():
        result = await rehydrate_corpus(db, corpus, embedder=FixedEmbedder())

    assert result["rebuilt_documents"] == 1
    assert result["rebuilt_chunks"] == 1
    async with sessions() as db:
        document = (
            await db.execute(
                text("SELECT id FROM documents WHERE corpus_id = :c AND path = :p"),
                {"c": corpus, "p": "architecture.md"},
            )
        ).scalar_one()
        assert document == first["document_id"]
        live = await RetrievalService(db).search(
            FixedEmbedder().embed(["database"])[0], corpus_id=corpus
        )
        assert [row.text for row in live] == ["The primary database is Redis."]
        assert len(await memory.history(db, corpus)) == 1


async def test_rehydrate_removes_deleted_paths_but_keeps_live_only_paths(memory_ingestion_db):
    sessions, corpus = memory_ingestion_db
    service = ingestion.IngestionService(memory_enabled=True)
    await service.ingest_file(source("Redis"), "architecture.md", corpus)
    await service.ingest_file(source("PostgreSQL"), "other.md", corpus)
    async with sessions() as db, db.begin():
        await memory.record_deletion(db, corpus, "architecture.md")

    async with sessions() as db, db.begin():
        result = await rehydrate_corpus(db, corpus, embedder=FixedEmbedder())

    assert result["removed_documents"] == 1
    async with sessions() as db:
        assert not (
            await db.execute(
                text("SELECT 1 FROM documents WHERE corpus_id = :c AND path = :p"),
                {"c": corpus, "p": "architecture.md"},
            )
        ).first()
        assert (
            await db.execute(
                text("SELECT 1 FROM documents WHERE corpus_id = :c AND path = :p"),
                {"c": corpus, "p": "other.md"},
            )
        ).first()


async def test_failed_memory_write_rolls_back_live_chunks_manifest_and_archive(
    memory_ingestion_db, monkeypatch
):
    sessions, corpus = memory_ingestion_db
    service = ingestion.IngestionService(memory_enabled=True)
    first = await service.ingest_file(source("Redis"), "architecture.md", corpus)
    original_record = memory.record_version

    async def fail_after_archive(*args, **kwargs):
        await original_record(*args, **kwargs)
        raise RuntimeError("injected failure after archive insertion")

    monkeypatch.setattr(memory, "record_version", fail_after_archive)
    with pytest.raises(RuntimeError, match="injected failure"):
        await service.ingest_file(source("PostgreSQL"), "architecture.md", corpus)
    async with sessions() as db:
        assert len(await memory.history(db, corpus)) == 1
        assert len(await memory.evidence(db, corpus)) == 1
        live = await RetrievalService(db).search(FixedEmbedder().embed([""])[0], corpus_id=corpus)
        assert [r.text for r in live] == ["The primary database is Redis."]
        manifest = (
            await db.execute(
                text("SELECT content_hash FROM ingestion_manifest WHERE doc_id = :d"),
                {"d": first["document_id"]},
            )
        ).scalar_one()
        assert manifest == hashlib.sha256(source("Redis").encode()).hexdigest()
    monkeypatch.setattr(memory, "record_version", original_record)
    assert (await service.ingest_file(source("PostgreSQL"), "architecture.md", corpus))["success"]
    async with sessions() as db:
        assert len(await memory.history(db, corpus)) == 2


@pytest.mark.asyncio
async def test_invalid_evidence_does_not_replace_current_version(memory_ingestion_db):
    sessions, corpus = memory_ingestion_db
    service = ingestion.IngestionService(memory_enabled=True)
    await service.ingest_file(source("Redis"), "architecture.md", corpus)
    invalid = source("PostgreSQL").replace(
        "evidence: The primary database is PostgreSQL.", "evidence: invented evidence"
    )
    with pytest.raises(ValueError, match="exact substring"):
        await service.ingest_file(invalid, "architecture.md", corpus)
    async with sessions() as db:
        assert len(await memory.history(db, corpus)) == 1
        assert [c["value"] for c in (await memory.query(db, corpus))["current_memories"]] == [
            "Redis"
        ]


@pytest.mark.asyncio
async def test_string_claim_a_b_delete_restore_without_memory_flag(memory_ingestion_db, tmp_path):
    sessions, corpus = memory_ingestion_db
    service = ingestion.IngestionService()
    path = tmp_path / "architecture.md"

    def document(value):
        return (
            f'---\ntitle: Architecture\nclaims:\n  - "The application uses {value}."\n'
            f"---\n# Architecture\n\nThe application uses {value}."
        )

    path.write_text(document("Redis Streams"))
    assert (await service.sync_repo(str(tmp_path), corpus))["added"] == 1
    async with sessions() as db:
        first = (await memory.history(db, corpus))[0]
        assert (await memory.query(db, corpus))["current_memories"][0]["status"] == "CURRENT"
    path.write_text(document("Kafka"))
    assert (await service.sync_repo(str(tmp_path), corpus))["changed"] == 1
    async with sessions() as db:
        state = await memory.query(db, corpus)
        assert state["current_memories"][0]["claim"] == "The application uses Kafka."
        assert state["current_memories"][0]["supersession_basis"] == "single_claim_replacement"
        assert state["superseded_memories"][0]["status"] == "SUPERSEDED"
        assert (await memory.query(db, corpus, as_of=first["observed_at"]))["current_memories"][0][
            "claim"
        ] == "The application uses Redis Streams."
    path.unlink()
    assert (await service.sync_repo(str(tmp_path), corpus))["deleted"] == 1
    async with sessions() as db:
        assert not (await memory.query(db, corpus))["current_memories"]
        assert len(await memory.evidence(db, corpus)) == 2
    path.write_text(document("Kafka"))
    assert (await service.sync_repo(str(tmp_path), corpus))["added"] == 1
    async with sessions() as db:
        assert [v["event"] for v in await memory.history(db, corpus)] == [
            "NEW",
            "MODIFIED",
            "DELETED",
            "RESTORED",
        ]
    path.write_text("# Architecture\n\nNo claims now.")
    assert (await service.sync_repo(str(tmp_path), corpus))["changed"] == 1
    async with sessions() as db:
        assert len(await memory.history(db, corpus)) == 5
        assert not (await memory.query(db, corpus))["current_memories"]
