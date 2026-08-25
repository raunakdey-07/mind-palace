"""Ingestion lifecycle tests.

Covers the full pipeline: parse -> hash -> upsert -> chunk -> embed ->
insert -> manifest, against a live PostgreSQL+pgvector database.

These tests require DATABASE_URL to point at a reachable pgvector database
with migrations applied. They are skipped automatically otherwise, keeping
the ordinary unit suite DB-independent.
"""

from __future__ import annotations

import os

import pytest
import sqlalchemy as sa

from api.services.parser import parse_markdown
from api.services.repository import content_hash, deterministic_doc_id

pytestmark = pytest.mark.asyncio


def _sync_url() -> str:
    """Normalize DATABASE_URL to a sync driver available in this env.

    Prefers psycopg2 (repo dependency); falls back to raw postgresql:// which
    SQLAlchemy maps to psycopg2 as well.
    """
    url = os.getenv("DATABASE_URL", "")
    return (
        url.replace("postgresql+psycopg://", "postgresql+psycopg2://")
        .replace("postgresql+psycopg2://", "postgresql+psycopg2://")
        .replace("postgresql+asyncpg://", "postgresql+psycopg2://")
        .replace("postgresql://", "postgresql+psycopg2://")
    )


def _db_available() -> bool:
    if not os.getenv("DATABASE_URL"):
        return False
    try:
        engine = sa.create_engine(_sync_url())
        with engine.connect():
            engine.dispose()
        return True
    except Exception:
        return False


requires_db = pytest.mark.skipif(not _db_available(), reason="no reachable DATABASE_URL")


@pytest.fixture(autouse=True)
def _clean_test_paths():
    """Remove test documents from DB before/after each test.

    Prevents manifest/document state from previous runs short-circuiting
    ingestion (the skip-on-unchanged path would otherwise report 0 chunks).
    """
    from sqlalchemy import create_engine
    from sqlalchemy import text as sa_text

    if not _db_available():
        yield
        return

    def cleanup():
        engine = create_engine(_sync_url())
        with engine.begin() as conn:
            conn.execute(
                sa_text(
                    "DELETE FROM chunks WHERE doc_id IN "
                    "(SELECT id FROM documents WHERE path LIKE 'test/%')"
                )
            )
            conn.execute(sa_text("DELETE FROM ingestion_manifest WHERE path LIKE 'test/%'"))
            conn.execute(sa_text("DELETE FROM documents WHERE path LIKE 'test/%'"))
        engine.dispose()

    cleanup()
    yield
    cleanup()


@pytest.fixture(autouse=True)
def _fresh_engine_per_test():
    """Dispose the shared async engine before/after each test.

    asyncpg connections are bound to the event loop that created them;
    pytest-asyncio gives each test a fresh loop, so pooled connections from
    a previous test's loop cause 'another operation is in progress' errors.
    """
    import asyncio

    from api.services.db import async_engine

    # dispose any connections left over from a previous loop
    try:
        asyncio.run(async_engine.dispose())
    except Exception:
        pass
    yield
    try:
        asyncio.run(async_engine.dispose())
    except Exception:
        pass


DOC_V1 = """---
title: "Lifecycle Test Doc"
date: 2024-05-01
tags: ["lifecycle", "test"]
document_type: "project"
summary: "First version"
---

# Intro

Original introduction content about alpha.

# Details

Deep technical details about beta.
"""

DOC_V2 = """---
title: "Lifecycle Test Doc"
date: 2024-05-01
tags: ["lifecycle", "test"]
document_type: "project"
summary: "Second version"
---

# Intro

Rewritten introduction mentioning gamma.

# Details

Updated technical details about delta.

# Extra

Brand new section about epsilon.
"""


async def _get_service():
    from api.services.ingestion import IngestionService

    return IngestionService()


async def _counts(db, doc_id: str | None = None):
    from api.services.db import session_scope

    async with session_scope() as db:
        if doc_id:
            chunks = await db.execute(
                sa.text("SELECT count(*) FROM chunks WHERE doc_id = :d"), {"d": doc_id}
            )
            return chunks.scalar_one()
        docs = await db.execute(sa.text("SELECT count(*) FROM documents"))
        return docs.scalar_one()


async def _fetch_doc_by_path(db, path: str):
    row = await db.execute(
        sa.text("SELECT id, title, document_type, tags FROM documents WHERE path = :p"),
        {"p": path},
    )
    return row.first()


async def _manifest_entry(db, path: str):
    row = await db.execute(
        sa.text(
            "SELECT path, content_hash, doc_id, chunk_count FROM ingestion_manifest WHERE path = :p"
        ),
        {"p": path},
    )
    return row.first()


@requires_db
async def test_new_document_fully_indexed():
    from api.services.db import session_scope

    svc = await _get_service()
    async with session_scope() as db:
        result = await svc._ingest_content(db, DOC_V1, "test/lifecycle.md")

    assert result["success"] is True
    assert result["chunk_count"] == 2  # two H1 sections

    async with session_scope() as db:
        doc = await _fetch_doc_by_path(db, "test/lifecycle.md")
        assert doc is not None
        assert doc.title == "Lifecycle Test Doc"
        assert doc.document_type == "project"
        assert set(doc.tags) >= {"lifecycle", "test"}

        manifest = await _manifest_entry(db, "test/lifecycle.md")
        assert manifest is not None
        assert manifest.chunk_count == 2
        assert manifest.doc_id == doc.id


