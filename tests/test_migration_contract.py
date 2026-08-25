"""Migration contract tests: verify the database schema and extension setup.

These run against a live PostgreSQL database that has had
``alembic upgrade head`` applied (CI applies migrations in a dedicated step;
locally run it against your DATABASE_URL first). They guard the
fresh-database setup contract:

- required server extensions exist (vector, pgcrypto, pg_trgm)
- chunks.embedding is a 384-dimensional vector column
- gen_random_uuid() works (chunk id default)
- the alembic version is at head

The Python `pgvector` package does NOT install the PostgreSQL `vector`
server extension; these tests exist so a missing-extension regression
fails loudly instead of surfacing as an opaque migration error.
"""

from __future__ import annotations

import os

import pytest
import sqlalchemy as sa

# DB-backed but synchronous (direct engine access); intentionally NOT marked
# with the asyncio mark other suites use.


def _sync_url() -> str:
    url = os.getenv("DATABASE_URL", "")
    return (
        url.replace("postgresql+psycopg://", "postgresql+psycopg2://")
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


def _scalar(sql: str) -> object:
    engine = sa.create_engine(_sync_url())
    try:
        with engine.connect() as conn:
            return conn.execute(sa.text(sql)).scalar()
    finally:
        engine.dispose()


@requires_db
def test_required_extensions_exist():
    extnames = {
        row[0]
        for row in sa.create_engine(_sync_url())
        .connect()
        .execute(sa.text("SELECT extname FROM pg_extension"))
        .fetchall()
    }
    assert "vector" in extnames, "pgvector server extension missing"
    assert "pgcrypto" in extnames, "pgcrypto missing (gen_random_uuid)"
    assert "pg_trgm" in extnames, "pg_trgm missing (hybrid keyword search)"


@requires_db
def test_chunks_embedding_is_vector_384():
    dim = _scalar(
        "SELECT atttypmod FROM pg_attribute "
        "WHERE attrelid = 'chunks'::regclass AND attname = 'embedding'"
    )
    assert dim == 384


@requires_db
def test_gen_random_uuid_works():
    assert _scalar("SELECT gen_random_uuid()") is not None


@requires_db
def test_all_expected_tables_exist():
    tables = {
        row[0]
        for row in sa.create_engine(_sync_url())
        .connect()
        .execute(sa.text("SELECT tablename FROM pg_tables WHERE schemaname = 'public'"))
        .fetchall()
    }
    assert {"documents", "chunks", "ingestion_manifest"} <= tables


@requires_db
def test_hnsw_index_on_embedding_exists():
    method = _scalar(
        "SELECT amname FROM pg_indexes i JOIN pg_class c ON i.indexname = c.relname "
        "JOIN pg_am am ON c.relam = am.oid "
        "WHERE i.tablename = 'chunks' AND i.indexname = 'idx_chunks_embedding'"
    )
    assert method == "hnsw"


@requires_db
def test_migration_at_head():
    from alembic.config import Config
    from alembic.script import ScriptDirectory

    current = _scalar("SELECT version_num FROM alembic_version")
    cfg = Config("migrations/alembic.ini")
    script = ScriptDirectory.from_config(cfg)
    head = script.get_current_head()
    assert current == head
