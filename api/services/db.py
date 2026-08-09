# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 Raunak Dey

"""Database setup and session management for Mind Palace.

Note: Schema is managed by Alembic migrations. This module only provides
session management. Run `alembic upgrade head` to apply migrations.
"""

from __future__ import annotations

import os

from sqlalchemy import create_engine
from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)
from sqlalchemy.orm import Session, sessionmaker

DATABASE_URL = os.getenv(
    "DATABASE_URL",
    "postgresql://mpadmin:secret@localhost:5432/mindpalace",
)

# Sync engine (for DDL, bulk inserts)
engine = create_engine(DATABASE_URL, echo=False)

# Async engine (for FastAPI async routes) - use asyncpg driver
async_database_url = DATABASE_URL.replace("postgresql://", "postgresql+asyncpg://")
async_engine: AsyncEngine = create_async_engine(async_database_url, echo=False)

AsyncSessionLocal = async_sessionmaker(async_engine, class_=AsyncSession, expire_on_commit=False)
SessionLocal = sessionmaker(bind=engine)


async def init_db() -> None:
    """Verify database connectivity. Schema is managed by Alembic."""
    async with async_engine.begin() as conn:
        # Just verify connection works
        await conn.execute("SELECT 1")


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
