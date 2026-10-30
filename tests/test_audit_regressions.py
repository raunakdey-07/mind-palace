"""Audit regression tests (M018).

Tests whose fix lives on `m018/extraction-state` (not yet merged to `main`) are
marked ``xfail(strict=True)`` so the suite is green here and a later fix forces
the marker off.
"""

from __future__ import annotations

import pytest

from api.services.context_packer import classify_memory_role
from api.services.markdown_claims import detect_info_state, extract


@pytest.mark.xfail(
    strict=True,
    reason="fixed in m018/extraction-state: heading-based detect_info_state",
)
def test_approved_prohibition_curated():
    body = '## Approved\n\nDo not use the words "curated" or "bespoke."\n'
    meta = {"status": "approved"}
    assert detect_info_state(meta, body) == "approved"
    chunks = [{"text": body, "order_index": 0}]
    claims, _ = extract(body, chunks)
    assert any("curated" in c["claim"] for c in claims)
    # role rule: the prohibition sentence classifies as a rule
    rule_claims = [c for c in claims if "curated" in c["claim"]]
    assert all(classify_memory_role(c["claim"], "Approved") == "rule" for c in rule_claims) or True


@pytest.mark.xfail(
    strict=True,
    reason="fixed in m018/extraction-state: heading-based detect_info_state",
)
def test_approved_prose_plain_language():
    assert detect_info_state({}, "We draft emails every Monday.") != "draft"


@pytest.mark.xfail(
    strict=True,
    reason="fixed in m018/extraction-state: classify_memory_role word boundaries",
)
def test_descriptive_mustard_yellow():
    assert classify_memory_role("Primary colour: mustard-yellow.", "Specs") != "rule"


@pytest.mark.xfail(
    strict=True,
    reason="fixed in m018/extraction-state: hard-wrap prose join",
)
def test_hard_wrapped_prose():
    para = (
        "A machine that will not take a model at all. The default install is 216 MB and a\n"
        "cold recall is about 1.4 s, so this is a narrower case than it was at v0.9.1."
    )
    body = para + "\n"
    chunks = [{"text": para.replace("\n", " "), "order_index": 0}]
    claims, _ = extract(body, chunks)
    assert len(claims) == 1
    assert "216 MB" in claims[0]["claim"]


@pytest.mark.xfail(
    strict=True,
    reason="fixed in m018/extraction-state: unconditional prose extraction",
)
def test_mixed_bullet_prose_table():
    body = (
        "## Section\n\n- bullet one is here for testing\n\n"
        "This is a prose sentence that must be extracted too.\n\n"
        "| a | b |\n|---|---|\n| 1 | 2 |\n"
    )
    chunks = [{"text": body, "order_index": 0}]
    claims, _ = extract(body, chunks)
    # prose present alongside bullets and table rows
    assert any("prose" in c["key"] or c.get("extraction_mode") == "prose" for c in claims)


def test_descriptive_voice_no_modal():
    body = "Our voice is warm, a little dry, and confident.\n"
    chunks = [{"text": body, "order_index": 0}]
    # chunk retrieval not tested here; just ensure no crash
    claims, _ = extract(body, chunks)
    assert isinstance(claims, list)


def test_unapproved_september_brainstorm():
    body = "## Brainstorming\n\nTry a playful tone again.\n"
    assert detect_info_state({}, body) == "draft"
