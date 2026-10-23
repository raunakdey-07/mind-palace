"""Regression tests for score capping and compound-query ambiguity.

Issue #27: displayed candidate scores must never exceed 1.0, and complementary
winners of distinct sub-parts of a multi-part question must not be reported as
an ambiguous near-tie.
"""

from datetime import datetime, timezone

from api.models.memory import Claim, Evidence, MemoryResponse
from api.services.memory_query import adjudicate
from api.services.memory_relevance import RelevancePolicy

NOW = datetime.now(timezone.utc)


def claim(identifier, key, value, evidence="e1", status="CURRENT"):
    return Claim(
        id=identifier,
        key=key,
        value=value,
        claim=f"{key} is {value}",
        status=status,
        version_id="a" * 64,
        path="adr.md",
        observed_at=NOW,
        evidence_ids=[evidence],
    )


def evidence(identifier, text):
    return Evidence(
        id=identifier,
        claim_id=identifier,
        version_id="a" * 64,
        document_id="d" * 64,
        path="adr.md",
        source_hash="f" * 64,
        chunk_id="c" * 64,
        heading=None,
        text=text,
        start_offset=0,
        end_offset=len(text),
        observed_at=NOW,
    )


def adjudicate_case(
    scored,
    claims,
    evidence_rows,
    *,
    ranked=None,
    parts=None,
    topic_winners=None,
    key_parts=None,
    corpus=None,
    policy=None,
):
    result = MemoryResponse(query="q", corpus="c", current_memories=claims)
    full = MemoryResponse(
        query="q",
        corpus="c",
        current_memories=corpus if corpus is not None else claims,
        evidence=evidence_rows,
    )
    adjudicate(
        full,
        result,
        scored,
        policy or RelevancePolicy.configured(),
        ranked=ranked,
        parts=parts,
        topic_winners=topic_winners,
        key_parts=key_parts,
    )
    return result


TWO = [claim("a", "datastore", "PostgreSQL"), claim("b", "search", "OpenSearch", "e2")]
QUOTES = [evidence("e1", "The datastore is PostgreSQL."), evidence("e2", "Search is OpenSearch.")]


def test_candidate_scores_never_exceed_one_even_with_full_coverage():
    # A coverage-adjusted score can exceed 1.0; the displayed score must not.
    result = adjudicate_case(
        {"datastore": 1.6, "search": 0.4},
        TWO,
        QUOTES,
        ranked={"datastore": 1.6, "search": 0.4},
    )
    assert all(0.0 <= c.score <= 1.0 for c in result.candidates)
    assert result.candidates[0].score == 1.0


def test_multi_part_distinct_subpart_winners_are_confident():
    claims = [
        claim("a", "bundle", "Gzip: 20.40 kB", "e1"),
        claim("b", "latency", "Value: 71 ms", "e2"),
    ]
    quotes = [evidence("e1", "bundle size"), evidence("e2", "latency")]
    # Gap between the two winners is below the margin, but they answer different
    # sub-parts, so the response stays confident.
    scored = {"bundle": 0.764, "latency": 0.843}
    result = adjudicate_case(
        scored,
        claims,
        quotes,
        ranked=dict(scored),
        parts=["gzipped bundle size", "keystroke-to-preview latency"],
        topic_winners=["bundle", "latency"],
    )
    assert result.match_status == "confident"


def test_competing_claims_for_same_subpart_stay_ambiguous():
    claims = [
        claim("a", "datastore", "PostgreSQL", "e1"),
        claim("b", "search", "OpenSearch", "e2"),
    ]
    # Both compete for the same sub-part and sit inside the margin.
    scored = {"datastore": 0.70, "search": 0.66}
    result = adjudicate_case(
        scored,
        claims,
        QUOTES,
        ranked=dict(scored),
        parts=["datastore and search"],
        key_parts={"datastore": 0, "search": 0},
    )
    assert result.match_status == "ambiguous"
