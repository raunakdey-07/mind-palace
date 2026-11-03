"""`path_prefix` scopes memory reads to a folder (or a union of folders).

Acceptance tests for v0.10.0 PR-4. Every test owns a corpus created over HTTP
(`POST /api/corpora`, asserted 201) and passes that corpus to every read call.
Reads use the in-process client (httpx ASGITransport over `api.main.app`), exactly
as tests/test_withholding.py does. Optional fields are read with `.get()`.
"""

from __future__ import annotations

import uuid

import pytest
from httpx import ASGITransport, AsyncClient

from api.main import app
from api.services import authz
from api.services import db as db_module

API_BASE = "http://test"

BRAND = "Brand voice stays warm for every launch."
A = "Campaign A launch targets city cyclists."
AB = "Campaign AB launch targets night runners."
B = "Campaign B launch targets weekend hikers."


def corpus_name() -> str:
    return f"t{uuid.uuid4().hex[:12]}"


async def create_corpus(client, name: str) -> None:
    response = await client.post("/api/corpora", json={"name": name})
    assert response.status_code == 201, response.text


async def ingest(client, corpus: str, path: str, content: str) -> None:
    response = await client.post(
        "/api/ingest/file",
        json={"content": content, "path": path, "corpus": corpus},
    )
    assert response.status_code == 200, response.text


async def pack(client, corpus: str, **fields) -> dict:
    response = await client.post(
        "/api/memory/pack",
        json={"corpus": corpus, "query": "", "budget": 8000, **fields},
    )
    assert response.status_code == 200, response.text
    return response.json()


STANDARD_DOCS = {
    "brand/voice.md": "# Brand voice\n\n- Brand voice stays warm for every launch.\n",
    "campaigns/a/brief.md": "# Campaign A brief\n\n- Campaign A launch targets city cyclists.\n",
    "campaigns/ab/brief.md": "# Campaign AB brief\n\n- Campaign AB launch targets night runners.\n",
    "campaigns/b/brief.md": "# Campaign B brief\n\n- Campaign B launch targets weekend hikers.\n",
}


async def seed_standard(client, corpus: str) -> None:
    for path, content in STANDARD_DOCS.items():
        await ingest(client, corpus, path, content)


# ---------------------------------------------------------------------------
# 1-3: basic scoping
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_pack_path_prefix_union():
    corpus = corpus_name()
    async with AsyncClient(transport=ASGITransport(app=app), base_url=API_BASE) as client:
        await create_corpus(client, corpus)
        await seed_standard(client, corpus)
        body = await pack(client, corpus, path_prefix=["brand", "campaigns/a"])
    text = str(body)
    assert BRAND in text
    assert A in text
    assert AB not in text
    assert B not in text


@pytest.mark.asyncio
async def test_pack_path_prefix_trailing_slash():
    corpus = corpus_name()
    async with AsyncClient(transport=ASGITransport(app=app), base_url=API_BASE) as client:
        await create_corpus(client, corpus)
        await seed_standard(client, corpus)
        body = await pack(client, corpus, path_prefix="campaigns/a/")
    text = str(body)
    assert A in text
    assert AB not in text
    assert B not in text
    assert BRAND not in text


@pytest.mark.asyncio
async def test_pack_path_prefix_exact_not_prefix():
    corpus = corpus_name()
    async with AsyncClient(transport=ASGITransport(app=app), base_url=API_BASE) as client:
        await create_corpus(client, corpus)
        await seed_standard(client, corpus)
        body = await pack(client, corpus, path_prefix="campaigns/a")
    text = str(body)
    assert A in text
    assert AB not in text


# ---------------------------------------------------------------------------
# 4-5: validation and empty
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_path_and_path_prefix_conflict():
    corpus = corpus_name()
    async with AsyncClient(transport=ASGITransport(app=app), base_url=API_BASE) as client:
        await create_corpus(client, corpus)
        await seed_standard(client, corpus)
        response = await client.post(
            "/api/memory/pack",
            json={
                "corpus": corpus,
                "query": "",
                "budget": 8000,
                "path": "brand/voice.md",
                "path_prefix": "brand",
            },
        )
    assert response.status_code == 422, response.text
    assert response.json()["detail"]["code"] == "path_conflict"


@pytest.mark.asyncio
async def test_pack_path_prefix_empty_result():
    corpus = corpus_name()
    async with AsyncClient(transport=ASGITransport(app=app), base_url=API_BASE) as client:
        await create_corpus(client, corpus)
        await seed_standard(client, corpus)
        body = await pack(client, corpus, path_prefix="campaigns/zzz")
    assert body.get("current_memories", []) == []


