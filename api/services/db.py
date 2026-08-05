"""Database setup and session management for Mind Palace."""

from __future__ import annotations

import os

from api.models.schemas import SearchResult
from sqlalchemy import create_engine, text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.orm import Session, sessionmaker

DATABASE_URL = os.getenv(
    "DATABASE_URL",
    "postgresql://mpadmin:secret@localhost:5432/mindpalace",
)

# Sync engine (for DDL, bulk inserts)
engine = create_engine(DATABASE_URL, echo=False)

# Async engine (for FastAPI async routes)
async_engine = create_async_engine(DATABASE_URL, echo=False)

AsyncSessionLocal = async_sessionmaker(
    async_engine, class_=AsyncSession, expire_on_commit=False
)
SessionLocal = sessionmaker(bind=engine)


async def init_db() -> None:
    """Create tables if they don't exist (idempotent)."""
    async with async_engine.begin() as conn:
        await conn.execute(
            text("""
            CREATE TABLE IF NOT EXISTS documents (
                id CHAR(64) PRIMARY KEY,
                title TEXT,
                path TEXT UNIQUE,
                document_type TEXT DEFAULT 'note',
                date DATE,
                summary TEXT,
                tags TEXT[] DEFAULT '{}',
                git_repo TEXT,
                status TEXT DEFAULT 'active',
                content_hash TEXT,
                last_indexed TIMESTAMP DEFAULT now(),
                created_at TIMESTAMP DEFAULT now(),
                updated_at TIMESTAMP DEFAULT now()
            )
        """)
        )
        await conn.execute(
            text("""
            CREATE TABLE IF NOT EXISTS chunks (
                id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
                doc_id CHAR(64) REFERENCES documents(id) ON DELETE CASCADE,
                order_index INT,
                text TEXT NOT NULL,
                heading_path TEXT,
                document_type TEXT,
                source_url TEXT,
                tags TEXT[] DEFAULT '{}',
                language TEXT DEFAULT 'en',
                token_count INT,
                embedding vector(384),
                embedding_model TEXT,
                embedding_dimension INT,
                embedding_version TEXT,
                metadata JSONB DEFAULT '{}',
                created_at TIMESTAMP DEFAULT now(),
                updated_at TIMESTAMP DEFAULT now()
            )
        """)
        )
        await conn.execute(
            text("""
            CREATE TABLE IF NOT EXISTS ingestion_manifest (
                path TEXT PRIMARY KEY,
                content_hash TEXT NOT NULL,
                doc_id CHAR(64),
                chunk_count INT DEFAULT 0,
                last_ingested TIMESTAMP DEFAULT now()
            )
        """)
        )
        await conn.execute(
            text("""
            CREATE INDEX IF NOT EXISTS idx_chunks_embedding
            ON chunks USING hnsw (embedding vector_cosine_ops)
        """)
        )
        await conn.execute(
            text("""
            CREATE INDEX IF NOT EXISTS idx_chunks_doc_id
            ON chunks (doc_id)
        """)
        )
        await conn.execute(
            text("""
            CREATE INDEX IF NOT EXISTS idx_chunks_document_type
            ON chunks (document_type)
        """)
        )
        await conn.execute(
            text("""
            CREATE INDEX IF NOT EXISTS idx_documents_type
            ON documents (document_type)
        """)
        )
        await conn.execute(
            text("""
            CREATE INDEX IF NOT EXISTS idx_documents_updated_at
            ON documents (updated_at)
        """)
        )
        await conn.execute(
            text("""
            CREATE INDEX IF NOT EXISTS idx_chunks_heading_path
            ON chunks (heading_path)
        """)
        )


def get_db() -> Session:
    """Sync session dependency."""
    session = SessionLocal()
    try:
        yield session
    finally:
        session.close()


async def get_async_db() -> AsyncSession:
    """Async session dependency."""
    async with AsyncSessionLocal() as session:
        yield session
