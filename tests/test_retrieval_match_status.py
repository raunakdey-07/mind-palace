"""`match_status`, `candidates`, and the decisions behind them.

The signal only matters if it is right in the cases it is for, and silent in the
ones it is not. Rank 1 is correct for 117 of the 175 answerable dev questions and
wrong for 58, and the two distributions of `score_1 - score_2` overlap almost
entirely -- so `ambiguous` is not a claim that the answer is wrong. It is a
statement that the ranking could not choose and the shortlist has to be read.
"""

from datetime import datetime, timezone

import pytest

from api.models.memory import Candidate, Claim, Evidence, MemoryResponse
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


def adjudicate_scored(scored, claims, evidence_rows, policy=None, corpus=None):
    """Adjudicate a response the way the archive does.

    `claims` is what the response carried. `corpus` is what the archive holds
    -- the whole corpus, of which the response is a selection. It is separate
    because a competitor the caller cannot see is only a competitor if the
    archive can still show it.
    """
    result = MemoryResponse(query="q", corpus="c", current_memories=claims)
    full = MemoryResponse(
        query="q",
        corpus="c",
        current_memories=corpus if corpus is not None else claims,
        evidence=evidence_rows,
    )
    adjudicate(full, result, scored, policy or RelevancePolicy.configured())
    return result


TWO = [claim("a", "datastore", "PostgreSQL"), claim("b", "search", "OpenSearch", "e2")]
QUOTES = [evidence("e1", "The datastore is PostgreSQL."), evidence("e2", "Search is OpenSearch.")]


def test_a_clear_top_score_is_confident():
    result = adjudicate_scored({"datastore": 0.72, "search": 0.30}, TWO, QUOTES)
    assert result.match_status == "confident"
    assert result.match_margin == pytest.approx(0.42)


def test_two_claims_with_different_values_inside_the_margin_are_ambiguous():
    result = adjudicate_scored({"datastore": 0.70, "search": 0.66}, TWO, QUOTES)
    assert result.match_status == "ambiguous"
    assert result.match_margin == pytest.approx(0.04)


def test_a_near_tie_on_the_same_value_is_not_ambiguous():
    """Two interchangeable claims need no adjudication, so they get no warning."""
    result = adjudicate_scored({"datastore": 0.70}, TWO[:1], QUOTES)
    assert result.match_status == "confident"


def test_a_top_score_just_clear_of_the_floor_is_ambiguous():
    """Weak in absolute terms, whatever beat it."""
    result = adjudicate_scored({"datastore": 0.33, "search": 0.10}, TWO, QUOTES)
    assert result.match_status == "ambiguous"


def test_a_score_too_weak_to_be_a_near_tie_is_not_ambiguous():
    result = adjudicate_scored({"datastore": 0.12, "search": 0.05}, TWO, QUOTES)
    assert result.match_status == "confident"


def test_candidates_carry_the_quote_and_the_claim_id():
    """A shortlist the caller cannot check the source of is not a shortlist."""
    result = adjudicate_scored({"datastore": 0.72, "search": 0.30}, TWO, QUOTES)
    top = result.candidates[0]
    assert isinstance(top, Candidate)
    assert top.key == "datastore"
    assert top.quotes == ["The datastore is PostgreSQL."]
    assert top.claim_ids == ["a"]
    assert top.version_ids == ["a" * 64]
    assert top.score == pytest.approx(0.72)


def test_no_claims_means_abstained_and_no_candidates():
    """An abstention must not advertise sixty weak candidates as a shortlist."""
    result = adjudicate_scored({"datastore": 0.9, "search": 0.8}, [], QUOTES)
    assert result.match_status == "abstained"
    assert result.candidates == []
    assert result.match_margin is None


def test_the_margin_is_measured_against_the_ranking_not_against_nobody():
    """A response of one claim is a near-tie when the ranking says it is.

    The margin used to be `score_1 - 0.0`: a number that reads like a decisive
    win over a field that was never in the ranking. The ranking is the field the
    caller is being asked to adjudicate against, so the runner-up comes from
    there.
    """
    # `search` scores just under `datastore`, and the response carried only
    # `datastore`.
    result = adjudicate_scored({"datastore": 0.70, "search": 0.69}, TWO[:1], QUOTES, corpus=TWO)
    assert result.match_margin == pytest.approx(0.01)
    # The competitor that decides the verdict is listed, so the reader can see
    # what they are being asked to choose between.
    assert {c.key for c in result.candidates} == {"datastore", "search"}
    assert result.match_status == "ambiguous"


def test_the_margin_and_the_candidate_scores_are_on_one_scale():
    """The banner must not contradict the numbers printed beside it.

    The CLI prints a candidate's score next to a banner that calls it a
    near-tie, so the margin has to be the difference between two of those
    scores. It used to be the raw-cosine difference, which put "almost the same"
    beside 0.755 and 0.556 -- a gap of 0.199.
    """
    result = adjudicate_scored({"datastore": 0.72, "search": 0.30}, TWO, QUOTES)
    scores = [c.score for c in result.candidates]
    assert result.match_margin == pytest.approx(max(scores) - min(scores))


def test_a_lone_claim_against_no_field_is_still_its_own_score():
    """One claim in the corpus and one in the response: nothing competed."""
    result = adjudicate_scored({"datastore": 0.50}, TWO[:1], QUOTES[:1])
    assert [c.key for c in result.candidates] == ["datastore"]
    assert result.match_margin == pytest.approx(0.50)
    assert result.match_status == "confident"


def test_the_canonical_json_digest_does_not_depend_on_the_ranking():
    """`match_status` is derived from the claims the digest already covers.

    Two responses with identical claims and different rankings are the same
    memory, and the receipt digest is what proves it. Folding the diagnostics in
    would make the digest move when the answer did not.
    """
    confident = adjudicate_scored({"datastore": 0.72, "search": 0.30}, TWO, QUOTES)
    ambiguous = adjudicate_scored({"datastore": 0.70, "search": 0.66}, TWO, QUOTES)
    assert confident.match_status != ambiguous.match_status
    assert confident.canonical_json() == ambiguous.canonical_json()


def test_a_bad_threshold_setting_is_ignored(monkeypatch):
    """A non-finite or out-of-range setting must not make every answer ambiguous."""
    from api.services.memory_relevance import DEFAULT_MARGIN, margin_threshold

    assert margin_threshold() == DEFAULT_MARGIN
    for bad in ("nan", "inf", "-1", "5", "not-a-number"):
        monkeypatch.setenv("MEMORY_QUERY_MARGIN", bad)
        assert margin_threshold() == DEFAULT_MARGIN, bad


def test_the_threshold_setting_is_honoured_when_it_is_sane(monkeypatch):
    monkeypatch.setenv("MEMORY_QUERY_MARGIN", "0.5")
    from api.services.memory_relevance import margin_threshold

    assert margin_threshold() == 0.5
