"""Corpus isolation and synchronization tests.

Proves the product-critical contracts:

- searches never leak across corpora
- ingestion is scoped: the same path in two corpora stays independent
- deletion of a source file removes it from the index on sync
- sync summaries are machine-readable and accurate
- a failed document leaves no false success state
"""

from __future__ import annotations

import os

import pytest
import sqlalchemy as sa

pytestmark = pytest.mark.asyncio

CORPUS_A_NAME = "isolation-a"
CORPUS_B_NAME = "isolation-b"


def _db_available() -> bool:
    if not os.getenv("DATABASE_URL"):
        return False
    try:
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


def _sync_url() -> str:
    return (
        os.getenv("DATABASE_URL", "")
        .replace("postgresql+psycopg://", "postgresql+psycopg2://")
        .replace("postgresql://", "postgresql+psycopg2://")
    )


@pytest.fixture(autouse=True)
def _clean_corpora():
    """Remove test corpora before/after each test."""
    if not _db_available():
        yield
        return

    def cleanup():
        engine = sa.create_engine(_sync_url())
        try:
            with engine.begin() as conn:
                for name in (CORPUS_A_NAME, CORPUS_B_NAME):
                    # Purge ALL manifest rows for this corpus (including
                    # orphans whose document is already gone).
                    conn.execute(
                        sa.text(
                            "DELETE FROM ingestion_manifest WHERE corpus_id IN "
                            "(SELECT id FROM corpora WHERE name = :n)"
                        ),
                        {"n": name},
                    )
                    conn.execute(
                        sa.text(
                            "DELETE FROM chunks WHERE doc_id IN "
                            "(SELECT id FROM documents WHERE corpus_id IN "
                            "(SELECT id FROM corpora WHERE name = :n))"
                        ),
                        {"n": name},
                    )
                    conn.execute(
                        sa.text(
                            "DELETE FROM documents WHERE corpus_id IN "
                            "(SELECT id FROM corpora WHERE name = :n)"
                        ),
                        {"n": name},
                    )
                conn.execute(
                    sa.text("DELETE FROM corpora WHERE name IN (:a, :b)"),
                    {"a": CORPUS_A_NAME, "b": CORPUS_B_NAME},
                )
        finally:
            engine.dispose()

    cleanup()
    yield
    cleanup()


@pytest.fixture(autouse=True)
async def _seed_corpus_ids():
    """Create the two test corpora so ingestion has valid FK targets.

    Async so seeding shares the test's event loop (asyncpg binds
    connections to the creating loop).
    """
    from api.services.corpora import create_corpus
    from api.services.db import session_scope

    if _db_available():
        async with session_scope() as db:
            await create_corpus(db, CORPUS_A_NAME, "test corpus a")
            await create_corpus(db, CORPUS_B_NAME, "test corpus b")
    yield


@pytest.fixture(autouse=True)
def _fresh_engine():
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


DOC_A = """---
title: "Corpus A Doc"
date: 2024-07-01
tags: ["isolation"]
document_type: "note"
summary: "Lives only in corpus A"
---

# Corpus A

Quokka fusion reactors require beryllium shielding.
"""

DOC_B = """---
title: "Corpus B Doc"
date: 2024-07-01
tags: ["isolation"]
document_type: "note"
summary: "Lives only in corpus B"
---

# Corpus B

Marmalade skies dominate the western forecast.
"""


async def _corpus_id(name: str) -> str:
    from api.services.corpora import get_corpus_by_name
    from api.services.db import session_scope

    async with session_scope() as db:
        c = await get_corpus_by_name(db, name)
    assert c, f"test corpus {name} missing"
    return c["id"]


async def _seed(cid: str, content: str, path: str):
    from api.services.db import session_scope
    from api.services.ingestion import IngestionService

    svc = IngestionService()
    async with session_scope() as db:
        return await svc._ingest_content(db, content, path, cid)


@requires_db
async def test_same_path_two_corpora_stays_independent():
    """The same relative path in two corpora must coexist without collision."""
    await _seed(await _corpus_id(CORPUS_A_NAME), DOC_A, "shared/doc.md")
    await _seed(await _corpus_id(CORPUS_B_NAME), DOC_B, "shared/doc.md")

    from api.services.db import session_scope
    from api.services.embedder import Embedder
    from api.services.retrieval import RetrievalService

    emb = Embedder()
    emb.embed_single("warmup")
    async with session_scope() as db:
        svc = RetrievalService(db)

        cid_a = await _corpus_id(CORPUS_A_NAME)
        cid_b = await _corpus_id(CORPUS_B_NAME)
        ra = await svc.search(emb.embed_single("quokka beryllium"), k=10, corpus_id=cid_a)
        rb = await svc.search(emb.embed_single("marmalade skies"), k=10, corpus_id=cid_b)

    assert any(r.source_title == "Corpus A Doc" for r in ra)
    assert all(r.source_title != "Corpus B Doc" for r in ra)
    assert any(r.source_title == "Corpus B Doc" for r in rb)
    assert all(r.source_title != "Corpus A Doc" for r in rb)


