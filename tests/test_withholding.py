"""Draft, rejected and superseded claims are withheld from current and pack.

history and query are unchanged: history is the audit trail, and query is the
raw retrieval surface for this release. Withholding is opt-in per state via
include_states. A response with nothing withheld is byte-identical to main.
"""

from __future__ import annotations

from uuid import uuid4

import pytest
from httpx import ASGITransport, AsyncClient

from api.main import app

API_BASE = "http://test"

WITHHELD_STATES = {"draft", "rejected", "superseded"}


def corpus_name() -> str:
    return f"t{uuid4().hex[:12]}"


async def _create_corpus(client, name: str) -> None:
    response = await client.post("/api/corpora", json={"name": name})
    assert response.status_code == 201, response.text


async def _ingest(client, corpus: str, path: str, content: str) -> None:
    response = await client.post(
        "/api/ingest/file",
        json={"content": content, "path": path, "corpus": corpus},
    )
    assert response.status_code == 200, response.text


async def _memory(client, operation: str, corpus: str, **fields) -> dict:
    response = await client.post(
        f"/api/memory/{operation}",
        json={"corpus": corpus, "query": "", **fields},
    )
    assert response.status_code == 200, response.text
    return response.json()


async def _setup(client, corpus: str, path: str, content: str) -> None:
    await _create_corpus(client, corpus)
    await _ingest(client, corpus, path, content)


# ---------------------------------------------------------------------------
# 1-3: withholding removes draft / rejected / superseded from pack and current
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_pack_withholds_draft_claim():
    # #1: PR-1 test 3's document; pack keeps the approved voice claim and
    # withholds the draft brainstorm claim.
    corpus = corpus_name()
    path = "brand/guide.md"
    content = (
        "# Brand Guide\n\n"
        "## Voice\n\n"
        "- Our voice is warm and consistent.\n\n"
        "## Brainstorming\n\n"
        "- Try a playful tone again tomorrow.\n"
    )
    async with AsyncClient(transport=ASGITransport(app=app), base_url=API_BASE) as client:
        await _setup(client, corpus, path, content)
        body = await _memory(client, "pack", corpus)
    text = str(body)
    assert "Our voice is warm and consistent" in text
    assert "playful tone" not in text
    assert body.get("withheld_counts", {}).get("draft") == 1


@pytest.mark.asyncio
async def test_include_states_draft_keeps_draft_claim():
    # #2: include_states=["draft"] keeps the draft claim; nothing withheld.
    corpus = corpus_name()
    path = "brand/guide.md"
    content = (
        "# Brand Guide\n\n"
        "## Voice\n\n"
        "- Our voice is warm and consistent.\n\n"
        "## Brainstorming\n\n"
        "- Try a playful tone again tomorrow.\n"
    )
    async with AsyncClient(transport=ASGITransport(app=app), base_url=API_BASE) as client:
        await _setup(client, corpus, path, content)
        response = await client.post(
            "/api/memory/pack",
            json={"corpus": corpus, "query": "", "include_states": ["draft"]},
        )
        assert response.status_code == 200, response.text
        body = response.json()
    text = str(body)
    assert "withheld_counts" not in text
    claims = body.get("current_memories", [])
    brainstorm = next(c for c in claims if "playful tone" in c["claim"])
    assert brainstorm["info_state"] == "draft"


@pytest.mark.asyncio
async def test_current_withholds_rejected_claims():
    # #3: a document with status: rejected and three bullets; current returns
    # none of them and counts all three as withheld.
    corpus = corpus_name()
    path = "slogans/rejected.md"
    content = (
        "---\nstatus: rejected\n---\n\n"
        "# Rejected Slogans\n\n"
        "- Feel the heat of summer.\n"
        "- Buy now, pay later, no interest.\n"
        "- Act fast before it is gone forever.\n"
    )
    async with AsyncClient(transport=ASGITransport(app=app), base_url=API_BASE) as client:
        await _setup(client, corpus, path, content)
        body = await _memory(client, "current", corpus)
    assert body.get("current_memories", []) == []
    assert body.get("withheld_counts", {}).get("rejected") == 3


# ---------------------------------------------------------------------------
# 4: rules heading stays approved (guards the no-withholding path)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_do_not_use_heading_stays_approved():
    # #4: "## Do not use" is a list of rules, so the prohibition is present.
    corpus = corpus_name()
    path = "brand/rules.md"
    content = (
        "# Brand Rules\n\n"
        "## Do not use\n\n"
        '- Never use the word "curated" in copy.\n'
        '- Never call a product "bespoke" in ads.\n'
    )
    async with AsyncClient(transport=ASGITransport(app=app), base_url=API_BASE) as client:
        await _setup(client, corpus, path, content)
        body = await _memory(client, "pack", corpus)
    text = str(body)
    assert 'Never use the word "curated" in copy.' in text
    assert 'Never call a product "bespoke" in ads.' in text