@requires_db
async def test_unchanged_document_skipped_without_duplicates():
    from api.services.db import session_scope

    svc = await _get_service()
    async with session_scope() as db:
        first = await svc._ingest_content(db, DOC_V1, "test/unchanged.md")
    async with session_scope() as db:
        second = await svc._ingest_content(db, DOC_V1, "test/unchanged.md")

    assert first["success"] and second["success"]
    assert second["chunk_count"] == 0
    assert "unchanged" in second["message"].lower()

    async with session_scope() as db:
        rows = await db.execute(
            sa.text("SELECT count(*) FROM documents WHERE path = 'test/unchanged.md'")
        )
        assert rows.scalar_one() == 1  # no duplicate documents


@requires_db
async def test_changed_document_updates_and_removes_stale_chunks():
    from api.services.db import session_scope

    svc = await _get_service()
    async with session_scope() as db:
        v1 = await svc._ingest_content(db, DOC_V1, "test/changed.md")
    async with session_scope() as db:
        v2 = await svc._ingest_content(db, DOC_V2, "test/changed.md")

    assert v1["chunk_count"] == 2
    # V2 adds a section -> 3 chunks; same path so the document row keeps its id
    assert v2["chunk_count"] == 3
    assert v2["document_id"] == v1["document_id"]

    async with session_scope() as db:
        doc = await _fetch_doc_by_path(db, "test/changed.md")
        assert doc.id == v1["document_id"]

        fresh = await db.execute(
            sa.text("SELECT count(*) FROM chunks WHERE doc_id = :d"),
            {"d": v1["document_id"]},
        )
        assert fresh.scalar_one() == 3  # exactly the new chunk set, no stale rows

        manifest = await _manifest_entry(db, "test/changed.md")
        assert manifest.doc_id == v1["document_id"]
        assert manifest.chunk_count == 3


@requires_db
async def test_metadata_only_change_is_detected():
    """Changing frontmatter changes the body? No -- body identical means skip.

    Documents the current behavior: metadata-only edits do NOT retrigger
    ingestion because change detection hashes only the body. This is a
    known limitation captured deliberately as a test.
    """
    from api.services.db import session_scope

    meta_a = DOC_V1.replace('summary: "First version"', 'summary: "Changed summary"')
    assert meta_a != DOC_V1

    svc = await _get_service()
    async with session_scope() as db:
        r1 = await svc._ingest_content(db, DOC_V1, "test/metaonly.md")
    async with session_scope() as db:
        r2 = await svc._ingest_content(db, meta_a, "test/metaonly.md")

    assert r1["chunk_count"] > 0
    # body unchanged -> manifest hit -> skipped even though summary changed
    assert r2["chunk_count"] == 0


@requires_db
async def test_empty_document_is_skipped_gracefully():
    from api.services.db import session_scope

    svc = await _get_service()
    for name, content in [
        ("test/empty.md", ""),
        ("test/blank_frontmatter.md", "---\ntitle: X\ndate: 2024-01-01\n---\n\n   \n"),
    ]:
        async with session_scope() as db:
            result = await svc._ingest_content(db, content, name)
        assert result["success"] is True
        assert result["chunk_count"] == 0


@requires_db
async def test_malformed_document_does_not_corrupt_state():
    """A file failing mid-pipeline must not leave a manifest entry claiming success."""
    from api.services.db import session_scope

    svc = await _get_service()

    # Ingest a good version first
    async with session_scope() as db:
        good = await svc._ingest_content(db, DOC_V1, "test/malformed.md")
    assert good["success"]

    # Now force a failure after upsert by breaking embeddings via monkeypatched embedder
    original_embed = svc.embedder.embed

    def boom(_texts):
        raise RuntimeError("embedding backend down")

    svc.embedder.embed = boom
    try:
        modified = DOC_V1.replace("alpha", "alpha-v2-failure")
        async with session_scope() as db:
            with pytest.raises(RuntimeError):
                await svc._ingest_content(db, modified, "test/malformed.md")
    finally:
        svc.embedder.embed = original_embed

    async with session_scope() as db:
        manifest = await _manifest_entry(db, "test/malformed.md")
        body_hash = content_hash(parse_markdown(modified)[1])
        # Manifest must NOT claim the failed version was ingested
        assert manifest.content_hash != body_hash


@requires_db
async def test_duplicate_paths_deterministic_ids():
    from api.services.db import session_scope

    svc = await _get_service()
    async with session_scope() as db:
        r1 = await svc._ingest_content(db, DOC_V1, "test/dup.md")
    async with session_scope() as db:
        r2 = await svc._ingest_content(db, DOC_V1, "test/dup.md")

    expected_id = deterministic_doc_id("test/dup.md", parse_markdown(DOC_V1)[1])
    assert r1["document_id"] == expected_id
    # second ingest is a no-op; reported id stays consistent when present
    assert r2.get("document_id") in (None, expected_id)


@requires_db
async def test_ingest_repo_reports_failures(tmp_path):
    d = tmp_path / "corpus"
    d.mkdir()
    (d / "good.md").write_text(DOC_V1)
    (d / "broken.md").write_text("---\ntitle: [unclosed\n")  # invalid YAML may still parse;
    # use a genuinely unreadable file instead
    bad = d / "unreadable.md"
    bad.write_text(DOC_V1)
    bad.chmod(0o000)

    try:
        svc = await _get_service()
        result = await svc.ingest_repo(str(d))
    finally:
        bad.chmod(0o644)

    assert result["success"] is False  # one file failed
    assert "failed" in result["message"].lower()
