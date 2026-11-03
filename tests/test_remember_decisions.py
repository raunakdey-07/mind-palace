"""Remember records human decisions: `state` and `scope` on a remembered claim.

Acceptance tests for v0.10.0 PR-3. Every test owns a corpus created over HTTP
(`POST /api/corpora`, asserted 201) and passes that corpus to every remember and
read call. Reads use the in-process client (httpx ASGITransport over
`api.main.app`), exactly as tests/test_withholding.py does.

Statements are at least 12 characters. Optional fields are read with `.get()`.
"""

from __future__ import annotations

import os
from uuid import uuid4

import pytest
from httpx import ASGITransport, AsyncClient

from api.main import app
from api.services.remember import memory_path

API_BASE = "http://test"


def corpus_name() -> str:
    return f"t{uuid4().hex[:12]}"


async def create_corpus(client, name: str) -> None:
    response = await client.post("/api/corpora", json={"name": name})
    assert response.status_code == 201, response.text


async def remember(client, corpus: str, expected: int = 200, **fields) -> dict:
    response = await client.post(
        "/api/memory/remember",
        json={"corpus": corpus, **fields},
    )
    assert response.status_code == expected, response.text
    return response.json()


async def read(client, operation: str, corpus: str, **fields) -> dict:
    response = await client.post(
        f"/api/memory/{operation}",
        json={"corpus": corpus, "query": "", **fields},
    )
    assert response.status_code == 200, response.text
    return response.json()


# ---------------------------------------------------------------------------
# 1-3: state is stored and withholds like any other decided state
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_rejected_claim_is_withheld_from_pack():
    corpus = corpus_name()
    async with AsyncClient(transport=ASGITransport(app=app), base_url=API_BASE) as client:
        await create_corpus(client, corpus)
        body = await remember(
            client,
            corpus,
            statement="Headline: Summer of Speed",
            key="spring.headline",
            scope="campaigns/spring",
            state="rejected",
        )
        assert body["event"] == "NEW", body
        response = await client.post(
            "/api/memory/pack",
            json={"corpus": corpus, "query": ""},
        )
        assert response.status_code == 200, response.text
    assert "Summer of Speed" not in response.text
    assert response.json().get("withheld_counts", {}).get("rejected") == 1


@pytest.mark.asyncio
async def test_include_states_rejected_shows_rejected_claim():
    corpus = corpus_name()
    async with AsyncClient(transport=ASGITransport(app=app), base_url=API_BASE) as client:
        await create_corpus(client, corpus)
        await remember(
            client,
            corpus,
            statement="Headline: Summer of Speed",
            key="spring.headline",
            state="rejected",
        )
        response = await client.post(
            "/api/memory/pack",
            json={"corpus": corpus, "query": "", "include_states": ["rejected"]},
        )
        assert response.status_code == 200, response.text
        body = response.json()
    claims = body.get("current_memories", [])
    found = next((c for c in claims if "Summer of Speed" in c.get("claim", "")), None)
    assert found is not None, claims
    assert found.get("info_state") == "rejected", found


@pytest.mark.asyncio
async def test_rejecting_the_same_key_modifies_it():
    corpus = corpus_name()
    async with AsyncClient(transport=ASGITransport(app=app), base_url=API_BASE) as client:
        await create_corpus(client, corpus)
        first = await remember(
            client,
            corpus,
            statement="Never use the word curated in copy.",
            key="brand.rule.curated",
        )
        assert first["event"] == "NEW", first
        second = await remember(
            client,
            corpus,
            statement="Never use the word curated in copy.",
            key="brand.rule.curated",
            state="rejected",
        )
        assert second["event"] == "MODIFIED", second
        path = second["path"]
        current = await read(client, "current", corpus)
        assert not any("curated" in c.get("claim", "") for c in current.get("current_memories", []))
        assert current.get("withheld_counts", {}).get("rejected") == 1
        history = await read(client, "history", corpus, path=path)
    changes = history.get("changes", [])
    assert len(changes) == 2, changes
    assert "info_state" not in changes[0]["current"][0], changes[0]
    assert changes[1]["current"][0].get("info_state") == "rejected", changes[1]
    assert changes[1]["observed_at"] > changes[0]["observed_at"], changes