# ---------------------------------------------------------------------------
# 5: invalid include_states is rejected
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_include_states_invalid_is_422():
    # #5: include_states ["banana"] raises invalid_state.
    corpus = corpus_name()
    path = "brand/guide.md"
    content = "# Guide\n\n- Our voice is warm and consistent.\n"
    async with AsyncClient(transport=ASGITransport(app=app), base_url=API_BASE) as client:
        await _setup(client, corpus, path, content)
        response = await client.post(
            "/api/memory/pack",
            json={"corpus": corpus, "query": "", "include_states": ["banana"]},
        )
    assert response.status_code == 422, response.text
    assert response.json()["detail"]["code"] == "invalid_state"


# ---------------------------------------------------------------------------
# 6-7: query is unchanged; historical_observation is not withheld
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_query_returns_draft_claim():
    # #6: query is unchanged; the brainstorm claim comes back with info_state
    # "draft".
    corpus = corpus_name()
    path = "brand/guide.md"
    content = (
        "# Brand Guide\n\n"
        "## Voice\n\n"
        "- Our voice is warm and consistent.\n\n"
        "## Brainstorming\n\n"
        "- Try a playful tone again tomorrow.\n"
    )
    async with AsyncClient(transport=ASGITransport(app=app), base_url=API_BASE) as client:
        await _setup(client, corpus, path, content)
        response = await client.post(
            "/api/memory/query",
            json={"corpus": corpus, "query": "playful tone"},
        )
        assert response.status_code == 200, response.text
        body = response.json()
    claims = body.get("current_memories", [])
    brainstorm = next(c for c in claims if "playful tone" in c["claim"])
    assert brainstorm["info_state"] == "draft"


@pytest.mark.asyncio
async def test_historical_observation_not_withheld():
    # #7: historical_observation is not in the withheld set.
    corpus = corpus_name()
    path = "reports/campaigns.md"
    content = (
        "# Campaign Report\n\n"
        "## Past campaigns\n\n"
        "- Spring 2025 used a playful tone throughout.\n"
    )
    async with AsyncClient(transport=ASGITransport(app=app), base_url=API_BASE) as client:
        await _setup(client, corpus, path, content)
        body = await _memory(client, "pack", corpus)
    claims = body.get("current_memories", [])
    claim = next(c for c in claims if "Spring 2025" in c["claim"])
    assert claim["info_state"] == "historical_observation"


# ---------------------------------------------------------------------------
# 8: nothing withheld is byte-identical to main
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_approved_only_no_withheld_counts():
    # #8: a corpus with only approved claims has no withheld_counts and no
    # info_state anywhere in the response.
    corpus = corpus_name()
    path = "brand/guide.md"
    content = "# Brand Guide\n\n" "## Voice\n\n" "- Our voice is warm and consistent.\n"
    async with AsyncClient(transport=ASGITransport(app=app), base_url=API_BASE) as client:
        await _setup(client, corpus, path, content)
        response = await client.post(
            "/api/memory/pack",
            json={"corpus": corpus, "query": ""},
        )
        assert response.status_code == 200, response.text
        text = response.text
    assert "withheld_counts" not in text
    assert "info_state" not in text


# ---------------------------------------------------------------------------
# 9: history is the audit trail and is unchanged
# ---------------------------------------------------------------------------


def _find_claim(body: dict, word: str) -> dict:
    for field in ("current_memories", "historical_memories", "uncertain_memories"):
        for claim in body.get(field, []):
            if word in claim["claim"]:
                return claim
    for change in body.get("changes", []):
        for claim in list(change.get("previous", [])) + list(change.get("current", [])):
            if word in claim["claim"]:
                return claim
    raise AssertionError(f"no claim containing {word!r} in history response")


@pytest.mark.asyncio
async def test_history_keeps_draft_claim():
    # #9: history still returns the brainstorm claim.
    corpus = corpus_name()
    path = "brand/guide.md"
    content = (
        "# Brand Guide\n\n"
        "## Voice\n\n"
        "- Our voice is warm and consistent.\n\n"
        "## Brainstorming\n\n"
        "- Try a playful tone again tomorrow.\n"
    )
    async with AsyncClient(transport=ASGITransport(app=app), base_url=API_BASE) as client:
        await _setup(client, corpus, path, content)
        response = await client.post(
            "/api/memory/history",
            json={"corpus": corpus, "query": "", "path": path},
        )
        assert response.status_code == 200, response.text
        body = response.json()
    brainstorm = _find_claim(body, "playful tone")
    assert brainstorm is not None