@requires_db
async def test_search_never_leaks_across_corpora():
    """A query matching only corpus A content returns nothing from corpus B."""
    await _seed(await _corpus_id(CORPUS_A_NAME), DOC_A, "only_a.md")

    from api.services.db import session_scope
    from api.services.embedder import Embedder
    from api.services.retrieval import RetrievalService

    emb = Embedder()
    emb.embed_single("warmup")
    async with session_scope() as db:
        svc = RetrievalService(db)
        results_b = await svc.search(
            emb.embed_single("quokka fusion beryllium"),
            k=10,
            corpus_id=await _corpus_id(CORPUS_B_NAME),
        )
    assert results_b == []


@requires_db
async def test_sync_deletes_removed_source_files(tmp_path):
    """A file deleted from the source directory disappears from the index."""
    d = tmp_path / "test"
    d.mkdir()
    f1 = d / "keep.md"
    f2 = d / "gone.md"
    f1.write_text(DOC_A.replace("Corpus A Doc", "Sync Keep"))
    f2.write_text(DOC_A.replace("Corpus A Doc", "Sync Gone"))

    from api.services.ingestion import IngestionService

    svc = IngestionService()
    first = await svc.sync_repo(str(d), await _corpus_id(CORPUS_A_NAME))
    assert first["added"] == 2
    assert first["success"]

    f2.unlink()
    second = await svc.sync_repo(str(d), await _corpus_id(CORPUS_A_NAME))

    assert second["deleted"] == 1
    assert second["unchanged"] == 1
    assert second["added"] == 0

    # 'gone' document must be absent from retrieval
    from api.services.db import session_scope
    from api.services.embedder import Embedder
    from api.services.retrieval import RetrievalService

    emb = Embedder()
    emb.embed_single("warmup")
    async with session_scope() as db:
        svc_r = RetrievalService(db)
        titles = [
            r.source_title
            for r in await svc_r.search(
                emb.embed_single("sync keep gone"), k=20, corpus_id=await _corpus_id(CORPUS_A_NAME)
            )
        ]
    assert "Sync Gone" not in titles
    assert "Sync Keep" in titles


@requires_db
async def test_sync_summary_is_machine_readable(tmp_path):
    d = tmp_path / "test"
    d.mkdir()
    (d / "one.md").write_text(DOC_A.replace("Corpus A Doc", "Summary One"))

    from api.services.ingestion import IngestionService

    svc = IngestionService()
    result = await svc.sync_repo(str(d), await _corpus_id(CORPUS_A_NAME))

    for key in ("added", "changed", "unchanged", "deleted", "failed", "duration_ms"):
        assert key in result, f"missing summary key: {key}"
    assert result["added"] == 1
    assert isinstance(result["duration_ms"], int)


@requires_db
async def test_failed_ingestion_leaves_no_success_state(tmp_path):
    """If embedding fails mid-sync, the file must not be marked ingested."""
    d = tmp_path / "test"
    d.mkdir()
    (d / "doomed.md").write_text(DOC_A.replace("Corpus A Doc", "Doomed Doc"))

    from api.services.ingestion import IngestionService

    svc = IngestionService()
    original = svc.embedder.embed

    def boom(_texts):
        raise RuntimeError("embedding backend down")

    svc.embedder.embed = boom
    try:
        result = await svc.sync_repo(str(d), await _corpus_id(CORPUS_A_NAME))
    finally:
        svc.embedder.embed = original

    assert result["failed"] == 1
    assert result["success"] is False

    # manifest must not claim success
    engine = sa.create_engine(_sync_url())
    try:
        with engine.connect() as conn:
            count = conn.execute(
                sa.text(
                    "SELECT count(*) FROM ingestion_manifest m "
                    "JOIN documents d ON m.doc_id = d.id "
                    "WHERE d.corpus_id = :c AND d.path = 'doomed.md'"
                ),
                {"c": CORPUS_A_NAME},
            ).scalar_one()
    finally:
        engine.dispose()
    assert count == 0
