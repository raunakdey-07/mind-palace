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
                id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
                title TEXT,
                path TEXT UNIQUE,
                type TEXT DEFAULT 'markdown',
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
                doc_id UUID REFERENCES documents(id) ON DELETE CASCADE,
                order_index INT,
                text TEXT NOT NULL,
                section TEXT,
                embedding vector(384),
                metadata JSONB DEFAULT '{}',
                created_at TIMESTAMP DEFAULT now()
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
