"""Claim state comes from frontmatter and headings, never body text.

A brand guide that says "Do not use the word curated" must stay approved: body
text never sets state. State comes only from frontmatter `status`/`state`/`tags`
and from headings above a claim, combined most-restrictive-wins.
"""

from __future__ import annotations

import os

import pytest

from api.services.markdown_claims import (
    assign_states,
    detect_info_state,
    document_state,
    extract,
)
from api.services.parser import (
    chunk_with_heading_paths,
    extract_sections_with_paths,
)


def chunks_for(body):
    return [
        {**chunk, "order_index": index}
        for index, chunk in enumerate(chunk_with_heading_paths(extract_sections_with_paths(body)))
    ]


def claimed(body, metadata=None):
    """Extract claims from `body` and assign states from `metadata`."""
    metadata = metadata or {}
    claims, _warnings = extract(body, chunks_for(body))
    assign_states(claims, metadata)
    return claims


def find_claim(claims, word):
    for claim in claims:
        if word.lower() in claim["claim"].lower():
            return claim
    raise AssertionError(f"no claim containing {word!r}; claims: {[c['claim'] for c in claims]}")


def state_of(claims, word):
    return find_claim(claims, word).get("info_state", "approved")


# ---------------------------------------------------------------------------
# 1-4: body text never sets state
# ---------------------------------------------------------------------------


def test_approved_frontmatter_keeps_body_words_from_setting_state():
    # #1: "curated" and "bespoke" appear only in the body; status=approved wins.
    metadata = {"status": "approved"}
    body = '## Approved\n\n- Do not use the words "curated" or "bespoke."\n'
    claims = claimed(body, metadata)
    assert state_of(claims, "curated") == "approved"


def test_body_word_draft_does_not_make_a_claim_draft():
    # #2: "draft" appears only in the body, with no frontmatter at all.
    claims = claimed("- We draft emails every Monday.\n")
    assert state_of(claims, "draft") == "approved"


def test_heading_above_claim_sets_its_state():
    # #3: "Voice" stays approved; "Brainstorming" makes its claim draft.
    body = (
        "## Voice\n\n"
        "- Our voice is warm.\n\n"
        "## Brainstorming\n\n"
        "- Try a playful tone again.\n"
    )
    claims = claimed(body)
    assert state_of(claims, "voice is warm") == "approved"
    assert state_of(claims, "playful tone") == "draft"


def test_do_not_use_heading_is_rules_not_state():
    # #4: "Do not use" is a list of rules, so both claims stay approved.
    body = (
        "## Do not use\n\n"
        '- Never use the word "curated" in copy.\n'
        '- Never call a product "bespoke" in ads.\n'
    )
    claims = claimed(body)
    assert len(claims) == 2, claims
    for claim in claims:
        assert claim.get("info_state", "approved") == "approved"


# ---------------------------------------------------------------------------
# 5-8: frontmatter rules
# ---------------------------------------------------------------------------


def test_status_draft_makes_every_claim_draft():
    # #5
    claims = claimed(
        "- The claim is on the record.\n- Another claim is here too.\n", {"status": "Draft"}
    )
    assert len(claims) == 2, claims
    assert all(claim.get("info_state", "approved") == "draft" for claim in claims)


def test_status_final_is_approved_with_no_warnings():
    # #6
    state, warnings = document_state({"status": "final"})
    assert state == "approved"
    assert warnings == []


def test_unknown_status_warns_and_approves():
    # #7
    state, warnings = document_state({"status": "banana"})
    assert state == "approved"
    assert warnings == ["unknown_status:banana"]


def test_title_never_sets_state():
    # #8: "title" is not status/state/tags, so it is silent.
    state, warnings = document_state({"title": "Rejected ideas archive"})
    assert state == "approved"
    assert warnings == []


# ---------------------------------------------------------------------------
# 9-12: headings and most-restrictive-wins
# ---------------------------------------------------------------------------


