"""Oracle tests for the M018 sealed benchmark harness (D1).

These tests use the fixture generator to build placement records and verify
that:
  * Oracle context (all gold items present) scores rule_recall=1.0, violation=0
  * Stale oracle context (superseded/draft markers present) scores violation=1
  * Filler context scores 0 recall on all metrics
  * B* (brand-guide document only) equals the generator's guide placement

Per-brand oracle scores are printed as part of the test output.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import pytest

from benchmarks.agency_readiness.checker import score
from benchmarks.agency_readiness.fixture_generator import (
    HARNESS_DEV_FIXTURES_SEED,
    generate_brand,
    write_fixtures,
)


def _load_spec():
    spec_path = ROOT / "benchmarks" / "agency_readiness" / "sealed_spec.json"
    return json.loads(spec_path.read_text())


def _generated(brand_name):
    return generate_brand(brand_name, _load_spec(), HARNESS_DEV_FIXTURES_SEED)


def _oracle_context(result: dict) -> str:
    return "\n".join(p["sentence"] for p in result["placements"] if p["status"] == "approved")


def _stale_oracle_context(result: dict) -> str:
    markers = result["gold"]["superseded_v1_markers"] + result["gold"]["unapproved_draft_markers"]
    return "\n".join(
        sentence
        for body in result["docs"].values()
        for sentence in body.replace("\n", " ").split(". ")
        if any(marker in sentence for marker in markers)
    )


def _filler_context(result: dict) -> str:
    return "\n".join(
        body for path, body in result["docs"].items() if result["template_of"][path] == "filler"
    )


@pytest.mark.parametrize("brand_name", ["northstar", "voltedge", "hearth_and_loom"])
def test_oracle_context_scores_perfect_recall_no_violation(brand_name):
    """Oracle context (all gold items) scores rule_recall=1.0, violation=0."""
    spec = _load_spec()
    generated = _generated(brand_name)
    gold = generated["gold"]
    ctx = _oracle_context(generated)
    result = score(ctx, gold)
    print(f"\nOracle {brand_name}: rule_recall={result['rule_recall']}, "
          f"voice_recall={result['voice_recall']}, spec_recall={result['spec_recall']}, "
          f"violation={result['violation']}, stale_hits={result['stale_hits']}")
    assert result["rule_recall"] == 1.0, f"{brand_name}: rule_recall {result['rule_recall']} != 1.0"
    assert result["voice_recall"] == 1.0, f"{brand_name}: voice_recall {result['voice_recall']} != 1.0"
    assert result["spec_recall"] == 1.0, f"{brand_name}: spec_recall {result['spec_recall']} != 1.0"
    assert result["violation"] == 0, f"{brand_name}: violation {result['violation']} != 0"
    assert result["stale_hits"] == 0, f"{brand_name}: stale_hits {result['stale_hits']} != 0"


@pytest.mark.parametrize("brand_name", ["northstar", "voltedge", "hearth_and_loom"])
def test_stale_oracle_scores_violation_one(brand_name):
    """Stale oracle context (superseded/draft markers) scores violation=1."""
    spec = _load_spec()
    generated = _generated(brand_name)
    gold = generated["gold"]
    ctx = _stale_oracle_context(generated)
    result = score(ctx, gold)
    print(f"\nStale oracle {brand_name}: violation={result['violation']}, "
          f"stale_hits={result['stale_hits']}, rule_recall={result['rule_recall']}")
    assert result["violation"] == 1, f"{brand_name}: violation {result['violation']} != 1"
    assert result["stale_hits"] >= 1, f"{brand_name}: stale_hits {result['stale_hits']} < 1"


@pytest.mark.parametrize("brand_name", ["northstar", "voltedge", "hearth_and_loom"])
def test_filler_context_scores_zero_recall(brand_name):
    """Filler context (no gold items) scores 0 recall on all metrics."""
    spec = _load_spec()
    generated = _generated(brand_name)
    gold = generated["gold"]
    ctx = _filler_context(generated)
    result = score(ctx, gold)
    print(f"\nFiller {brand_name}: rule_recall={result['rule_recall']}, "
          f"voice_recall={result['voice_recall']}, spec_recall={result['spec_recall']}, "
          f"violation={result['violation']}")
    assert result["rule_recall"] == 0.0, f"{brand_name}: rule_recall {result['rule_recall']} != 0.0"
    assert result["voice_recall"] == 0.0, f"{brand_name}: voice_recall {result['voice_recall']} != 0.0"
    assert result["spec_recall"] == 0.0, f"{brand_name}: spec_recall {result['spec_recall']} != 0.0"
    assert result["violation"] == 0, f"{brand_name}: violation {result['violation']} != 0"


@pytest.mark.parametrize("brand_name", ["northstar", "voltedge", "hearth_and_loom"])
def test_b_star_equals_generator_guide_placement(brand_name):
    """B* (brand-guide document only) equals the generator's guide placement.

    The guide document is headings.md (template='guide'). B* context should
    contain exactly what headings.md contains -- no more, no less.
    """
    result = _generated(brand_name)
    docs = result["docs"]
    gold = result["gold"]

    guide_rel = next(
        rel
        for rel, metadata in result["meta"].items()
        if metadata.get("document_type") == "guide" and rel.endswith("headings.md")
    )
    guide_body = docs.get(guide_rel)
    assert guide_body is not None, f"{brand_name}: no guide document found"

    # B* context is the guide document only
    b_star_ctx = guide_body

    b_star_result = score(b_star_ctx, gold)
    print(f"\nB* {brand_name}: rule_recall={b_star_result['rule_recall']}, "
          f"voice_recall={b_star_result['voice_recall']}, spec_recall={b_star_result['spec_recall']}, "
          f"violation={b_star_result['violation']}")

    expected = {}
    for category, key in (("rule", "standing_prohibitions"), ("voice", "required_voice_descriptors"), ("spec", "approved_specs")):
        total = len(gold[key])
        placed = {
            p["sentence"]
            for p in result["placements"]
            if p["category"] == category and p["path"] == guide_rel
        }
        expected[category] = len(placed) / total if total else 1.0
    print(f"B* {brand_name}: selected={guide_rel}, expected={expected}")
    assert b_star_result["rule_recall"] == expected["rule"]
    assert b_star_result["voice_recall"] == expected["voice"]
    assert b_star_result["spec_recall"] == expected["spec"]
    assert b_star_result["violation"] == 0, (
        f"{brand_name}: B* violation {b_star_result['violation']} != 0"
    )


def test_oracle_per_brand_scores():
    """Print per-brand oracle scores for the record."""
    spec = _load_spec()
    print("\n=== Per-brand oracle scores ===")
    for brand_name in ["northstar", "voltedge", "hearth_and_loom"]:
        generated = _generated(brand_name)
        gold = generated["gold"]
        ctx = _oracle_context(generated)
        result = score(ctx, gold)
        print(f"  {brand_name}: rule_recall={result['rule_recall']}, "
              f"voice_recall={result['voice_recall']}, "
              f"spec_recall={result['spec_recall']}, "
              f"violation={result['violation']}, "
              f"stale_hits={result['stale_hits']}")
