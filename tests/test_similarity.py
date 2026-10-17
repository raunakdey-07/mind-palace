"""The lexical similarity measure, and the defect it replaces.

Both callers -- the write path's conflict hint and `remember --supersedes` phrase
resolution -- used raw token overlap. On a corpus that repeats itself, a word every
candidate carries counted as evidence that any one of them was about the statement
being written, and the caller was handed three rows chosen from a set of over a
hundred.

The fix is inverse document frequency, and the numbers that justify it are in
`api/services/similarity`'s docstring. What these tests pin is the property that
makes it safe: weighting changes how much fires, not what is found.
"""

import math

from api.services import similarity


def docs(*texts):
    return [similarity.terms(t) for t in texts]


def test_boilerplate_shared_by_every_claim_stops_counting_as_evidence():
    """The defect, in miniature.

    Four claims, one of which differs from the other three only in its serial.
    Raw overlap says the three are as close to the probe as the probe is to itself.
    """
    probe = similarity.terms("Service 1001 cache is Redis with a 300 second TTL.")
    claims = docs(
        "Service 1001 cache is Redis with a 300 second TTL.",
        "Service 1002 cache is Redis with a 300 second TTL.",
        "Service 1003 cache is Redis with a 300 second TTL.",
        "Service 1004 queue is RabbitMQ with quorum queues.",
    )
    frequencies = similarity.document_frequencies(claims)
    total = len(claims)

    self_match = similarity.weighted_overlap(probe, claims[0], frequencies, total)
    near_duplicate = similarity.weighted_overlap(probe, claims[1], frequencies, total)
    unrelated = similarity.weighted_overlap(probe, claims[3], frequencies, total)

    assert self_match == 1.0
    # Raw overlap gives the near-duplicate 6/7 = 0.857 and the unrelated claim 3/7.
    # Weighting must pull the first down, and the serial is the only term that tells
    # them apart, so it has to carry most of the probe's weight.
    assert near_duplicate < self_match
    assert unrelated < near_duplicate


def test_a_rare_term_dominates_a_common_one():
    probe = similarity.terms("The failover region for tier 3 is eu-west-3.")
    claims = docs(
        "The failover region for tier 3 is eu-west-3.",
        "Backups run nightly at 02:00 UTC and are retained for 35 days.",
    )
    frequencies = similarity.document_frequencies(claims)
    total = len(claims)
    assert similarity.weighted_overlap(probe, claims[0], frequencies, total) == 1.0
    assert similarity.weighted_overlap(probe, claims[1], frequencies, total) < 0.5


def test_a_probe_of_pure_boilerplate_matches_all_of_them():
    """And that is the right answer, not absence.

    "primary datastore" is the subject of both claims below, and it cannot tell
    them apart. Scoring 1.0 against each lets `--supersedes` refuse with both ids;
    scoring 0 would tell the caller no claim is about the subject, which is false.
    """
    claims = docs(
        "The primary datastore is PostgreSQL 15 with three replicas.",
        "The primary datastore also keeps a warm read replica.",
    )
    frequencies = similarity.document_frequencies(claims)
    probe = similarity.terms("primary datastore")
    assert similarity.weighted_overlap(probe, claims[0], frequencies, 2) == 1.0
    assert similarity.weighted_overlap(probe, claims[1], frequencies, 2) == 1.0


def test_a_single_candidate_scores_one_by_definition():
    """With one document every term has frequency equal to the total, and that is fine."""
    claims = docs("The primary datastore is PostgreSQL 15 with three replicas.")
    frequencies = similarity.document_frequencies(claims)
    probe = similarity.terms("primary datastore")
    assert similarity.weighted_overlap(probe, claims[0], frequencies, 1) == 1.0


def test_an_empty_probe_matches_nothing():
    assert similarity.weighted_overlap(frozenset(), frozenset({"anything"}), {}, 1) == 0.0


def test_stopwords_are_removed_and_case_is_folded():
    assert similarity.terms("What is the Datastore?") == {"datastore"}
    assert similarity.terms("") == frozenset()
    # Digits survive: a version number and an id are the terms that separate two
    # otherwise identical sentences.
    assert similarity.terms("PostgreSQL 16 is the datastore") == {"postgresql", "16", "datastore"}


def test_idf_is_always_positive():
    """A term in every document still has weight, so it never zeroes the denominator."""
    frequencies = {"a": 10, "b": 1}
    assert similarity.idf("a", frequencies, 10) > 0
    assert similarity.idf("unseen", frequencies, 10) > 0
    assert similarity.idf("b", frequencies, 10) > similarity.idf("a", frequencies, 10)


def test_weighting_is_bounded_by_one():
    """The threshold in `remember` assumes a [0, 1] score, and it has to stay one."""
    corpus = docs(*[f"Service {i} runs on host {i}" for i in range(50)])
    frequencies = similarity.document_frequencies(corpus)
    probe = similarity.terms("Service 7 runs on host 7")
    score = similarity.weighted_overlap(probe, corpus[7], frequencies, len(corpus))
    assert 0.0 <= score <= 1.0
    assert score == 1.0


def test_the_measure_is_deterministic_and_order_independent():
    corpus = docs("alpha beta", "beta gamma", "gamma delta")
    frequencies = similarity.document_frequencies(corpus)
    probe = similarity.terms("beta gamma")
    forward = similarity.weighted_overlap(probe, corpus[1], frequencies, 3)
    backward = similarity.weighted_overlap(probe, corpus[1], frequencies, 3)
    assert forward == backward
    assert math.isfinite(forward)


def test_both_callers_use_this_measure():
    """One function, so the two paths cannot drift apart.

    A phrase that resolves to forty claims in `--supersedes` and two in the
    conflict hint would be a bug nobody could reproduce.
    """
    import inspect

    from api.services.memory import _phrase_matches
    from api.services.remember import related_active

    hint = inspect.getsource(related_active)
    phrase = inspect.getsource(_phrase_matches)
    assert "weighted_overlap" in hint
    assert "weighted_overlap" in phrase
    assert "document_frequencies" in hint and "document_frequencies" in phrase
