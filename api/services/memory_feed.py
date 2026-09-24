"""Durable corpus-scoped operational feed over immutable memory versions."""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import os
import re
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import text
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.sql.elements import TextClause

from api.models.memory import FeedItem, FeedResponse
from api.services.corpora import get_corpus_by_name, validate_corpus_name
from api.services.memory_public import MemoryError, translate_database_error

MAX_PAGE_SIZE = 500
DEFAULT_PAGE_SIZE = 50
MAX_CURSOR_LENGTH = 4096
MIN_CURSOR_SECRET_BYTES = 32
_CURSOR_VERSION = 1
_CURSOR_FEED = "memory_versions"
_CORPUS_RE = re.compile(r"^[A-Za-z0-9._-]+$")
_CURSOR_PART_RE = re.compile(r"^[A-Za-z0-9_-]+$")


def _b64(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).decode().rstrip("=")


def _unb64(value: str) -> bytes:
    if not isinstance(value, str) or not value or not _CURSOR_PART_RE.fullmatch(value):
        raise ValueError("invalid base64")
    padded = value + "=" * (-len(value) % 4)
    try:
        decoded = base64.b64decode(padded, altchars=b"-_", validate=True)
    except (ValueError, TypeError) as exc:
        raise ValueError("invalid base64") from exc
    if _b64(decoded) != value:
        raise ValueError("non-canonical base64")
    return decoded


def _cursor_secret() -> bytes:
    value = os.getenv("MIND_PALACE_CURSOR_SECRET")
    if value is None or not value.strip():
        raise MemoryError(
            "cursor_unavailable",
            "Cursor signing is not configured",
            503,
        )
    secret = value.encode()
    if len(secret) < MIN_CURSOR_SECRET_BYTES:
        raise MemoryError(
            "cursor_unavailable",
            "Cursor signing configuration is invalid",
            503,
        )
    return secret


def _validate_cursor_datetime(value: Any) -> datetime:
    if not isinstance(value, datetime) or value.tzinfo is None or value.utcoffset() is None:
        raise MemoryError("invalid_cursor", "Invalid change-feed cursor", 422)
    return value


def encode_cursor(corpus: str, observed_at: datetime, version_id: str) -> str:
    try:
        corpus = validate_corpus_name(corpus)
    except (AttributeError, TypeError, ValueError) as exc:
        raise MemoryError("invalid_corpus", "Invalid corpus name", 422) from exc
    observed_at = _validate_cursor_datetime(observed_at)
    if not isinstance(version_id, str) or not version_id or len(version_id) > 256:
        raise MemoryError("invalid_cursor", "Invalid change-feed cursor", 422)
    payload = {
        "version": _CURSOR_VERSION,
        "feed": _CURSOR_FEED,
        "corpus": corpus,
        "observed_at": observed_at.astimezone(UTC).isoformat(),
        "version_id": version_id,
    }
    raw = json.dumps(payload, sort_keys=True, separators=(",", ":")).encode()
    signature = hmac.new(_cursor_secret(), raw, hashlib.sha256).digest()
    return _b64(raw) + "." + _b64(signature)


