# SPDX-License-Identifier: Apache-2.0

# Copyright 2026 Raunak Dey

"""Database setup and async session management for Mind Palace.

Schema is managed by Alembic migrations. This module only provides
async session management and connectivity checks.
"""

from __future__ import annotations

import os
from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager

from sqlalchemy import text
from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)
from sqlalchemy.pool import NullPool

DATABASE_URL = os.getenv(
    "DATABASE_URL",
    "postgresql://mpadmin:secret@localhost:5432/mindpalace",
)

# Normalize any sync-style URL to asyncpg
ASYNC_DATABASE_URL = (
    DATABASE_URL.replace("postgresql+psycopg://", "postgresql+asyncpg://")
    .replace("postgresql+psycopg2://", "postgresql+asyncpg://")
    .replace("postgresql://", "postgresql+asyncpg://")
)

# NullPool: connections are not reused across event loops. The API server
# runs a single long-lived loop so pooling helps there, but SDK/CLI callers
# create short-lived loops per operation and pooled connections would break
# with 'attached to a different loop' errors.
async_engine: AsyncEngine = create_async_engine(
    ASYNC_DATABASE_URL,
    echo=False,
    poolclass=NullPool,
)

AsyncSessionLocal = async_sessionmaker(
    async_engine,
    class_=AsyncSession,
    expire_on_commit=False,
)


async def init_db() -> None:
    """Verify database connectivity. Schema is managed by Alembic."""
    async with async_engine.begin() as conn:
        await conn.execute(text("SELECT 1"))


async def get_async_db() -> AsyncGenerator[AsyncSession, None]:
    """FastAPI async session dependency."""
    async with AsyncSessionLocal() as session:
        yield session


@asynccontextmanager
async def session_scope() -> AsyncGenerator[AsyncSession, None]:
    """Standalone async session context manager for non-FastAPI callers."""
    async with AsyncSessionLocal() as session:
        yield session
