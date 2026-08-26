"""SDK smoke tests: the developer-facing MindPalace client.

These run against a live database when DATABASE_URL is set; they exercise
the exact flow from the product thesis:

    mp = MindPalace("my-corpus")
    mp.sync("./docs")
    context = mp.context("question", budget_tokens=4000)
"""

from __future__ import annotations

import os

import pytest

# SDK is a synchronous client; tests call it synchronously.


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
def _cleanup_sdk_corpus():
    def purge():
        import sqlalchemy as sa

        engine = sa.create_engine(
            (
                os.environ["DATABASE_URL"]
                .replace("postgresql+psycopg://", "postgresql+psycopg2://")
                .replace("postgresql://", "postgresql+psycopg2://")
            )
        )
        try:
            with engine.begin() as conn:
                conn.execute(
                    sa.text(
                        "DELETE FROM ingestion_manifest WHERE corpus_id IN "
                        "(SELECT id FROM corpora WHERE name = 'sdk-test-corpus')"
                    )
                )
                conn.execute(
                    sa.text(
                        "DELETE FROM chunks WHERE doc_id IN "
                        "(SELECT id FROM documents WHERE corpus_id IN "
                        "(SELECT id FROM corpora WHERE name = 'sdk-test-corpus'))"
                    )
                )
                conn.execute(
                    sa.text(
                        "DELETE FROM documents WHERE corpus_id IN "
                        "(SELECT id FROM corpora WHERE name = 'sdk-test-corpus')"
                    )
                )
                conn.execute(sa.text("DELETE FROM corpora WHERE name = 'sdk-test-corpus'"))
        finally:
            engine.dispose()

    if _db_available():
        purge()
    yield
    if _db_available():
        purge()


@requires_db
def test_sdk_end_to_end(tmp_path):
    """The full product flow: create -> sync -> context with attribution."""
    from mindpalace_sdk import MindPalace

    docs = tmp_path / "docs"
    docs.mkdir()
    (docs / "auth.md").write_text("""---
title: "Authentication Guide"
date: 2024-07-01
tags: ["security"]
document_type: "note"
summary: "How authentication works"
---

# Authentication

The system uses flurble-token authentication with wibble rotation every hour.
""")

    mp = MindPalace("sdk-test-corpus")

    summary = mp.sync(str(docs))
    assert summary.success
    assert summary.added == 1
    assert summary.chunk_count >= 1

    # Re-sync unchanged content: nothing to do.
    again = mp.sync(str(docs))
    assert again.added == 0
    assert again.unchanged == 1

    pack = mp.context("how does authentication work", budget_tokens=2000)
    assert pack.token_estimate > 0
    assert "flurble-token" in pack.context
    assert any(s.title == "Authentication Guide" for s in pack.sources)
    assert not pack.truncated


@requires_db
def test_sdk_budget_enforced(tmp_path):
    from mindpalace_sdk import MindPalace

    docs = tmp_path / "docs"
    docs.mkdir()
    for i in range(5):
        (docs / f"doc{i}.md").write_text(f"""---
title: "Budget Doc {i}"
date: 2024-07-01
document_type: "note"
---

# Doc {i}

Padding text {"x" * 900} unique marker {i}.
""")

    mp = MindPalace("sdk-test-corpus")
    mp.sync(str(docs))

    pack = mp.context("unique markers in budget docs", budget_tokens=1024)
    # 1024 tokens * 4 chars = ~4096 chars max
    assert len(pack.context) <= 4096 + 10  # small tolerance for separators
