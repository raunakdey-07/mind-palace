"""Smoke test for the repaired M018 benchmark harness.

Runs the harness condition functions on a tiny in-memory fixture and asserts the
output schema. No product code under api/cli/migrations is changed by this test.
"""

from __future__ import annotations

import asyncio
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from benchmarks.agency_readiness.checker import score as score_context, normalize
from benchmarks.agency_readiness.evaluate import (
    count_tokens,
    truncate_words,
    condition_A,
    condition_B,
    condition_B_plus,
    condition_C,
    condition_C_plus,
)

GOLD = {
    "standing_prohibitions": ["curated"],
    "required_voice_descriptors": ["warm a little dry and confident"],
    "approved_specs": ["80% grs certified recycled polyester"],
    "superseded_v1_markers": ["65% recycled polyester"],
    "unapproved_draft_markers": ["try a playful tone again"],
}


def test_count_tokens_and_truncate():
    assert count_tokens("one two three") == 3
    assert count_tokens("") == 0
    long_text = " ".join(["word"] * 2000)
    assert count_tokens(truncate_words(long_text, 1200)) == 1200


def test_normalize_and_score_schema():
    assert not normalize("cure") == normalize("curated")
    m = score_context("Do not use the word curated.", GOLD)
    assert set(m) == {"rule_recall", "voice_recall", "spec_recall", "violation", "stale_hits"}
    assert m["rule_recall"] == 1.0
    assert m["violation"] == 0
    empty = score_context("", GOLD)
    assert empty["rule_recall"] == 0.0


async def _run_conditions():
    ctx_a = await condition_A("Write a launch tweet.")
    docs = [("rel/approved.md", "Do not use the word curated in any copy. The material is 80% GRS certified recycled polyester. Our voice is warm a little dry and confident.", "approved")]
    ctx_b = await condition_B(docs, "Write a launch tweet.")
    ctx_bp = await condition_B_plus(docs, "Write a launch tweet.")
    return ctx_a, ctx_b, ctx_bp


def test_conditions_return_schema():
    ctx_a, ctx_b, ctx_bp = asyncio.run(_run_conditions())
    assert "Write a launch tweet." in ctx_a
    assert ctx_b  # non-empty
    assert ctx_bp  # non-empty
    ma = score_context(ctx_a, GOLD)
    mb = score_context(ctx_b, GOLD)
    assert set(ma) == {"rule_recall", "voice_recall", "spec_recall", "violation", "stale_hits"}
    assert set(mb) == {"rule_recall", "voice_recall", "spec_recall", "violation", "stale_hits"}
    # B contains the prohibition -> rule recalled
    assert mb["rule_recall"] == 1.0


def test_c_plus_filters_draft():
    class R:
        def __init__(self, text, source_path):
            self.text = text
            self.source_path = source_path
    draft = R("try a playful tone again.", "rel/draft.md")
    approved = R("Do not use the word curated.", "rel/approved.md")
    path_status = {"rel/draft.md": "draft", "rel/approved.md": "approved"}
    ctx = asyncio.run(condition_C_plus([draft, approved], "q", path_status))
    assert "curated" in ctx
    assert "playful tone" not in ctx