def decode_cursor(cursor: str, corpus: str) -> tuple[datetime, str]:
    if not isinstance(cursor, str) or not cursor or len(cursor) > MAX_CURSOR_LENGTH:
        raise MemoryError("invalid_cursor", "Invalid change-feed cursor", 422)
    try:
        parts = cursor.split(".")
        if len(parts) != 2 or not all(parts):
            raise ValueError("cursor shape")
        raw = _unb64(parts[0])
        signature = _unb64(parts[1])
        if len(signature) != hashlib.sha256().digest_size:
            raise ValueError("signature shape")
        expected = hmac.new(_cursor_secret(), raw, hashlib.sha256).digest()
        if not hmac.compare_digest(signature, expected):
            raise ValueError("signature")
        payload = json.loads(raw.decode("utf-8"))
        required = {"version", "feed", "corpus", "observed_at", "version_id"}
        if not isinstance(payload, dict) or set(payload) != required:
            raise ValueError("payload shape")
        if type(payload["version"]) is not int or payload["version"] != _CURSOR_VERSION:
            raise ValueError("cursor version")
        if payload["feed"] != _CURSOR_FEED:
            raise ValueError("cursor feed")
        if not isinstance(payload["corpus"], str) or not _CORPUS_RE.fullmatch(payload["corpus"]):
            raise ValueError("cursor corpus")
        if payload["corpus"] != corpus:
            raise MemoryError("cursor_corpus_mismatch", "Cursor belongs to another corpus", 422)
        if not isinstance(payload["version_id"], str) or not payload["version_id"]:
            raise ValueError("cursor version id")
        if len(payload["version_id"]) > 256:
            raise ValueError("cursor version id")
        observed_at = datetime.fromisoformat(payload["observed_at"])
        if observed_at.tzinfo is None or observed_at.utcoffset() is None:
            raise ValueError("cursor timestamp")
        return observed_at, payload["version_id"]
    except MemoryError:
        raise
    except (ValueError, TypeError, UnicodeError, json.JSONDecodeError) as exc:
        raise MemoryError("invalid_cursor", "Invalid change-feed cursor", 422) from exc


def build_feed_query(
    corpus_id: str, boundary: tuple[datetime, str] | None, limit: int
) -> tuple[TextClause, dict[str, Any]]:
    """Build the exact parameterized keyset query used by the feed service."""
    where = "v.corpus_id = :c"
    params: dict[str, Any] = {"c": corpus_id, "limit": limit}
    if boundary is not None:
        where += " AND (v.observed_at, v.id) > (:observed_at, :version_id)"
        params.update(observed_at=boundary[0], version_id=boundary[1])
    return (
        text(f"""
        SELECT v.id, v.document_id, v.memory_document_id, v.version_number,
               v.event, v.observed_at, v.predecessor_id, d.path
        FROM memory_versions v JOIN memory_documents d
          ON d.corpus_id = v.corpus_id AND d.id = v.memory_document_id
        WHERE {where}
        ORDER BY v.observed_at ASC, v.id ASC
        LIMIT :limit
    """),
        params,
    )


async def feed(db: AsyncSession, corpus: str, cursor: str | None, page_size: int) -> FeedResponse:
    if (
        not isinstance(page_size, int)
        or isinstance(page_size, bool)
        or not 1 <= page_size <= MAX_PAGE_SIZE
    ):
        raise MemoryError("invalid_page_size", "page_size must be between 1 and 500", 422)
    try:
        corpus = validate_corpus_name(corpus)
    except (AttributeError, TypeError, ValueError) as exc:
        raise MemoryError("invalid_corpus", "Invalid corpus name", 422) from exc

    boundary = decode_cursor(cursor, corpus) if cursor is not None else None
    try:
        corpus_row = await get_corpus_by_name(db, corpus)
        if corpus_row is None:
            raise MemoryError("corpus_not_found", "Corpus not found", 404)
        query, params = build_feed_query(corpus_row["id"], boundary, page_size + 1)
        result = await db.execute(query, params)
        rows = [dict(row) for row in result.mappings()]
    except MemoryError:
        raise
    except (SQLAlchemyError, OSError, TimeoutError) as exc:
        raise translate_database_error(exc) from exc

    has_more = len(rows) > page_size
    rows = rows[:page_size]
    items = [
        FeedItem(
            version_id=row["id"],
            document_id=row["document_id"] or row["memory_document_id"],
            corpus=corpus,
            path=row["path"],
            version_number=row["version_number"],
            status=row["event"],
            observed_at=row["observed_at"],
            predecessor_id=row["predecessor_id"],
        )
        for row in rows
    ]
    next_cursor = (
        encode_cursor(corpus, rows[-1]["observed_at"], rows[-1]["id"])
        if has_more and rows
        else None
    )
    return FeedResponse(
        corpus=corpus,
        items=items,
        has_more=has_more,
        next_cursor=next_cursor,
        page_size=page_size,
    )