# ---------------------------------------------------------------------------
# 6: scoped conflicts
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_scoped_conflicts_match_unscoped():
    corpus = corpus_name()
    async with AsyncClient(transport=ASGITransport(app=app), base_url=API_BASE) as client:
        await create_corpus(client, corpus)
        await ingest(
            client,
            corpus,
            "brand/decision.md",
            (
                "---\n"
                "claims:\n"
                "  - key: decision\n"
                '    claim: "Decision is red."\n'
                "    value: red\n"
                '    evidence: "Decision is red."\n'
                "---\n"
                "\n"
                "# Brand decision\n"
                "\n"
                "Decision is red.\n"
            ),
        )
        await ingest(
            client,
            corpus,
            "campaigns/a/decision.md",
            (
                "---\n"
                "claims:\n"
                "  - key: decision\n"
                '    claim: "Decision is blue."\n'
                "    value: blue\n"
                '    evidence: "Decision is blue."\n'
                "---\n"
                "\n"
                "# Campaign A decision\n"
                "\n"
                "Decision is blue.\n"
            ),
        )
        scoped = await pack(client, corpus, path_prefix=["brand", "campaigns/a"])
        unscoped = await pack(client, corpus)
    assert len(scoped.get("conflicts", [])) == len(unscoped.get("conflicts", [])) == 1


# ---------------------------------------------------------------------------
# 7: authorization not bypassed
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_path_prefix_does_not_bypass_corpus_authorization(monkeypatch):
    monkeypatch.setenv("MIND_PALACE_AUTH_MODE", "required")
    name_x = "tx" + uuid.uuid4().hex[:8]
    name_y = "ty" + uuid.uuid4().hex[:8]
    async with db_module.admin_session_scope() as db:
        with db_module.principal_scope(authz.ADMIN_PRINCIPAL):
            from api.services.corpora import get_or_create_corpus

            await get_or_create_corpus(db, name_x)
            await get_or_create_corpus(db, name_y)
            issued = await authz.issue_credential(db, "pp-test", [name_x], "read_only")
    secret = issued["secret"]
    async with AsyncClient(transport=ASGITransport(app=app), base_url=API_BASE) as client:
        r1 = await client.post(
            "/api/memory/pack",
            headers={"Authorization": f"Bearer {secret}"},
            json={"corpus": name_y, "query": "", "budget": 8000, "path_prefix": "brand"},
        )
        r2 = await client.post(
            "/api/memory/pack",
            headers={"Authorization": f"Bearer {secret}"},
            json={"corpus": name_y, "query": "", "budget": 8000},
        )
    assert r1.status_code == r2.status_code, (r1.status_code, r2.status_code, r1.text, r2.text)
    assert r1.status_code in (403, 404), (r1.status_code, r1.text)


# ---------------------------------------------------------------------------
# 8: literal "_" and "%"
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_path_prefix_literal_underscore_percent():
    corpus = corpus_name()
    async with AsyncClient(transport=ASGITransport(app=app), base_url=API_BASE) as client:
        await create_corpus(client, corpus)
        await seed_standard(client, corpus)
        await ingest(
            client,
            corpus,
            "campaigns/aXb/x.md",
            "# Campaign AXB note\n\n- Campaign AXB launch targets dog walkers.\n",
        )
        body = await pack(client, corpus, path_prefix="campaigns/a_b")
    text = str(body)
    assert "dog walkers" not in text
    assert body.get("current_memories", []) == []


# ---------------------------------------------------------------------------
# 9: G guard
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_no_path_prefix_is_byte_identical_today():
    corpus = corpus_name()
    async with AsyncClient(transport=ASGITransport(app=app), base_url=API_BASE) as client:
        await create_corpus(client, corpus)
        await seed_standard(client, corpus)
        body = await pack(client, corpus)
    texts = {c.get("claim") for c in body.get("current_memories", [])}
    assert texts == {BRAND, A, AB, B}
    assert "path_prefix" not in str(body)


# ---------------------------------------------------------------------------
# 10: templated briefs conflict isolation
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_scoped_conflict_isolates_campaign():
    corpus = corpus_name()
    async with AsyncClient(transport=ASGITransport(app=app), base_url=API_BASE) as client:
        await create_corpus(client, corpus)
        await ingest(
            client,
            corpus,
            "campaigns/spring/brief.md",
            "# Campaign brief\n\n## Headline\n\n- Headline is Summer of Speed now.\n",
        )
        await ingest(
            client,
            corpus,
            "campaigns/autumn/brief.md",
            "# Campaign brief\n\n## Headline\n\n- Headline is Autumn Glow Sale now.\n",
        )
        body1 = await pack(client, corpus)
        conflicts1 = body1.get("conflicts", [])
        assert len(conflicts1) == 1, (
            conflicts1,
            [c.get("claim") for c in body1.get("current_memories", [])],
        )
        body2 = await pack(client, corpus, path_prefix=["campaigns/spring"])
    text2 = str(body2)
    assert "Autumn Glow" not in text2
    assert any("Summer of Speed" in c.get("claim", "") for c in body2.get("current_memories", []))
    assert body2.get("conflicts", []) == []


