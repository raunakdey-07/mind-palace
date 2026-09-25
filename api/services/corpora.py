# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 Raunak Dey

"""Corpus management: explicit namespaces for isolated document collections.

A corpus is the unit of tenancy in Mind Palace. Documents, chunks, and
embeddings all belong to exactly one corpus; retrieval and ingestion are
always corpus-scoped so data cannot leak across corpora.
"""

from __future__ import annotations

import hashlib
import re
from typing import Optional

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

# The migration-created corpus that holds all pre-corpus documents.
DEFAULT_CORPUS_ID = "00000000000000000000000000000000000000000000000000000000default"
DEFAULT_CORPUS_NAME = "default"

_NAME_RE = re.compile(r"[A-Za-z0-9._\-]+")


def corpus_id_for_name(name: str) -> str:
    """Deterministic corpus ID derived from the unique corpus name."""
    return hashlib.sha256(f"corpus:{name}".encode()).hexdigest()


def validate_corpus_name(name: str) -> str:
    """Validate and normalize a corpus name.

    Names become part of identifiers and URIs, so restrict to a conservative
    charset: letters, digits, hyphen, underscore, dot. 1-128 chars.
    """
    name = name.strip()
    if not (1 <= len(name) <= 128):
        raise ValueError("corpus name must be 1-128 characters")
    if not re_fullmatch(name):
        raise ValueError("corpus name may contain only letters, digits, '-', '_', '.'")
    return name


def re_fullmatch(name: str) -> bool:
    import re

    return re.fullmatch(r"[A-Za-z0-9._\-]+", name) is not None


async def create_corpus(
    db: AsyncSession,
    name: str,
    description: str = "",
) -> dict:
    """Create a corpus. Raises ValueError if the name is taken or invalid."""
    name = validate_corpus_name(name)
    corpus_id = corpus_id_for_name(name)

    existing = await get_corpus_by_name(db, name)
    if existing:
        raise ValueError(f"corpus '{name}' already exists")

    await db.execute(
        text("""
            INSERT INTO corpora (id, name, description)
            VALUES (:id, :name, :description)
        """),
        {"id": corpus_id, "name": name, "description": description},
    )
    await db.commit()
    return {"id": corpus_id, "name": name, "description": description}


async def get_corpus_by_name(db: AsyncSession, name: str) -> Optional[dict]:
    result = await db.execute(
        text("SELECT id, name, description FROM corpora WHERE name = :name"),
        {"name": name},
    )
    row = result.first()
    if not row:
        return None
    return {"id": row[0], "name": row[1], "description": row[2]}


class CorpusScopeNotFound(LookupError):
    """The explicitly requested corpus does not exist."""


class CorpusScopeRequired(ValueError):
    """An operation cannot choose safely among multiple corpora."""


async def resolve_corpus_scope(
    db: AsyncSession, corpus: str | None
) -> tuple[Optional[str], Optional[str]]:
    """Resolve a safe corpus scope for live retrieval routes.

    A single corpus remains ergonomic when the caller omits the name. Multiple
    corpora require an explicit name so retrieval cannot cross namespaces.
    """
    if corpus is not None:
        row = await get_corpus_by_name(db, corpus)
        if row is None:
            raise CorpusScopeNotFound(f"corpus '{corpus}' not found")
        return row["id"], row["name"]

    result = await db.execute(text("SELECT id, name FROM corpora ORDER BY name LIMIT 2"))
    rows = result.fetchall()
    if len(rows) > 1:
        raise CorpusScopeRequired("corpus is required when multiple corpora exist")
    if rows:
        return rows[0][0], rows[0][1]
    return None, None


async def get_or_create_corpus(db: AsyncSession, name: str) -> dict:
    """Return the corpus, creating it if needed. Used by sync-style flows."""
    existing = await get_corpus_by_name(db, name)
    if existing:
        return existing
    return await create_corpus(db, name)


async def list_corpora(db: AsyncSession) -> list[dict]:
    """List all corpora with document counts."""
    result = await db.execute(text("""
            SELECT c.id, c.name, c.description, count(d.id) AS document_count
            FROM corpora c
            LEFT JOIN documents d ON d.corpus_id = c.id
            GROUP BY c.id, c.name, c.description
            ORDER BY c.name
        """))
    return [
        {
            "id": row[0],
            "name": row[1],
            "description": row[2],
            "document_count": row[3],
        }
        for row in result.fetchall()
    ]


async def delete_corpus(db: AsyncSession, name: str) -> bool:
    """Delete a corpus and all its documents/chunks. Returns True if deleted."""
    corpus = await get_corpus_by_name(db, name)
    if not corpus:
        return False
    # chunks cascade from documents; documents cascade from the corpus
    await db.execute(text("DELETE FROM documents WHERE corpus_id = :cid"), {"cid": corpus["id"]})
    await db.execute(text("DELETE FROM corpora WHERE id = :cid"), {"cid": corpus["id"]})
    await db.commit()
    return True


async def corpus_stats(db: AsyncSession, name: str) -> Optional[dict]:
    """Ingestion/retrieval statistics for one corpus."""
    corpus = await get_corpus_by_name(db, name)
    if not corpus:
        return None
    result = await db.execute(
        text("""
            SELECT
                (SELECT count(*) FROM documents WHERE corpus_id = :cid),
                (SELECT count(*) FROM chunks c JOIN documents d ON c.doc_id = d.id
                 WHERE d.corpus_id = :cid),
                (SELECT coalesce(max(last_ingested), NULL) FROM ingestion_manifest m
                 JOIN documents d ON m.doc_id = d.id WHERE d.corpus_id = :cid)
        """),
        {"cid": corpus["id"]},
    )
    row = result.first()
    return {
        **corpus,
        "document_count": row[0],
        "chunk_count": row[1],
        "last_ingested": row[2],
    }
