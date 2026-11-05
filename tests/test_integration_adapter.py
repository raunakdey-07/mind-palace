"""Adapter tests for examples/brand-memory-adapter/brand_memory.py.

No LLM calls. Two guarantees:
- context_for degrades to ("", [], False) within 5 s against a closed port.
- A rejected decision is withheld from context_for.
"""

from __future__ import annotations

import os
import sys
import time
from pathlib import Path

import pytest

sys.path.insert(
    0,
    str(Path(__file__).resolve().parents[1] / "examples" / "brand-memory-adapter"),
)

from live_server import live_server  # noqa: E402,F401,F811  fixture


def test_context_for_closed_port_degrades(monkeypatch):
    """context_for returns memory_available=False within 5 s on a dead server."""
    import brand_memory  # noqa: E402

    monkeypatch.setenv("MIND_PALACE_URL", "http://127.0.0.1:19999")
    monkeypatch.setenv("BRAND_MEMORY_KEY", "x")

    start = time.monotonic()
    text, ids, available = brand_memory.context_for("acme", "spring", 16000)
    elapsed = time.monotonic() - start

    assert available is False, "closed port must degrade to no memory"
    assert text == "", "no-memory path must return empty text"
    assert ids == [], "no-memory path must return no claim ids"
    assert elapsed < 5, f"degradation took {elapsed:.2f}s, expected under 5s"


def test_rejected_decision_excluded_from_context(live_server, monkeypatch):
    """record_decision(rejected) then context_for must leave the text out."""
    import brand_memory  # noqa: E402

    monkeypatch.setenv("MIND_PALACE_URL", live_server.url)
    monkeypatch.setenv("BRAND_MEMORY_KEY", live_server.keys["acme"])

    brand_memory.load_documents(
        srv.corpora["acme"],
        [
            (
                "brand/guide.md",
                "# Trailhead brand guide\n\n## Voice\n\n"
                "- Our voice is warm, plain and confident.\n",
            ),
            (
                "campaigns/spring/brief.md",
                "# Campaign brief\n\n## Facts\n\n- The spring launch date is 14 March 2027.\n",
            ),
        ],
    )

    brand_memory.record_decision(
        srv.corpora["acme"], "spring", "spring.headline", "Headline: Ride Into Spring", "rejected"
    )

    text, ids, available = brand_memory.context_for(srv.corpora["acme"], "spring", 16000)
    assert available is True, "live server must answer"
    assert "Ride Into Spring" not in text, f"rejected text leaked: {text!r}"
    assert any(
        "warm, plain and confident" in line for line in text.splitlines()
    ), f"approved voice missing: {text!r}"


def test_record_decision_rejects_invalid_decision(live_server, monkeypatch):
    """record_decision with an invalid decision raises ValueError before any call."""
    import brand_memory  # noqa: E402

    monkeypatch.setenv("MIND_PALACE_URL", live_server.url)
    monkeypatch.setenv("BRAND_MEMORY_KEY", live_server.keys["acme"])

    with pytest.raises(ValueError, match="decision must be"):
        brand_memory.record_decision(
            srv.corpora["acme"], "spring", "spring.headline", "Headline: X", "maybe"
        )
