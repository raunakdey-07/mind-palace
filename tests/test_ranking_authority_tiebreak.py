"""A near-tie should be broken on something the scorer does not know.

The scorer answers "which claim is about this question". It cannot answer "is this
claim the standing answer or one scoped to an interval", and when two claims score
0.003 apart it has already said it cannot choose.

These are the properties the rule must hold. What it is worth is measured in
`memory_relevance.authority_order`: rank 1 goes from 116/175 to 129/175 on the
answerable dev questions, with k >= 2 unchanged.
"""

from datetime import datetime, timezone

from api.services.memory_relevance import authority_order

OPEN_ENDED = datetime(2026, 1, 1, tzinfo=timezone.utc)
LATER = datetime(2026, 2, 1, tzinfo=timezone.utc)


class FakeClaim:
    """Only the fields `authority_order` reads, so a test cannot drift with the model."""

    def __init__(self, *, bounded=False, status="CURRENT", observed_at=OPEN_ENDED):
        self.valid_from = OPEN_ENDED if bounded else None
        self.valid_until = None
        self.status = status
        self.observed_at = observed_at


def live(*specs):
    """`live(key, claim)` per entry, in the order the keys were given."""
    return {key: [claim] for key, claim in specs}


def keys(order):
    return [key for key, _score in order]


# --- the rule ---------------------------------------------------------------


def test_a_standing_claim_leads_a_scoped_one_inside_the_band():
    """The whole rule, in one case: 0.003 apart, and only one of them stands."""
    order = [("scoped", 0.7326), ("standing", 0.7296)]
    claims = live(("scoped", FakeClaim(bounded=True)), ("standing", FakeClaim()))
    assert keys(authority_order(order, claims, dated=False, band=0.10)) == [
        "standing",
        "scoped",
    ]


def test_a_scoped_claim_still_leads_when_it_is_a_clear_margin_behind():
    """Being scoped is a tie-breaker, not a promotion.

    A claim already 0.10 behind is not a near-tie, it lost. Re-ordering it would
    replace the scorer's answer with a heuristic about the archive's shape.
    """
    order = [("scoped", 0.80), ("standing", 0.60)]
    claims = live(("scoped", FakeClaim(bounded=True)), ("standing", FakeClaim()))
    assert keys(authority_order(order, claims, dated=False, band=0.10)) == [
        "scoped",
        "standing",
    ]


def test_a_dated_question_keeps_the_scoped_claim():
    """ "What was it in March?" is about an interval, so the interval claim is right."""
    order = [("standing", 0.7326), ("scoped", 0.7296)]
    claims = live(("standing", FakeClaim()), ("scoped", FakeClaim(bounded=True)))
    assert keys(authority_order(order, claims, dated=True, band=0.10)) == [
        "standing",
        "scoped",
    ]


def test_within_one_scope_the_score_still_decides():
    """Two standing claims are interchangeable by scope, so the score is the answer."""
    order = [("low", 0.55), ("high", 0.74)]
    claims = live(("low", FakeClaim()), ("high", FakeClaim()))
    assert keys(authority_order(order, claims, dated=False, band=0.10)) == [
        "high",
        "low",
    ]


def test_observation_time_and_status_are_not_tie_breakers():
    """Measured: re-ordering on either of these scores worse than doing nothing.

    They describe how the corpus was written, not what the question asked, and
    inside the band they outrank the score. The rule has to stay one boolean.
    """
    order = [("older_current", 0.7326), ("newer_superseded", 0.7296)]
    claims = live(
        ("older_current", FakeClaim(status="CURRENT", observed_at=OPEN_ENDED)),
        ("newer_superseded", FakeClaim(status="SUPERSEDED", observed_at=LATER)),
    )
    assert keys(authority_order(order, claims, dated=False, band=0.10)) == [
        "older_current",
        "newer_superseded",
    ]


# --- the edges --------------------------------------------------------------


def test_a_key_holding_both_a_scoped_and_a_standing_claim_counts_as_standing():
    """The caller would be pointed at a claim that is true outside the interval."""
    order = [("mixed", 0.60), ("scoped", 0.59)]
    claims = {
        "mixed": [FakeClaim(bounded=True), FakeClaim(bounded=False)],
        "scoped": [FakeClaim(bounded=True)],
    }
    assert keys(authority_order(order, claims, dated=False, band=0.10)) == [
        "mixed",
        "scoped",
    ]


def test_a_sole_candidate_is_never_moved():
    order = [("only", 0.72)]
    assert (
        authority_order(order, live(("only", FakeClaim(bounded=True))), dated=False, band=0.1)
        == order
    )


def test_a_key_with_no_claim_is_treated_as_standing():
    """Defensive: an absent claim cannot be shown, so it must not be preferred."""
    order = [("ghost", 0.60), ("real", 0.59)]
    claims = {"ghost": [], "real": [FakeClaim(bounded=True)]}
    assert keys(authority_order(order, claims, dated=False, band=0.10)) == [
        "ghost",
        "real",
    ]


def test_the_returned_order_is_a_permutation_of_the_input():
    """Re-ordering a shortlist must not drop or invent a key."""
    order = [("a", 0.80), ("b", 0.79), ("c", 0.78), ("d", 0.50)]
    claims = live(
        ("a", FakeClaim(bounded=True)),
        ("b", FakeClaim()),
        ("c", FakeClaim(bounded=True)),
        ("d", FakeClaim(bounded=True)),
    )
    out = authority_order(order, claims, dated=False, band=0.10)
    assert sorted(keys(out)) == sorted(keys(order))
    # The claim outside the band keeps its place at the end.
    assert keys(out)[-1] == "d"
    assert len(set(keys(out))) == len(order)


def test_ties_on_score_and_scope_fall_back_to_the_key():
    """Two indistinguishable claims must not order by dict insertion."""
    order = [("zebra", 0.70), ("alpha", 0.70)]
    claims = live(("zebra", FakeClaim()), ("alpha", FakeClaim()))
    assert keys(authority_order(order, claims, dated=False, band=0.10)) == [
        "alpha",
        "zebra",
    ]


def test_the_band_boundary_is_exclusive():
    """`band` is the width of the tie. Exactly `band` apart is not a tie."""
    order = [("scoped", 0.80), ("standing", 0.70)]
    claims = live(("scoped", FakeClaim(bounded=True)), ("standing", FakeClaim()))
    assert keys(authority_order(order, claims, dated=False, band=0.10)) == [
        "scoped",
        "standing",
    ]


def test_claims_outside_the_band_are_not_pulled_in_by_a_dated_shortlist():
    order = [("standing", 0.80), ("scoped", 0.79), ("far", 0.10)]
    claims = live(
        ("standing", FakeClaim()),
        ("scoped", FakeClaim(bounded=True)),
        ("far", FakeClaim()),
    )
    out = authority_order(order, claims, dated=False, band=0.10)
    assert keys(out) == ["standing", "scoped", "far"]
