from __future__ import annotations

import base64
import hashlib
import hmac
import json
import logging
from datetime import UTC, datetime

import httpx
import pytest
from fastapi.testclient import TestClient

from api.main import app
from api.models.memory import FeedItem, FeedResponse
from api.services.memory_feed import decode_cursor, encode_cursor
from api.services.memory_public import MemoryError
from mindpalace_sdk import MemoryClientError, MindPalace

SECRET = "m009-adapter-test-secret-" + "x" * 48
STAMP = "2026-01-01T00:00:00Z"


def _cursor_payload_cursor(payload: dict) -> str:
    raw = json.dumps(payload, sort_keys=True, separators=(",", ":")).encode()
    signature = hmac.new(SECRET.encode(), raw, hashlib.sha256).digest()

    def encode(value: bytes) -> str:
        return base64.urlsafe_b64encode(value).decode().rstrip("=")

    return encode(raw) + "." + encode(signature)


def _valid_body(**updates):
    body = {
        "schema_version": 1,
        "corpus": "adapter-corpus",
        "items": [
            {
                "version_id": "a" * 64,
                "document_id": "doc-a",
                "corpus": "adapter-corpus",
                "path": "docs/a.md",
                "version_number": 1,
                "status": "NEW",
                "observed_at": STAMP,
                "predecessor_id": None,
            }
        ],
        "has_more": False,
        "next_cursor": None,
        "page_size": 5,
    }
    body.update(updates)
    return body


def test_cursor_rejects_empty_non_string_and_signed_malformed_payloads(monkeypatch):
    monkeypatch.setenv("MIND_PALACE_CURSOR_SECRET", SECRET)
    with pytest.raises(MemoryError) as empty:
        decode_cursor("", "adapter-corpus")
    assert empty.value.code == "invalid_cursor"

    with pytest.raises(MemoryError) as non_string:
        decode_cursor(None, "adapter-corpus")  # type: ignore[arg-type]
    assert non_string.value.code == "invalid_cursor"

    base = {
        "version": 1,
        "feed": "memory_versions",
        "corpus": "adapter-corpus",
        "observed_at": STAMP,
        "version_id": "a" * 64,
    }
    invalid_payloads = [
        {**base, "version": True},
        {**base, "version": 2},
        {**base, "feed": "other_feed"},
        {**base, "observed_at": "2026-01-01T00:00:00"},
        {**base, "version_id": 7},
        {**base, "unexpected": "field"},
        [],
    ]
    for payload in invalid_payloads:
        with pytest.raises(MemoryError) as error:
            decode_cursor(_cursor_payload_cursor(payload), "adapter-corpus")
        assert error.value.code == "invalid_cursor"


def test_cursor_requires_a_strong_configured_secret(monkeypatch):
    monkeypatch.delenv("MIND_PALACE_CURSOR_SECRET", raising=False)
    with pytest.raises(MemoryError) as missing:
        encode_cursor("adapter-corpus", datetime(2026, 1, 1, tzinfo=UTC), "a" * 64)
    assert missing.value.code == "cursor_unavailable"

    monkeypatch.setenv("MIND_PALACE_CURSOR_SECRET", "too-short")
    with pytest.raises(MemoryError) as weak:
        encode_cursor("adapter-corpus", datetime(2026, 1, 1, tzinfo=UTC), "a" * 64)
    assert weak.value.code == "cursor_unavailable"


def test_sdk_feed_preserves_structured_errors_and_validates_responses(monkeypatch):
    calls = []

    def get(url, **kwargs):
        calls.append((url, kwargs))
        return httpx.Response(
            422,
            json={"detail": {"code": "cursor_corpus_mismatch", "message": "wrong corpus"}},
            request=httpx.Request("GET", url),
        )

    monkeypatch.setattr(httpx, "get", get)
    client = MindPalace(base_url="https://memory.example").memory
    with pytest.raises(MemoryClientError) as error:
        client.feed(corpus="adapter-corpus", page_size=5, cursor="bad")
    assert (error.value.code, error.value.status_code) == ("cursor_corpus_mismatch", 422)

    monkeypatch.setattr(
        httpx,
        "get",
        lambda url, **kwargs: httpx.Response(200, content=b"{", request=httpx.Request("GET", url)),
    )
    with pytest.raises(MemoryClientError) as invalid:
        client.feed(corpus="adapter-corpus", page_size=5)
    assert (invalid.value.code, invalid.value.status_code) == ("invalid_response", 502)


