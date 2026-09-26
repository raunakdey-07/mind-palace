"""Isolated pack regression oracle and single-repetition measurements; no DB/embeddings.

The reference intentionally retains the pre-optimization algorithm. Timing is
reported, never asserted: correctness is exact canonical output equivalence.
"""

from datetime import datetime, timezone
from random import Random
from time import perf_counter

import pytest

from api.models.memory import Change, Claim, Conflict, Evidence, MemoryResponse, Snapshot, State
from api.services import memory_public
from api.services.memory_public import MemoryError, _attach, _claims, bounded_pack


def reference_pack(full: MemoryResponse, budget: int) -> MemoryResponse:
    """Frozen pre-optimization bounded_pack; do not delegate to production selection.

    Envelope note: the oracle carries ``constraints`` because production does.
    The absence marker must survive selection, so a bounded answer to an
    unanswerable question stays distinguishable from an empty envelope. The
    selection algorithm below is unchanged and is what this oracle pins.
    """
    result = MemoryResponse(
        query=full.query,
        corpus=full.corpus,
        state=full.state,
        constraints=list(full.constraints),
        truncated=True,
    )
    if len(result.canonical_json()) > budget:
        raise MemoryError("invalid_budget", "Budget cannot fit the response envelope", 422)
    evidence = {e.id: e for e in full.evidence}
    dropped = False
    for field in (
        "current_memories",
        "changes",
        "historical_memories",
        "conflicts",
        "uncertain_memories",
    ):
        for item in getattr(full, field):
            candidate = result.model_copy(deep=True)
            if item not in getattr(candidate, field):
                getattr(candidate, field).append(item)
            while True:
                claim_ids = {c.id for c in _claims(candidate)}
                missing = [
                    g
                    for g in full.conflicts
                    if g not in candidate.conflicts and any(c.id in claim_ids for c in g.claims)
                ]
                if not missing:
                    break
                candidate.conflicts.extend(missing)
            selected_conflicts = {g.id for g in candidate.conflicts}
            candidate.conflicts = [g for g in full.conflicts if g.id in selected_conflicts]
            _attach(candidate, evidence)
            if len(candidate.canonical_json()) <= budget:
                result = candidate
            else:
                dropped = True
    if not dropped:
        result.truncated = False
        if len(result.canonical_json()) > budget:
            result.truncated = True
    return result


def synthetic_response(size: int, *, seed: int = 0, mixed: bool = False) -> MemoryResponse:
    """Typed claims with complete multi-quote provenance and a fixed snapshot clock."""
    rng = Random(seed)
    at = datetime(2026, 1, 2, 3, 4, 5, tzinfo=timezone.utc)
    full = MemoryResponse(
        corpus="pack-test",
        query='decision 雪 "quoted"',
        state=State(as_of=at, valid_at=at, snapshot="a" * 64),
        snapshot=Snapshot(id="a" * 64, as_of=at, observed_at=at, version_ids=[]),
        constraints=["Not copied into the pack envelope"],
    )
    claims = []
    for i in range(size):
        # Two claims can share a source, but never share evidence IDs.
        version = f"version-{i // 2:04d}"
        path = f'notes/{i // 2:04d}-雪-"quoted".md'
        text = f'Decision {i}: café 🙂\n"quoted" \\ ' + "x" * rng.randrange(1, 120)
        quotes = [text, f"support {i} 雪"]
        claim = Claim(
            id=f"claim-{i:04d}",
            key=f"key-{i:04d}",
            value={"nested": [None, True, i, 1.25, {"雪": text}]},
            claim=text,
            status="CURRENT",
            version_id=version,
            path=path,
            observed_at=at,
            valid_from=at,
            evidence_ids=[f"e-{i:04d}-{j}" for j in range(2)],
        )
        claims.append(claim)
        for j, quote in enumerate(quotes):
            full.evidence.append(
                Evidence(
                    id=claim.evidence_ids[j],
                    claim_id=claim.id,
                    version_id=version,
                    document_id=f"doc-{i // 2}",
                    path=path,
                    source_hash=f"hash-{i // 2}",
                    chunk_id=f"chunk-{i}",
                    heading='Résumé "雪"',
                    text=quote,
                    start_offset=0 if j == 0 else len(text) + 1,
                    end_offset=len(quote) if j == 0 else len(text) + 1 + len(quote),
                    observed_at=at,
                )
            )
    full.current_memories = claims[:]
    if mixed:
        assert size >= 6
        claims[0].status = "SUPERSEDED"
        claims[1].status = "UNCERTAIN"
        full.historical_memories = [claims[0], claims[0].model_copy(deep=True)]
        full.uncertain_memories = [claims[1]]
        full.current_memories = claims[5:] + [claims[5].model_copy(deep=True)]
        for claim in claims[2:5]:
            claim.status = "CONFLICTING"
            claim.key = "decision"
        # Reverse chain order forces more than one closure pass from claim 2.
        full.conflicts = [
            Conflict(id="g-right", key="decision", claims=claims[3:5]),
            Conflict(id="g-left", key="decision", claims=claims[2:4]),
        ]
        rng.shuffle(full.current_memories)
    for i, claim in enumerate(claims):
        previous = [claims[0]] if mixed and i == 2 else []
        full.changes.append(
            Change(
                version_id=claim.version_id,
                predecessor_id=previous[0].version_id if previous else None,
                event="MODIFIED" if previous else "NEW",
                path=claim.path,
                observed_at=at,
                memory_changed=True,
                relationship="SUPERSEDES" if previous else "LIFECYCLE",
                previous=previous,
                current=[claim],
            )
        )
    if mixed:
        # Exercise closure introduced by a change before the conflict section.
        full.changes = [full.changes[2]] + full.changes[:2] + full.changes[3:]
        full.changes.append(full.changes[0].model_copy(deep=True))
        rng.shuffle(full.evidence)
    _attach(full, {e.id: e for e in full.evidence})
    return full