# ---------------------------------------------------------------------------
# 4-5: idempotency and modification without a scope
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_same_statement_key_scope_state_twice_is_unchanged():
    corpus = corpus_name()
    async with AsyncClient(transport=ASGITransport(app=app), base_url=API_BASE) as client:
        await create_corpus(client, corpus)
        first = await remember(
            client,
            corpus,
            statement="Headline: Summer of Speed",
            key="headline",
            scope="campaigns/spring",
            state="rejected",
        )
        assert first["event"] == "NEW", first
        path = first["path"]
        second = await remember(
            client,
            corpus,
            statement="Headline: Summer of Speed",
            key="headline",
            scope="campaigns/spring",
            state="rejected",
        )
        assert second["event"] == "UNCHANGED", second
        history = await read(client, "history", corpus, path=path)
    assert len(history.get("changes", [])) == 1, history


@pytest.mark.asyncio
async def test_rejecting_without_scope_modifies_it():
    corpus = corpus_name()
    async with AsyncClient(transport=ASGITransport(app=app), base_url=API_BASE) as client:
        await create_corpus(client, corpus)
        first = await remember(
            client,
            corpus,
            statement="Never use the word curated in copy.",
            key="brand.rule.curated",
        )
        assert first["event"] == "NEW", first
        second = await remember(
            client,
            corpus,
            statement="Never use the word curated in copy.",
            key="brand.rule.curated",
            state="rejected",
        )
        assert second["event"] == "MODIFIED", second


# ---------------------------------------------------------------------------
# 6-7: validation errors
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_superseded_state_is_refused():
    corpus = corpus_name()
    async with AsyncClient(transport=ASGITransport(app=app), base_url=API_BASE) as client:
        await create_corpus(client, corpus)
        response = await client.post(
            "/api/memory/remember",
            json={
                "corpus": corpus,
                "statement": "A statement at least twelve chars.",
                "key": "k1",
                "state": "superseded",
            },
        )
    assert response.status_code == 422, response.text
    assert response.json()["detail"]["code"] == "invalid_state"


@pytest.mark.asyncio
@pytest.mark.parametrize("scope", ["../etc", "/abs", "a//b", "a/./b", "a/b/"])
async def test_bad_scopes_are_refused(scope):
    corpus = corpus_name()
    async with AsyncClient(transport=ASGITransport(app=app), base_url=API_BASE) as client:
        await create_corpus(client, corpus)
        response = await client.post(
            "/api/memory/remember",
            json={
                "corpus": corpus,
                "statement": "A statement at least twelve chars.",
                "key": "k1",
                "scope": scope,
            },
        )
    assert response.status_code == 422, response.text
    assert response.json()["detail"]["code"] == "invalid_scope"


# ---------------------------------------------------------------------------
# 8-10: scope behaviour
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_scope_prefixes_path_without_state():
    corpus = corpus_name()
    async with AsyncClient(transport=ASGITransport(app=app), base_url=API_BASE) as client:
        await create_corpus(client, corpus)
        body = await remember(
            client,
            corpus,
            statement="Headline: Summer of Speed",
            key="spring.headline",
            scope="campaigns/spring",
        )
    assert body["path"] == "campaigns/spring/" + memory_path(body["key"]), body


@pytest.mark.asyncio
async def test_no_state_no_scope_is_today_exactly():
    # G: approved output is byte-identical to main: no status line, no info_state.
    statement = "Our voice is warm and consistent."
    key = "brand.voice"
    corpus = corpus_name()
    async with AsyncClient(transport=ASGITransport(app=app), base_url=API_BASE) as client:
        await create_corpus(client, corpus)
        body = await remember(client, corpus, statement=statement, key=key)
    assert body["event"] == "NEW", body
    assert body["path"] == memory_path(body["key"]), body
    from api.services.remember import render_statement

    rendered = render_statement(statement, key)
    assert "status:" not in rendered
    assert "info_state" not in rendered


@pytest.mark.asyncio
async def test_scope_isolates_the_same_key():
    corpus = corpus_name()
    async with AsyncClient(transport=ASGITransport(app=app), base_url=API_BASE) as client:
        await create_corpus(client, corpus)
        await remember(
            client,
            corpus,
            statement="Headline: Summer of Speed",
            key="headline",
            scope="campaigns/spring",
        )
        await remember(
            client,
            corpus,
            statement="Headline: Autumn Glow Sale",
            key="headline",
            scope="campaigns/autumn",
        )
        current = await read(client, "current", corpus)
    texts = [c.get("claim", "") for c in current.get("current_memories", [])]
    assert "Headline: Summer of Speed" in texts, texts
    assert "Headline: Autumn Glow Sale" in texts, texts