def test_sdk_feed_returns_typed_page_and_omits_absent_cursor_parameter(monkeypatch):
    captured = {}

    def get(url, **kwargs):
        captured.update(url=url, **kwargs)
        return httpx.Response(
            200,
            json=_valid_body(),
            request=httpx.Request("GET", url),
        )

    monkeypatch.setattr(httpx, "get", get)
    page = MindPalace(base_url="https://memory.example").memory.feed(
        corpus="adapter-corpus", page_size=5
    )
    assert isinstance(page, FeedResponse)
    assert page.items[0].observed_at == datetime(2026, 1, 1, tzinfo=UTC)
    assert captured["params"] == {"corpus": "adapter-corpus", "page_size": 5}
    assert captured["url"] == "https://memory.example/api/memory/feed"


def test_rest_feed_uses_shared_boundary_and_sanitizes_database_failure(monkeypatch):
    import api.services.db as db_module
    import api.services.memory_feed as feed_module

    class Session:
        async def __aenter__(self):
            return object()

        async def __aexit__(self, *_args):
            return False

    expected = FeedResponse.model_validate(_valid_body())
    seen = {}

    async def fake_feed(db, corpus, cursor, page_size):
        seen.update(db=db, corpus=corpus, cursor=cursor, page_size=page_size)
        return expected

    monkeypatch.setattr(db_module, "session_scope", lambda: Session())
    monkeypatch.setattr(feed_module, "feed", fake_feed)
    with TestClient(app) as client:
        response = client.get(
            "/api/memory/feed",
            params={"corpus": "adapter-corpus", "page_size": 5, "cursor": "opaque"},
        )
    assert response.status_code == 200
    assert response.json() == expected.model_dump(mode="json")
    assert seen["corpus"] == "adapter-corpus"
    assert seen["page_size"] == 5
    assert seen["cursor"] == "opaque"

    class BrokenSession:
        async def __aenter__(self):
            raise OSError("password=do-not-leak host=internal")

        async def __aexit__(self, *_args):
            return False

    monkeypatch.setattr(db_module, "session_scope", lambda: BrokenSession())
    with TestClient(app) as client:
        response = client.get("/api/memory/feed", params={"corpus": "adapter-corpus"})
    assert response.status_code == 503
    assert response.json() == {
        "detail": {"code": "database_unavailable", "message": "Memory database unavailable"}
    }
    assert "password" not in response.text
    assert "internal" not in response.text


def test_feed_logging_reports_bounded_operational_fields(monkeypatch, caplog):
    import api.services.db as db_module
    import api.services.memory_feed as feed_module

    class Session:
        async def __aenter__(self):
            return object()

        async def __aexit__(self, *_args):
            return False

    async def fake_feed(_db, _corpus, _cursor, _page_size):
        return FeedResponse.model_validate(_valid_body())

    monkeypatch.setattr(db_module, "session_scope", lambda: Session())
    monkeypatch.setattr(feed_module, "feed", fake_feed)
    with caplog.at_level(logging.INFO, logger="mindpalace.ops"):
        with TestClient(app) as client:
            response = client.get(
                "/api/memory/feed",
                params={"corpus": "adapter-corpus", "page_size": 5, "cursor": "opaque"},
            )
    assert response.status_code == 200
    message = "\n".join(record.getMessage() for record in caplog.records)
    assert "op=memory_feed" in message
    assert "page_size=5" in message
    assert "returned=1" in message
    assert "cursor_continuation=True" in message
    assert "cursor=opaque" not in message
    assert "adapter-corpus" in message


def test_rest_feed_rejects_empty_cursor_and_invalid_page_size(monkeypatch):
    with TestClient(app) as client:
        empty_cursor = client.get(
            "/api/memory/feed", params={"corpus": "adapter-corpus", "cursor": ""}
        )
        invalid_page = client.get(
            "/api/memory/feed", params={"corpus": "adapter-corpus", "page_size": 0}
        )
    assert empty_cursor.status_code == 422
    assert invalid_page.status_code == 422
    assert empty_cursor.json()["detail"]["code"] == "invalid_request"
    assert invalid_page.json()["detail"]["code"] == "invalid_request"


def test_feed_response_canonical_json_is_compact_and_feed_item_contract():
    response = FeedResponse(
        corpus="adapter-corpus",
        items=[
            FeedItem(
                version_id="a" * 64,
                document_id="doc-a",
                corpus="adapter-corpus",
                path="docs/a.md",
                version_number=1,
                status="NEW",
                observed_at=datetime(2026, 1, 1, tzinfo=UTC),
            )
        ],
        has_more=False,
        page_size=1,
    )
    assert response.canonical_json() == json.dumps(
        response.model_dump(mode="json"),
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )
