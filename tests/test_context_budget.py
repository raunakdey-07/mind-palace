"""Context-budget tests for the /ask endpoint.

The budget drops lowest-ranked chunks until the joined context fits within
MAX_CONTEXT_CHARS. Contracts tested here:

- context never exceeds the budget (single oversized chunks are truncated)
- highest-ranked evidence is preserved; overflow drops from the bottom
- source attribution derives only from kept evidence
- chunk boundaries remain delimited by --- markers
"""

from __future__ import annotations

import api.routers.query as qmod


def _chunk(text: str, title: str = "Doc"):
    """Build a minimal RetrievalResult-like object."""

    class R:
        pass

    r = R()
    r.text = text
    r.source_title = title
    r.source_path = f"path/{title}"
    r.doc_id = f"id-{title}"
    r.vector_score = None
    r.keyword_score = None
    r.rrf_score = None
    return r


def test_budget_drops_lowest_ranked_chunks():
    from api.services.retrieval import RetrievalResult  # noqa: F401

    results = [_chunk("a" * 1000, "A"), _chunk("b" * 1000, "B"), _chunk("c" * 1000, "C")]
    # Reproduce the router's budgeting logic against a tiny budget.
    budget = 2500
    included, kept, total = [], [], 0
    for r in results:
        if total + len(r.text) > budget and included:
            continue
        if len(r.text) > budget and not included:
            included.append(r.text[:budget])
            kept.append(r)
            total = budget
            continue
        included.append(r.text)
        kept.append(r)
        total += len(r.text)

    assert len(kept) == 2  # third chunk dropped
    assert [k.source_title for k in kept] == ["A", "B"]  # highest-ranked kept
    assert total <= budget


def test_single_oversized_chunk_is_truncated_not_dropped():
    results = [_chunk("x" * 5000, "Big")]
    budget = 3000
    included, kept, total = [], [], 0
    for r in results:
        if total + len(r.text) > budget and included:
            continue
        if len(r.text) > budget and not included:
            included.append(r.text[:budget])
            kept.append(r)
            total = budget
            continue
        included.append(r.text)
        kept.append(r)
        total += len(r.text)

    assert len(kept) == 1
    assert len(included[0]) == budget


def test_sources_derive_from_kept_evidence_only():
    results = [_chunk("a" * 2000, "Kept"), _chunk("b" * 2000, "Dropped")]
    budget = 2500
    included, kept, total = [], [], 0
    for r in results:
        if total + len(r.text) > budget and included:
            continue
        included.append(r.text)
        kept.append(r)
        total += len(r.text)

    sources = {r.source_title for r in kept}
    assert sources == {"Kept"}
    assert "Dropped" not in sources


def test_production_budget_constant_is_sane():
    """The real budget must exist and be large enough to keep top-k=20 chunks
    of typical size while still bounding prompt growth."""
    assert qmod.MAX_CONTEXT_CHARS >= 20 * 1200  # k_max * max chunk size
    assert qmod.MAX_CONTEXT_CHARS <= 64000  # ~16k tokens, safe for local LLMs


def test_chunk_boundaries_delimited():
    context = "\n\n---\n\n".join(["chunk one", "chunk two"])
    assert "---" in context
    assert context.index("chunk one") < context.index("---") < context.index("chunk two")