# ---------------------------------------------------------------------------
# 11: SDK file + state/scope is a local ValueError
# ---------------------------------------------------------------------------


def test_file_with_state_raises_value_error():
    corpus = corpus_name()
    from mindpalace_sdk import MindPalace

    mp = MindPalace(corpus, base_url="http://127.0.0.1:9")
    with pytest.raises(ValueError):
        mp.memory.remember(
            statement="Headline: Summer of Speed",
            key="k",
            file="note.md",
            state="rejected",
        )


# ---------------------------------------------------------------------------
# 12: runtime dispatch forwards state and scope
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_runtime_dispatch_forwards_state_and_scope():
    import api.runtime

    corpus = corpus_name()
    async with AsyncClient(transport=ASGITransport(app=app), base_url=API_BASE) as client:
        await create_corpus(client, corpus)
        result = await api.runtime._dispatch_scoped(
            "remember",
            {
                "corpus": corpus,
                "statement": "Call to action: Ride now",
                "key": "spring.cta",
                "scope": "campaigns/spring",
                "state": "draft",
            },
            None,
        )
        assert result["path"].startswith("campaigns/spring/memory/"), result
        response = await client.post("/api/memory/pack", json={"corpus": corpus, "query": ""})
        assert response.status_code == 200, response.text
    assert response.json().get("withheld_counts", {}).get("draft") == 1


# ---------------------------------------------------------------------------
# 13: CLI forwards --state and --scope
# ---------------------------------------------------------------------------


def test_cli_remember_records_state_and_scope():
    corpus = corpus_name()
    from mindpalace_sdk import MindPalace

    MindPalace(corpus)
    result = _invoke(
        [
            "remember",
            "Headline: Summer of Speed",
            "--key",
            "spring.headline",
            "--state",
            "rejected",
            "--scope",
            "campaigns/spring",
            "--corpus",
            corpus,
            "--json",
        ]
    )
    assert result.exit_code == 0, result.output
    assert "campaigns/spring/memory/" in result.stdout, result.stdout


# ---------------------------------------------------------------------------
# 14-16: scoped keys round-trip and scope topic_key
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_scoped_key_round_trip_is_idempotent():
    corpus = corpus_name()
    async with AsyncClient(transport=ASGITransport(app=app), base_url=API_BASE) as client:
        await create_corpus(client, corpus)
        first = await remember(
            client,
            corpus,
            statement="Headline: Summer of Speed",
            key="spring.headline",
            scope="campaigns/spring",
        )
        assert first["key"] == "campaigns/spring/spring.headline", first
        assert first["path"] == "campaigns/spring/" + memory_path(
            "campaigns/spring/spring.headline"
        ), first
        second = await remember(
            client,
            corpus,
            statement="Headline: Summer of Speed",
            key=first["key"],
            scope="campaigns/spring",
        )
        assert second["event"] == "UNCHANGED", second
        assert second["path"] == first["path"], second


@pytest.mark.asyncio
async def test_scoped_topic_key_is_retired_by_scope():
    corpus = corpus_name()
    async with AsyncClient(transport=ASGITransport(app=app), base_url=API_BASE) as client:
        await create_corpus(client, corpus)
        body = await remember(
            client,
            corpus,
            statement="Headline: Summer of Speed",
            key="spring.headline",
            scope="campaigns/spring",
            topic_key="spring.headline",
        )
        assert body["key"] == "campaigns/spring/spring.headline", body


def test_scoped_key_unit_checks():
    from api.services.remember import scoped_key

    assert scoped_key(None, "k") == "k"
    assert scoped_key("a/b", "k") == "a/b/k"
    assert scoped_key("a/b", "a/b/k") == "a/b/k"
    assert scoped_key("a/b", "a/bk") == "a/b/a/bk"


# ---------------------------------------------------------------------------
# 17-19: supersession input errors are 422, not 500
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_unknown_topic_key_is_422():
    corpus = corpus_name()
    async with AsyncClient(transport=ASGITransport(app=app), base_url=API_BASE) as client:
        await create_corpus(client, corpus)
        response = await client.post(
            "/api/memory/remember",
            json={
                "corpus": corpus,
                "statement": "Our voice is warm and consistent.",
                "key": "brand.voice",
                "topic_key": "brand.tone",
            },
        )
    assert response.status_code == 422, response.text
    detail = response.json()["detail"]
    assert detail["code"] == "invalid_request", detail
    assert "topic_key" in detail["message"], detail
    assert detail["message"].startswith("topic_key"), detail


@pytest.mark.asyncio
async def test_unknown_full_claim_id_is_422():
    corpus = corpus_name()
    async with AsyncClient(transport=ASGITransport(app=app), base_url=API_BASE) as client:
        await create_corpus(client, corpus)
        response = await client.post(
            "/api/memory/remember",
            json={
                "corpus": corpus,
                "statement": "Our voice is calm and friendly now.",
                "key": "brand.voice2",
                "supersedes_claim_id": "0" * 64,
            },
        )
    assert response.status_code == 422, response.text
    assert response.json()["detail"]["code"] == "invalid_request"
    assert response.json()["detail"]["message"].startswith("supersedes_claim_id")


@pytest.mark.asyncio
async def test_topic_key_with_several_active_claims_is_422():
    corpus = corpus_name()
    async with AsyncClient(transport=ASGITransport(app=app), base_url=API_BASE) as client:
        await create_corpus(client, corpus)
        # ingest two documents authoring the same claim key
        r1 = await client.post(
            "/api/ingest/file",
            json={
                "corpus": corpus,
                "path": "brand/tone-a.md",
                "content": (
                    "---\n"
                    "claims:\n"
                    "  - key: brand.tone\n"
                    '    claim: "Tone is warm and steady."\n'
                    "    value: warm\n"
                    '    evidence: "Tone is warm and steady."\n'
                    "---\n"
                    "\n"
                    "# Tone A\n"
                    "\n"
                    "Tone is warm and steady.\n"
                ),
            },
        )
        assert r1.status_code == 200, r1.text
        r2 = await client.post(
            "/api/ingest/file",
            json={
                "corpus": corpus,
                "path": "brand/tone-b.md",
                "content": (
                    "---\n"
                    "claims:\n"
                    "  - key: brand.tone\n"
                    '    claim: "Tone is bold and direct."\n'
                    "    value: bold\n"
                    '    evidence: "Tone is bold and direct."\n'
                    "---\n"
                    "\n"
                    "# Tone B\n"
                    "\n"
                    "Tone is bold and direct.\n"
                ),
            },
        )
        assert r2.status_code == 200, r2.text
        response = await client.post(
            "/api/memory/remember",
            json={
                "corpus": corpus,
                "statement": "Tone is calm and friendly now.",
                "key": "brand.tone",
            },
        )
    assert response.status_code == 422, response.text
    detail = response.json()["detail"]
    assert detail["code"] == "invalid_request", detail
    assert "brand.tone" in detail["message"], detail
    assert detail["message"].startswith("topic_key"), detail


@pytest.mark.asyncio
async def test_supersedes_phrase_with_no_match_is_422():
    corpus = corpus_name()
    async with AsyncClient(transport=ASGITransport(app=app), base_url=API_BASE) as client:
        await create_corpus(client, corpus)
        response = await client.post(
            "/api/memory/remember",
            json={
                "corpus": corpus,
                "statement": "Our voice is calm and friendly now.",
                "key": "brand.voice2",
                "supersedes_claim_id": "zebra giraffe platypus",
            },
        )
    assert response.status_code == 422, response.text
    assert response.json()["detail"]["code"] == "invalid_request"


@pytest.mark.asyncio
async def test_supersedes_phrase_with_several_matches_is_422():
    corpus = corpus_name()
    async with AsyncClient(transport=ASGITransport(app=app), base_url=API_BASE) as client:
        await create_corpus(client, corpus)
        first = await remember(
            client,
            corpus,
            statement="Our voice is warm and consistent.",
            key="voice.a",
        )
        assert first["event"] == "NEW", first
        second = await remember(
            client,
            corpus,
            statement="Our voice is warm and very consistent.",
            key="voice.b",
        )
        assert second["event"] == "NEW", second
        response = await client.post(
            "/api/memory/remember",
            json={
                "corpus": corpus,
                "statement": "Our voice is bold and direct always.",
                "key": "voice.c",
                "supersedes_claim_id": "voice warm consistent",
            },
        )
    assert response.status_code == 422, response.text
    assert response.json()["detail"]["code"] == "invalid_request"


def _invoke(args, base_url_env: str | None = None):
    from cli.main import app

    env = dict(os.environ)
    env["MIND_PALACE_RUNTIME"] = "0"
    env.pop("MIND_PALACE_BASE_URL", None)
    if base_url_env is not None:
        env["MIND_PALACE_BASE_URL"] = base_url_env
    from typer.testing import CliRunner

    return CliRunner().invoke(app, args, env=env)
