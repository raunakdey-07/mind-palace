"""Query contracts, not semantic-quality tests.

Ranking uses prescribed unit vectors; public calls patch the lazy Embedder import
and retain the real executor, archive, projection and packer. PostgreSQL cases
reuse test_memory_public's rollback-only fixture and are gated by its test URL.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from math import sqrt

import pytest
from pydantic import ValidationError

from api.models.memory import (
    Change,
    Claim,
    Conflict,
    Evidence,
    MemoryRequest,
    MemoryResponse,
    State,
)
from api.services import memory, memory_public, memory_query, memory_relevance

try:
    from tests import test_memory_public as public
except ModuleNotFoundError as exc:
    if exc.name not in {"tests", "tests.test_memory_public"}:
        raise
    import test_memory_public as public

memory_db = public.memory_db
public_db = public.public_db

CLOCK = datetime(2024, 6, 15, 12, tzinfo=timezone.utc)
BUDGETS = (1000, 2000, 4000, 8000, 16000)
INTENTS = ("current", "historical", "temporal", "change", "conflict", "provenance")


class FakeEmbedder:
    """Prescribed cosine scores against [1, 0], independent of language meaning."""

    def __init__(self, scores=None, default=1.0):
        self.scores = scores or {}
        self.default = default
        self.calls = []

    def embed(self, texts):
        self.calls.append(list(texts))
        scores = [self.scores.get(text, self.default) for text in texts[1:]]
        return [[1.0, 0.0]] + [[score, sqrt(1 - score**2)] for score in scores]


def representation(claim):
    return f"{claim.claim} {claim.key} {claim.path}"


@pytest.fixture
def offline_embedder(monkeypatch):
    monkeypatch.setenv("HF_HUB_OFFLINE", "1")
    monkeypatch.setenv("TRANSFORMERS_OFFLINE", "1")
    from api.services import embedder

    fake = FakeEmbedder()
    monkeypatch.setattr(embedder, "Embedder", lambda: fake)
    return fake


@pytest.mark.parametrize(
    "question,expected",
    [
        ("Which database?", "current"),
        ("What is CURRENTLY in use?", "current"),
        ("What is current?", "current"),
        ("What do we use now rather than before?", "current"),
        ("What was used before?", "historical"),
        ("What happened after the migration?", "historical"),
        ("What did we used to use?", "historical"),
        ("Show previous settings", "historical"),
        ("What was formerly configured?", "historical"),
        ("Show historical decisions", "historical"),
        ("Show history", "historical"),
        ("Find the old database", "historical"),
        ("Find the prior decision", "historical"),
        ("What changed?", "change"),
        ("What did we replace?", "change"),
        ("What replaced the database?", "change"),
        ("What supersedes the old decision?", "change"),
        ("Which decisions conflict?", "conflict"),
        ("Which sources disagree?", "conflict"),
        ("Find contradictory evidence", "conflict"),
        ("Show evidence for the current decision", "provenance"),
        ("Show provenance", "provenance"),
        ("What source supports it?", "provenance"),
        ("List sources", "provenance"),
        ("Quote the old decision", "provenance"),
        ("What was configured on 2024-02-29?", "temporal"),
    ],
)
def test_interpret_auto_intents(question, expected):
    intent = memory_query.interpret(MemoryRequest(corpus="test", query=question))
    assert intent.name == expected
    assert intent.as_of == (
        datetime(2024, 2, 29, tzinfo=timezone.utc) if expected == "temporal" else None
    )


@pytest.mark.parametrize("intent", INTENTS)
def test_explicit_intent_overrides_question_keywords(intent):
    request = MemoryRequest(
        corpus="test",
        query="Which sources conflict and changed before now?",
        intent=intent,
        as_of=CLOCK,
    )
    assert memory_query.interpret(request) == memory_query.QueryIntent(intent, CLOCK)


def test_plan_preserves_question_text_and_removes_bounded_framing():
    request = MemoryRequest(
        corpus="test",
        query=(
            "At the pilot checkpoint, without inferring transport, "
            "what replay responsibility did the ownership document assert?"
        ),
    )
    planned = memory_relevance.plan(request, "temporal", memory_relevance.RelevancePolicy())
    assert planned.topics == ("what replay responsibility did the ownership document assert?",)


@pytest.mark.parametrize("selector", [{"as_of": CLOCK}, {"snapshot_id": "a" * 64}])
@pytest.mark.parametrize(
    "question",
    [
        "What was current on 1999-01-01?",
        "Compare 2020-01-01 and 2021-01-01",
        "What happened on 2024-02-30?",
        "What was used in stage B?",
    ],
)
def test_explicit_time_precedes_question_dates_and_stage(selector, question):
    request = MemoryRequest(corpus="test", query=question, **selector)
    before = request.model_dump()
    assert memory_query.interpret(request) == memory_query.QueryIntent(
        "temporal", selector.get("as_of")
    )
    assert request.model_dump() == before


@pytest.mark.parametrize(
    "fields,code",
    [
        ({"query": ""}, "invalid_query"),
        ({"query": "What happened on 2023-02-29?"}, "invalid_timestamp"),
        ({"query": "What happened on 2024-13-01?"}, "invalid_timestamp"),
        ({"query": "Compare 2024-01-01 and 2024-01-02"}, "invalid_timestamp"),
        ({"query": "2024-01-01 versus 2024-01-01"}, "invalid_timestamp"),
        ({"query": "What was used in STAGE b?"}, "invalid_timestamp"),
        ({"query": "What was used in stage 2?"}, "invalid_timestamp"),
        ({"query": "Which database?", "intent": "temporal"}, "invalid_timestamp"),
        ({"query": "Stage A", "intent": "current"}, "invalid_timestamp"),
        ({"query": "Stage A", "valid_at": CLOCK}, "invalid_timestamp"),
    ],
)
def test_interpret_rejects_invalid_or_ambiguous_time(fields, code):
    with pytest.raises(memory_public.MemoryError) as error:
        memory_query.interpret(MemoryRequest(corpus="test", **fields))
    assert (error.value.code, error.value.status_code) == (code, 422)


@pytest.mark.parametrize(
    "fields",
    [
        {"query": " \t\n"},
        {"as_of": "2024-01-01"},
        {"as_of": datetime(2024, 1, 1)},
        {"intent": "deletion"},
    ],
)
def test_query_request_rejects_invalid_structured_input(fields):
    with pytest.raises(ValidationError):
        MemoryRequest(corpus="test", **fields)


def make_claim(cid, text, *, key="database", path="note.md", status="CURRENT", supersedes=None):
    return Claim(
        id=cid,
        key=key,
        value=text,
        claim=text,
        status=status,
        version_id="version-" + cid,
        path=path,
        observed_at=CLOCK,
        supersedes_id=supersedes,
        evidence_ids=["evidence-" + cid],
    )


def make_response(**sections):
    """Build independently grounded projections for offline selection tests."""
    response = MemoryResponse(query="", corpus="test", state=State(valid_at=CLOCK), **sections)
    claims = {claim.id: claim for claim in public.all_claims(response)}
    for claim in sorted(claims.values(), key=lambda c: c.id):
        response.evidence.append(
            Evidence(
                id=claim.evidence_ids[0],
                claim_id=claim.id,
                version_id=claim.version_id,
                document_id="document-" + claim.path,
                path=claim.path,
                source_hash="hash-" + claim.id,
                chunk_id="chunk-" + claim.id,
                heading="Decision",
                text=claim.claim,
                start_offset=0,
                end_offset=len(claim.claim),
                observed_at=claim.observed_at,
            )
        )
    # Sources are not inputs to select; it must reconstruct them from selected evidence.
    return response


@pytest.fixture
def ranking_response():
    a = make_claim("a", "Alpha beta", key="choice", path="one.md", status="SUPERSEDED")
    b = make_claim("b", "alpha", key="choice", path="two.md")
    c = make_claim("c", "gamma", key="other", path="three.md", status="CONFLICTING")
    return make_response(
        current_memories=[b],
        historical_memories=[a],
        conflicts=[Conflict(id="group", key="other", claims=[c])],
        changes=[
            Change(
                version_id=b.version_id,
                predecessor_id=a.version_id,
                event="MODIFIED",
                path=b.path,
                observed_at=CLOCK,
                memory_changed=True,
                relationship="SUPERSEDES",
                previous=[a],
                current=[b],
            )
        ],
    )


@pytest.mark.parametrize(
    "strategy,ids,scores",
    [
        ("lexical", ["a", "b", "c"], [1.0, 0.5, 0.0]),
        ("embedding", ["c", "b", "a"], [1.0, 0.5, 0.0]),
        ("hybrid", ["a", "c", "b"], [1 / 61 + 1 / 63, 1 / 61 + 1 / 63, 2 / 62]),
    ],
)
def test_ranking_strategies_scores_deduplication_and_tie_breaks(
    ranking_response,
    strategy,
    ids,
    scores,
):
    full = ranking_response
    claims = {c.id: c for c in public.all_claims(full)}
    fake = FakeEmbedder(
        {representation(claims[cid]): score for cid, score in (("a", 0.0), ("b", 0.5), ("c", 1.0))}
    )
    before = full.model_dump()
    ranked = memory_query.rank_claims(full, "ALPHA beta", fake, strategy)
    assert [item.id for item in ranked] == ids
    assert [item.score for item in ranked] == pytest.approx(scores)
    assert len(ranked) == 3  # The same claims also occur in change records.
    assert full.model_dump() == before
    if strategy == "lexical":
        assert fake.calls == []
    else:
        assert fake.calls == [["ALPHA beta"] + [representation(claims[cid]) for cid in "abc"]]
    reversed_full = full.model_copy(
        update={
            "historical_memories": list(reversed(full.historical_memories)),
            "current_memories": list(reversed(full.current_memories)),
            "changes": list(reversed(full.changes)),
        }
    )
    assert memory_query.rank_claims(reversed_full, "ALPHA beta", fake, strategy) == ranked


@pytest.mark.parametrize("strategy", ["lexical", "embedding", "hybrid"])
def test_ranking_equal_scores_use_id_not_input_order(strategy):
    full = make_response(current_memories=[make_claim(cid, "same") for cid in ("c", "b", "a")])
    assert [c.id for c in memory_query.rank_claims(full, "same", FakeEmbedder(), strategy)] == [
        "a",
        "b",
        "c",
    ]


def test_lexical_tokens_include_key_path_and_unicode():
    assert memory_query.tokens("CAFÉ café; 雪, Alpha_Beta!") == {"café", "雪", "alpha_beta"}
    full = make_response(current_memories=[make_claim("a", "unrelated", key="CAFÉ", path="雪.md")])
    fake = FakeEmbedder()
    ranked = memory_query.rank_claims(full, "café 雪 absent", fake, "lexical")
    assert ranked == [memory_query.RankedClaim("a", pytest.approx(2 / 3))]
    assert memory_query.rank_claims(full, "?!", fake, "lexical")[0].score == 0
    assert fake.calls == []


@pytest.mark.parametrize("strategy", ["lexical", "embedding", "hybrid"])
def test_rank_empty_projection_does_not_embed(strategy):
    fake = FakeEmbedder()
    assert memory_query.rank_claims(make_response(), "database", fake, strategy) == []
    assert fake.calls == []


def test_rank_unknown_strategy_is_rejected(ranking_response):
    with pytest.raises(ValueError, match="Unknown memory ranking strategy"):
        memory_query.rank_claims(ranking_response, "database", FakeEmbedder(), "unknown")


@pytest.mark.parametrize("intent", INTENTS)
def test_select_stale_relevance_never_promotes_history_to_current(intent):
    old = make_claim("old", "legacy vocabulary", status="SUPERSEDED")
    new = make_claim("new", "replacement", supersedes=old.id)
    unrelated = make_claim("unrelated", "legacy vocabulary", key="unrelated", path="other.md")
    change = Change(
        version_id=new.version_id,
        predecessor_id=old.version_id,
        event="MODIFIED",
        path=new.path,
        observed_at=CLOCK,
        memory_changed=True,
        relationship="SUPERSEDES",
        previous=[old],
        current=[new],
    )
    full = make_response(
        current_memories=[unrelated, new], historical_memories=[old], changes=[change]
    )
    before = full.model_dump()
    fake = FakeEmbedder({representation(old): 1.0}, default=0.0)
    request = MemoryRequest(corpus="test", query="legacy vocabulary", intent=intent, as_of=CLOCK)
    selected = memory_query.select(full, request, memory_query.interpret(request), fake)
    assert selected.current_memories == [new]
    assert selected.current_memories[0].status == "CURRENT"
    assert selected.current_memories[0].supersedes_id == old.id
    assert selected.historical_memories == (
        [old] if intent in {"historical", "change", "provenance"} else []
    )
    assert selected.changes == ([change] if intent in {"historical", "change"} else [])
    assert selected.uncertain_memories == selected.conflicts == []
    assert unrelated.id not in {c.id for c in public.all_claims(selected)}
    assert selected.query == request.query
    assert selected.state == full.state
    public.assert_grounded(selected)
    assert full.model_dump() == before


@pytest.mark.parametrize(
    "scores,expected",
    [((1.0, 0.9, 0.899), {"a", "b"}), ((0.3, 0.299, -1.0), {"a"}), ((0.29, 0.0, -1.0), set())],
)
def test_select_relevance_floor_and_top_score_band(scores, expected):
    claims = [make_claim(cid, cid, key=cid) for cid in "abc"]
    full = make_response(current_memories=claims)
    fake = FakeEmbedder({representation(c): score for c, score in zip(claims, scores)})
    request = MemoryRequest(corpus="test", query="question")
    result = memory_query.select(full, request, memory_query.interpret(request), fake)
    assert {c.id for c in result.current_memories} == expected
    public.assert_grounded(result)


@pytest.fixture
def conflict_response():
    left = make_claim("left", 'red 雪 "quoted" \\', path="left.md", status="CONFLICTING")
    right = make_claim("right", "blue café", path="right.md", status="CONFLICTING")
    same = make_claim("same", left.claim, path="same.md", status="CONFLICTING")
    change = Change(
        version_id=left.version_id,
        predecessor_id=None,
        event="NEW",
        path=left.path,
        observed_at=CLOCK,
        memory_changed=True,
        relationship="LIFECYCLE",
        previous=[],
        current=[left],
    )
    return make_response(
        conflicts=[
            Conflict(id="left-right", key="database", claims=[left, right]),
            Conflict(id="right-same", key="database", claims=[right, same]),
        ],
        changes=[change],
    )


def assert_conflict_closure(response, full):
    present = {c.id for c in public.all_claims(response)}
    groups = {g.id: g for g in response.conflicts}
    for group in full.conflicts:
        if present & {c.id for c in group.claims}:
            assert groups.get(group.id) == group


@pytest.mark.parametrize("intent", ["current", "conflict", "historical", "change", "provenance"])
def test_select_path_keeps_transitive_counter_sources(conflict_response, intent):
    request = MemoryRequest(corpus="test", query="red", path="left.md", intent=intent)
    full = conflict_response
    fake = FakeEmbedder({representation(full.conflicts[0].claims[0]): 1.0}, default=0.0)
    result = memory_query.select(full, request, memory_query.interpret(request), fake)
    assert result.current_memories == result.historical_memories == result.uncertain_memories == []
    assert result.conflicts == full.conflicts
    assert {e.path for e in result.evidence} == {"left.md", "right.md", "same.md"}
    assert result.changes == (full.changes if intent in {"historical", "change"} else [])
    public.assert_grounded(result)


@pytest.mark.parametrize("budget", BUDGETS)
async def test_offline_query_budgets_preserve_atomic_closure_and_determinism(
    conflict_response,
    offline_embedder,
    budget,
):
    request = MemoryRequest(
        corpus="test", query="red", path="left.md", intent="change", budget=budget
    )
    before = conflict_response.model_dump()
    result = await memory_query.query(conflict_response, request)
    repeated = await memory_query.query(conflict_response, request)
    assert public.canonical(result) == public.canonical(repeated)
    assert len(public.canonical(result)) <= budget
    assert result.budget_unit == "unicode_characters"
    assert_conflict_closure(result, conflict_response)
    public.assert_grounded(result)
    for change in result.changes:
        assert change == conflict_response.changes[0]
    if budget == 1000:
        assert not public.all_claims(result)
        assert result.truncated is True
    if budget == 16000:
        assert result.conflicts == conflict_response.conflicts
        assert result.changes == conflict_response.changes
        assert result.truncated is False
    assert offline_embedder.calls
    assert conflict_response.model_dump() == before


@pytest.mark.parametrize("failure", [OSError, RuntimeError, ValueError])
async def test_query_sanitizes_embedding_failures(monkeypatch, offline_embedder, failure):
    def fail(texts):
        raise failure("private model location")

    monkeypatch.setattr(offline_embedder, "embed", fail)
    full = make_response(current_memories=[make_claim("a", "database")])
    with pytest.raises(memory_public.MemoryError) as error:
        await memory_query.query(full, MemoryRequest(corpus="test", query="database"))
    assert (error.value.code, error.value.status_code) == ("memory_unavailable", 503)
    assert "private" not in error.value.message
    assert isinstance(error.value.__cause__, failure)


@pytest.mark.parametrize("intent", ["current", "historical", "change", "provenance"])
async def test_public_query_stale_lineage_current_is_archive_authority(
    public_db, offline_embedder, intent
):
    store = public_db
    first = await public.authored(store, "db.md", "legacy Redis", key="database")
    latest = await public.authored(store, "db.md", "PostgreSQL", key="database")
    await public.authored(store, "other.md", "unrelated", key="other")
    full = await public.call(store, "history", valid_at=latest["observed_at"])
    old = next(c for c in full.historical_memories if c.version_id == first["id"])
    current = next(c for c in full.current_memories if c.version_id == latest["id"])
    offline_embedder.default = 0.0
    offline_embedder.scores = {representation(old): 1.0}
    result = await public.call(
        store,
        "query",
        query="legacy Redis",
        intent=intent,
        valid_at=latest["observed_at"],
        budget=128000,
    )
    assert result.current_memories == [current]
    assert current.status == "CURRENT" and current.supersedes_id == old.id
    assert old.status == "SUPERSEDED"
    assert result.historical_memories == ([old] if intent != "current" else [])
    assert result.uncertain_memories == result.conflicts == []
    if intent in {"historical", "change"}:
        assert [c.version_id for c in result.changes] == [first["id"], latest["id"]]
        assert result.changes[-1].previous == [old]
        assert result.changes[-1].current == [current]
    else:
        assert result.changes == []
    assert {e.path for e in result.evidence} == {"db.md"}
    assert offline_embedder.calls[0][0] == "legacy Redis"
    assert representation(old) in offline_embedder.calls[0][1:]
    public.assert_grounded(result)


async def test_public_query_temporal_boundaries_snapshot_and_explicit_precedence(
    public_db, offline_embedder
):
    store = public_db
    first = await public.record(store.db, store.corpus, "A")
    saved = await public.call(store, "snapshot", as_of=first["observed_at"])
    second = await public.record(store.db, store.corpus, "B")
    assert await memory.record_deletion(store.db, store.corpus, "note.md")
    deleted = (await memory.history(store.db, store.corpus, "note.md"))[-1]
    restored = await public.record(store.db, store.corpus, "C")
    for cutoff, expected in (
        (first["observed_at"] - timedelta(microseconds=1), []),
        (first["observed_at"], ["A"]),
        (second["observed_at"], ["B"]),
        (deleted["observed_at"], []),
        (restored["observed_at"], ["C"]),
    ):
        result = await public.call(
            store,
            "query",
            query="Stage B: compare 1999-01-01 and 2024-02-30",
            as_of=cutoff,
            budget=128000,
        )
        assert [c.value for c in result.current_memories] == expected
        assert result.state.as_of == result.state.valid_at == cutoff
        assert result.historical_memories == result.uncertain_memories == result.changes == []
        public.assert_grounded(result)
    replay_fields = dict(
        query="Stage A on 1900-01-01", snapshot_id=saved.snapshot.id, budget=128000
    )
    replay = await public.call(store, "query", **replay_fields)
    assert [c.value for c in replay.current_memories] == ["A"]
    assert replay.state == saved.state
    assert replay.state.snapshot == saved.snapshot.id
    await public.record(store.db, store.corpus, "D")
    assert public.canonical(await public.call(store, "query", **replay_fields)) == public.canonical(
        replay
    )
    dated = await public.call(store, "query", query="What was current on 1900-01-01?")
    assert dated.state.as_of == dated.state.valid_at == datetime(1900, 1, 1, tzinfo=timezone.utc)
    assert not public.all_claims(dated)
    public.assert_grounded(replay)


async def test_public_query_deleted_memory_has_history_not_current(public_db, offline_embedder):
    store = public_db
    await public.authored(store, "deleted.md", "archived 雪")
    assert await memory.record_deletion(store.db, store.corpus, "deleted.md")
    tombstone = (await memory.history(store.db, store.corpus, "deleted.md"))[-1]
    fields = dict(
        query="archived", path="deleted.md", valid_at=tombstone["observed_at"], budget=128000
    )
    current = await public.call(store, "query", intent="current", **fields)
    assert not public.all_claims(current)
    assert current.evidence == current.sources == []
    full = await public.call(store, "history", path="deleted.md", valid_at=tombstone["observed_at"])
    assert len(full.historical_memories) == 1
    for intent in ("historical", "change", "provenance"):
        result = await public.call(store, "query", intent=intent, **fields)
        assert result.current_memories == []
        assert result.historical_memories == full.historical_memories
        assert result.uncertain_memories == full.uncertain_memories
        assert {c.status for c in public.all_claims(result)} == {"UNCERTAIN"}
        assert result.evidence == full.evidence
        assert result.changes == (full.changes if intent != "provenance" else [])
        if intent != "provenance":
            deletion = result.changes[-1]
            assert deletion.event == "DELETED" and deletion.current == []
            assert deletion.previous == result.historical_memories
        public.assert_grounded(result)


@pytest.mark.parametrize("budget", BUDGETS)
@pytest.mark.parametrize("intent", ["current", "conflict", "historical"])
async def test_public_query_conflict_closure_history_budgets_and_determinism(
    public_db,
    offline_embedder,
    budget,
    intent,
):
    store = public_db
    await public.authored(store, "left.md", "old 雪")
    await public.authored(store, "left.md", "red 雪")
    await public.authored(store, "right.md", "blue café")
    last = await public.authored(store, "same.md", "red 雪")
    full = await public.call(store, "history", valid_at=last["observed_at"])
    assert len(full.conflicts) == 2
    old = full.historical_memories[0]
    offline_embedder.default = 0.0
    offline_embedder.scores = {representation(old): 1.0}
    fields = dict(
        query="old 雪", path="left.md", intent=intent, valid_at=last["observed_at"], budget=budget
    )
    result = await public.call(store, "query", **fields)
    assert result.current_memories == []
    assert len(public.canonical(result)) <= budget
    assert public.canonical(result) == public.canonical(await public.call(store, "query", **fields))
    assert_conflict_closure(result, full)
    public.assert_grounded(result)
    expected_changes = {c.version_id: c for c in full.changes if c.path == "left.md"}
    for change in result.changes:
        assert change == expected_changes[change.version_id]
    if intent != "historical":
        assert result.historical_memories == result.uncertain_memories == result.changes == []
    if budget == 1000:
        assert not public.all_claims(result)
        assert result.truncated is True
    if budget == 16000:
        assert result.conflicts == full.conflicts
        assert {e.path for e in result.evidence} == {"left.md", "right.md", "same.md"}
        if intent == "historical":
            assert result.historical_memories == [old]
            assert result.changes == list(expected_changes.values())
        assert result.truncated is False


@pytest.mark.parametrize(
    "fields,code",
    [
        ({"query": ""}, "invalid_query"),
        ({"query": "On 2024-02-30"}, "invalid_timestamp"),
        ({"query": "2024-01-01 and 2024-01-02"}, "invalid_timestamp"),
        ({"query": "Stage B"}, "invalid_timestamp"),
        ({"query": "database", "intent": "temporal"}, "invalid_timestamp"),
        ({"query": "database", "snapshot_id": "a" * 64, "as_of": CLOCK}, "invalid_request"),
        ({"query": "database", "snapshot_id": "a" * 64, "valid_at": CLOCK}, "invalid_request"),
    ],
)
async def test_public_query_invalid_selectors_fail_before_embedding(
    public_db, offline_embedder, fields, code
):
    with pytest.raises(memory_public.MemoryError) as error:
        await public.call(public_db, "query", **fields)
    assert (error.value.code, error.value.status_code) == (code, 422)
    assert offline_embedder.calls == []