def test_rejected_heading_makes_claim_rejected():
    # #9
    body = "## Rejected: summer slogans\n\n" "### Option A\n\n" "- Feel the heat\n"
    claims = claimed(body)
    assert state_of(claims, "Feel the heat") == "rejected"


def test_most_restrictive_wins_over_approved_heading():
    # #10: document rejected overrides an "Approved" heading.
    claims = claimed("## Approved\n\n- X is the only approved path.\n", {"status": "rejected"})
    assert state_of(claims, "approved path") == "rejected"


def test_tags_draft_makes_claim_draft():
    # #11
    claims = claimed("- A claim is on the record.\n", {"tags": ["draft"]})
    assert len(claims) == 1, claims
    assert claims[0].get("info_state", "approved") == "draft"


def test_past_campaigns_heading_is_historical_observation():
    # #12
    body = "## Past campaigns\n\n- Spring 2025 used a playful tone.\n"
    claims = claimed(body)
    assert state_of(claims, "Spring 2025") == "historical_observation"


# ---------------------------------------------------------------------------
# 13: real ingest route
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_claim_state_survives_the_ingest_route():
    # #13: test 3's document through POST /api/ingest/file, into a corpus the
    # test creates itself through the API (rule A1).
    url = os.environ["DATABASE_URL"]
    async_url = (
        url.replace("postgresql+psycopg://", "postgresql+asyncpg://")
        .replace("postgresql+psycopg2://", "postgresql+asyncpg://")
        .replace("postgresql://", "postgresql+asyncpg://")
    )

    from uuid import uuid4

    from sqlalchemy.ext.asyncio import create_async_engine
    from sqlalchemy.pool import NullPool
    from sqlalchemy import text

    engine = create_async_engine(async_url, poolclass=NullPool)
    try:
        async with engine.connect() as conn:
            await conn.execute(text("SELECT 1 FROM corpora LIMIT 1"))
    finally:
        await engine.dispose()

    name = f"t{uuid4().hex[:12]}"
    body = (
        "# Guide\n\n"
        "## Voice\n\n"
        "- Our voice is warm.\n\n"
        "## Brainstorming\n\n"
        "- Try a playful tone again.\n"
    )
    from httpx import ASGITransport, AsyncClient

    from api.main import app

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        corpus_response = await client.post(
            "/api/corpora",
            json={"name": name},
        )
        assert corpus_response.status_code == 201, corpus_response.text
        ingest_response = await client.post(
            "/api/ingest/file",
            json={"content": body, "path": "brand/guide.md", "corpus": name},
        )
        assert ingest_response.status_code == 200, ingest_response.text

    engine = create_async_engine(async_url, poolclass=NullPool)
    try:
        async with engine.connect() as conn:
            rows = (
                await conn.execute(
                    text(
                        "SELECT c.claim, c.info_state "
                        "FROM memory_claims c "
                        "JOIN memory_documents d ON d.id = c.memory_document_id "
                        "JOIN corpora k ON k.id = d.corpus_id "
                        "WHERE k.name = :name AND d.path = :p"
                    ),
                    {"name": name, "p": "brand/guide.md"},
                )
            ).all()
    finally:
        await engine.dispose()

    states = {claim: info_state for claim, info_state in rows}
    voice = next(value for claim, value in states.items() if "voice is warm" in claim)
    brainstorm = next(value for claim, value in states.items() if "playful tone" in claim)
    assert voice == "approved"
    assert brainstorm == "draft"


# ---------------------------------------------------------------------------
# Adopted from the M018 audit regressions (issue #157, section 9)
# ---------------------------------------------------------------------------


def test_approved_prohibition_is_extracted():
    body = '## Approved\n\nDo not use the words "curated" or "bespoke."\n'
    meta = {"status": "approved"}
    assert detect_info_state(meta, body) == "approved"
    chunks = [{"text": body, "order_index": 0}]
    claims, _ = extract(body, chunks)
    assert any("curated" in c["claim"] for c in claims)


def test_plain_prose_is_not_draft():
    assert detect_info_state({}, "We draft emails every Monday.") != "draft"