# ---------------------------------------------------------------------------
# 11-12: history and query
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_history_path_prefix_filters():
    corpus = corpus_name()
    async with AsyncClient(transport=ASGITransport(app=app), base_url=API_BASE) as client:
        await create_corpus(client, corpus)
        await seed_standard(client, corpus)
        body = await client.post(
            "/api/memory/history",
            json={"corpus": corpus, "query": "", "path_prefix": "campaigns/a"},
        )
        assert body.status_code == 200, body.text
        body = body.json()
    changes = body.get("changes", [])
    assert len(changes) >= 1
    for ch in changes:
        assert ch["path"].startswith("campaigns/a/"), ch


@pytest.mark.asyncio
async def test_query_path_prefix_filters():
    corpus = corpus_name()
    async with AsyncClient(transport=ASGITransport(app=app), base_url=API_BASE) as client:
        await create_corpus(client, corpus)
        await seed_standard(client, corpus)
        response = await client.post(
            "/api/memory/query",
            json={"corpus": corpus, "query": "launch", "path_prefix": ["campaigns/a"]},
        )
        assert response.status_code == 200, response.text
        body = response.json()
    text = str(body)
    assert A in text
    assert AB not in text
    assert B not in text
    assert BRAND not in text


# ---------------------------------------------------------------------------
# 13: empty prefix validation
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_path_prefix_empty_is_422():
    corpus = corpus_name()
    async with AsyncClient(transport=ASGITransport(app=app), base_url=API_BASE) as client:
        await create_corpus(client, corpus)
        await seed_standard(client, corpus)
        for pp in ["/", []]:
            response = await client.post(
                "/api/memory/pack",
                json={"corpus": corpus, "query": "", "budget": 8000, "path_prefix": pp},
            )
            assert response.status_code == 422, response.text
            assert response.json()["detail"]["code"] == "invalid_request"


# ---------------------------------------------------------------------------
# 14: SDK payload
# ---------------------------------------------------------------------------


def test_sdk_pack_sends_path_prefix():
    corpus = corpus_name()
    from mindpalace_sdk import MindPalace
    import httpx

    captured = {}

    def handle(request: httpx.Request) -> httpx.Response:
        captured["body"] = request.read().decode()
        return httpx.Response(
            200,
            json={
                "schema_version": 1,
                "query": "",
                "corpus": corpus,
                "state": {"as_of": None, "valid_at": "2026-01-01T00:00:00Z", "snapshot": None},
                "current_memories": [],
                "historical_memories": [],
                "uncertain_memories": [],
                "changes": [],
                "conflicts": [],
                "constraints": [],
                "evidence": [],
                "sources": [],
                "truncated": False,
                "budget_unit": "unicode_characters",
                "match_status": "abstained",
                "match_margin": None,
                "candidates": [],
                "unmatched_subparts": [],
            },
        )

    client = httpx.Client(transport=httpx.MockTransport(handle))
    import json as _json

    mp = MindPalace(corpus, base_url="http://test")
    # monkeypatch httpx.post globally? The SDK uses httpx.post directly.
    orig_post = httpx.post
    httpx.post = client.post
    try:
        mp.memory.pack(query="", budget=8000, path_prefix=["brand", "campaigns/a"])
    finally:
        httpx.post = orig_post
    body = _json.loads(captured["body"])
    assert body.get("path_prefix") == ["brand", "campaigns/a"], body


# ---------------------------------------------------------------------------
# 15: scoped reads keep cross-document supersession
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_scoped_read_keeps_cross_document_supersession():
    corpus = corpus_name()
    async with AsyncClient(transport=ASGITransport(app=app), base_url=API_BASE) as client:
        await create_corpus(client, corpus)
        await ingest(
            client,
            corpus,
            "brand/voice.md",
            "# Brand voice\n\n- Brand voice stays warm for every launch.\n",
        )
        r1 = await client.post(
            "/api/memory/remember",
            json={
                "corpus": corpus,
                "statement": "Brand voice is bold for every launch now.",
                "key": "brand.voice.v2",
                "supersedes_claim_id": "Brand voice stays warm for every launch",
            },
        )
        assert r1.status_code == 200, r1.text
        body_pack = await pack(client, corpus, path_prefix=["brand"])
        body_query_resp = await client.post(
            "/api/memory/query",
            json={"corpus": corpus, "query": "launch", "path_prefix": ["brand"], "budget": 8000},
        )
        assert body_query_resp.status_code == 200, body_query_resp.text
        body_query = body_query_resp.json()
    for body in (body_pack, body_query):
        claims = body.get("current_memories", [])
        assert not any("stays warm" in c.get("claim", "") for c in claims), body