def assert_equivalent(full, budget):
    original = full.canonical_json()
    try:
        expected = reference_pack(full, budget)
    except MemoryError as expected_error:
        with pytest.raises(MemoryError) as actual:
            bounded_pack(full, budget)
        assert (actual.value.code, actual.value.message, actual.value.status_code) == (
            expected_error.code,
            expected_error.message,
            expected_error.status_code,
        )
    else:
        actual = bounded_pack(full, budget)
        assert actual.canonical_json() == expected.canonical_json()
        assert len(actual.canonical_json()) <= budget
        assert MemoryResponse.model_validate_json(actual.canonical_json()) == actual
        ids = {eid for claim in _claims(actual) for eid in claim.evidence_ids}
        assert [e.id for e in actual.evidence] == sorted(ids)
        assert [s.version_id for s in actual.sources] == sorted(
            {e.version_id for e in actual.evidence}
        )
        claim_ids = {c.id for c in _claims(actual)}
        for group in full.conflicts:
            if any(c.id in claim_ids for c in group.claims):
                assert group in actual.conflicts
    assert full.canonical_json() == original


def test_every_small_budget_matches_reference():
    full = synthetic_response(1)
    upper = len(reference_pack(full, 128000).canonical_json())
    envelope = MemoryResponse(
        query=full.query, corpus=full.corpus, state=full.state, truncated=True
    )
    lower = len(envelope.canonical_json())
    for budget in range(lower - 1, upper + 2):
        assert_equivalent(full, budget)
    for budget in (lower - 1, lower, lower + 1):
        assert_equivalent(envelope, budget)


@pytest.mark.parametrize("seed", range(6))
def test_seeded_mixed_pack_matches_reference(seed):
    full = synthetic_response(6 + seed, seed=seed, mixed=True)
    rng = Random(seed)
    complete = len(reference_pack(full, 128000).canonical_json())
    budgets = {0, 512, 8000, 128000, complete - 1, complete, complete + 1}
    budgets.update(rng.randrange(512, complete + 2) for _ in range(20))
    # Probe exact accepted-output boundaries as well as random budgets.
    for budget in list(budgets):
        if budget >= 512:
            boundary = len(reference_pack(full, budget).canonical_json())
            budgets.update((boundary - 1, boundary, boundary + 1))
    for budget in sorted(budgets):
        assert_equivalent(full, budget)


@pytest.mark.parametrize("size", [100, 500])
def test_pack_single_repetition_measurement(size):
    full = synthetic_response(size)
    original = full.canonical_json()
    start = perf_counter()
    expected = reference_pack(full, 8000)
    before_ms = (perf_counter() - start) * 1000
    start = perf_counter()
    actual = bounded_pack(full, 8000)
    after_ms = (perf_counter() - start) * 1000
    assert actual.canonical_json() == expected.canonical_json()
    assert full.canonical_json() == original
    print(
        f"\npack size={size} budget=8000 reps=1 reference={before_ms:.3f}ms "
        f"production={after_ms:.3f}ms selected={len(actual.current_memories)} "
        f"chars={len(actual.canonical_json())}"
    )


def test_candidates_do_not_recursively_copy_accepted_items(monkeypatch):
    full = synthetic_response(6)
    expected = reference_pack(full, 8000).canonical_json()
    copy_modes = []
    original_copy = MemoryResponse.model_copy

    def tracked_copy(self, *, update=None, deep=False):
        copy_modes.append(deep)
        return original_copy(self, update=update, deep=deep)

    monkeypatch.setattr(MemoryResponse, "model_copy", tracked_copy)
    assert bounded_pack(full, 8000).canonical_json() == expected
    assert copy_modes == [False] * 12  # Six claims and six changes, including rejections.


def test_missing_evidence_is_not_silently_skipped():
    full = synthetic_response(1)
    full.evidence.clear()
    for pack in (reference_pack, memory_public.bounded_pack):
        with pytest.raises(KeyError):
            pack(full, 512)
