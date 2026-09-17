"""Public memory integration contracts, using the real archive and PostgreSQL.

Run with DATABASE_URL pointing at an isolated-test-capable PostgreSQL role and
HF_HUB_OFFLINE=1. The imported core fixture creates a random schema inside a
rolled-back transaction; no production schema or live index is changed. Adapter
tests replace only session ownership and synchronous HTTP transport, never the
executor, projection, archive, or bounded-selection semantics.
"""

from __future__ import annotations

import asyncio
import json
from contextlib import asynccontextmanager
from datetime import datetime, timedelta, timezone
from functools import partial
from time import perf_counter
from types import SimpleNamespace

import httpx
import pytest
from pydantic import ValidationError
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError

from api.models.memory import MemoryRequest, MemoryResponse, State
from api.services import memory, memory_public

try:
    from tests import test_memory_core as core
except ModuleNotFoundError as exc:
    if exc.name not in {"tests", "tests.test_memory_core"}:
        raise
    import test_memory_core as core

memory_db = core.memory_db
payload = core.payload
record = core.record


@pytest.fixture
async def public_db(memory_db):
    db, corpus, foreign, engine = memory_db
    # The core fixture deliberately has only the legacy corpus columns.
    await db.execute(text("ALTER TABLE corpora ADD COLUMN description TEXT NOT NULL DEFAULT ''"))
    return SimpleNamespace(
        db=db,
        corpus=corpus,
        foreign=foreign,
        name="memory-test-" + corpus,
        foreign_name="memory-test-" + foreign,
        engine=engine,
    )


async def call(store, operation, **fields):
    return await memory_public.execute_in_session(
        store.db, operation, MemoryRequest(corpus=store.name, **fields)
    )


def all_claims(response):
    return (
        response.current_memories
        + response.historical_memories
        + response.uncertain_memories
        + [claim for conflict in response.conflicts for claim in conflict.claims]
        + [claim for change in response.changes for claim in change.previous + change.current]
    )


def assert_grounded(response):
    """Every selected unit has complete provenance, with no orphan attachments."""
    claims = all_claims(response)
    evidence = {item.id: item for item in response.evidence}
    assert len(evidence) == len(response.evidence)
    assert set(evidence) == {eid for claim in claims for eid in claim.evidence_ids}
    sources = {source.version_id: source for source in response.sources}
    assert len(sources) == len(response.sources)
    assert set(sources) == {item.version_id for item in evidence.values()}
    for claim in claims:
        assert claim.evidence_ids
        for eid in claim.evidence_ids:
            item = evidence[eid]
            assert item.claim_id == claim.id
            assert item.version_id == claim.version_id
            assert item.path == claim.path
            assert item.observed_at == claim.observed_at
            assert item.end_offset - item.start_offset == len(item.text)
            source = sources[item.version_id]
            assert source.document_id == item.document_id
            assert source.path == item.path
            assert source.source_hash == item.source_hash
            assert source.observed_at == item.observed_at


def canonical(response):
    # Independent byte/character accounting, not just the production length helper.
    encoded = json.dumps(
        response.model_dump(mode="json"),
        sort_keys=True,
        ensure_ascii=False,
        separators=(",", ":"),
        allow_nan=False,
    )
    assert response.canonical_json() == encoded
    assert MemoryResponse.model_validate_json(encoded) == response
    return encoded


async def authored(store, path, value, *, key="decision", **validity):
    quote = f'Decision is {value}.\nQuoted "雪" and a backslash \\ stay intact.'
    chunk = "Préface 🙂\n" + quote + "\nEnd."
    return await memory.record_version(
        store.db,
        store.corpus,
        path,
        "live-" + path,
        chunk,
        {"claims": [{"key": key, "value": value, "claim": quote, "evidence": quote, **validity}]},
        [{"text": chunk, "heading_path": 'Résumé "雪"', "order_index": 0}],
    )


async def test_current_history_changes_and_archived_evidence(public_db):
    store = public_db
    first = await record(store.db, store.corpus, "A")
    before = await call(store, "current", valid_at=first["observed_at"])
    old_claim = before.current_memories[0]
    second = await record(store.db, store.corpus, "B")
    current = await call(store, "current", valid_at=second["observed_at"])
    assert [claim.value for claim in current.current_memories] == ["B"]
    assert current.current_memories[0].supersedes_id == old_claim.id
    assert current.historical_memories == current.uncertain_memories == current.changes == []
    assert all(claim.status == "CURRENT" for claim in current.current_memories)
    assert old_claim.id not in {claim.id for claim in all_claims(current)}

    history = await call(store, "history", path="note.md", valid_at=second["observed_at"])
    assert [claim.value for claim in history.historical_memories] == ["A"]
    assert history.historical_memories[0].status == "SUPERSEDED"
    changes = await call(store, "changes", path="note.md", valid_at=second["observed_at"])
    assert changes.changes == history.changes
    assert changes.current_memories == changes.historical_memories == []
    change = changes.changes[-1]
    assert change.version_id == second["id"]
    assert change.predecessor_id == first["id"]
    assert change.event == "MODIFIED"
    assert change.memory_changed is True
    assert change.relationship == "SUPERSEDES"
    assert [claim.value for claim in change.previous] == ["A"]
    assert [claim.value for claim in change.current] == ["B"]

    evidence = await call(store, "evidence", claim_id=old_claim.id)
    assert {claim.id for claim in all_claims(evidence)} == {old_claim.id}
    assert evidence.historical_memories[0].status == "SUPERSEDED"
    assert len(evidence.evidence) == len(evidence.sources) == 1
    archived = (await memory.history(store.db, store.corpus, "note.md"))[0]
    item = evidence.evidence[0]
    chunk = next(chunk for chunk in archived["chunks"] if chunk["id"] == item.chunk_id)
    assert chunk["text"][item.start_offset : item.end_offset] == item.text == "A"
    assert item.source_hash == archived["fingerprint"]
    assert item.heading == "Details"
    for response in (before, current, history, changes, evidence):
        assert_grounded(response)


async def test_metadata_only_change_is_not_a_memory_change(public_db):
    store = public_db
    first = await record(store.db, store.corpus, "A")
    second = await record(
        store.db, store.corpus, "A", metadata={**payload("A"), "title": "Metadata only"}
    )
    changes = await call(store, "changes")
    assert len(changes.changes) == 2
    change = changes.changes[-1]
    assert change.version_id == second["id"] != first["id"]
    assert change.predecessor_id == first["id"]
    assert change.event == "MODIFIED"
    assert change.memory_changed is False
    assert change.relationship == "DOCUMENT_MODIFIED"
    assert change.previous[0].id != change.current[0].id
    assert change.current[0].supersedes_id == change.previous[0].id
    assert change.previous[0].value == change.current[0].value == "A"
    assert_grounded(changes)


async def test_a_b_delete_restore_and_as_of_boundaries(public_db):
    store = public_db
    first = await record(store.db, store.corpus, "A")
    second = await record(store.db, store.corpus, "B")
    assert await memory.record_deletion(store.db, store.corpus, "note.md")
    tombstone = (await memory.history(store.db, store.corpus, "note.md"))[-1]
    deleted = await call(store, "current", path="note.md")
    assert not all_claims(deleted)
    assert deleted.evidence == deleted.sources == []
    restored = await record(store.db, store.corpus, "A")
    current = await call(store, "current")
    assert [claim.value for claim in current.current_memories] == ["A"]
    assert current.current_memories[0].version_id == restored["id"]
    assert current.current_memories[0].supersedes_id is None
    assert restored["memory_document_id"] == first["memory_document_id"]
    changes = await call(store, "changes")
    assert [change.event for change in changes.changes] == [
        "NEW",
        "MODIFIED",
        "DELETED",
        "RESTORED",
    ]
    assert all(change.memory_changed for change in changes.changes)
    deletion, restoration = changes.changes[-2:]
    assert [claim.value for claim in deletion.previous] == ["B"]
    assert deletion.current == []
    assert restoration.previous == []
    assert [claim.value for claim in restoration.current] == ["A"]
    assert deletion.relationship == restoration.relationship == "LIFECYCLE"
    assert restoration.predecessor_id == tombstone["id"]
    for cutoff, expected in (
        (first["observed_at"] - timedelta(microseconds=1), []),
        (first["observed_at"], ["A"]),
        (second["observed_at"], ["B"]),
        (tombstone["observed_at"], []),
        (restored["observed_at"], ["A"]),
    ):
        response = await call(store, "as-of", as_of=cutoff)
        assert response.state.as_of == response.state.valid_at == cutoff
        assert [claim.value for claim in response.current_memories] == expected
        assert response.historical_memories == response.uncertain_memories == []
        assert_grounded(response)
    assert_grounded(changes)


async def test_validity_clock_is_distinct_from_observation_cutoff(public_db):
    store = public_db
    version = await authored(
        store, "dated.md", "A", valid_from="1999-01-01", valid_until="1999-02-01"
    )
    present = await call(store, "current")
    assert not all_claims(present)
    history = await call(store, "history")
    assert len(history.uncertain_memories) == 1
    assert history.uncertain_memories[0].status == "UNCERTAIN"
    start = datetime(1999, 1, 1, tzinfo=timezone.utc)
    active = await call(store, "as-of", as_of=version["observed_at"], valid_at=start)
    assert [claim.value for claim in active.current_memories] == ["A"]
    expired = await call(
        store, "as-of", as_of=version["observed_at"], valid_at=start + timedelta(days=31)
    )
    assert not all_claims(expired)
    assert_grounded(active)
    assert_grounded(history)


async def test_snapshot_replay_is_immutable_through_modify_delete_restore(public_db):
    store = public_db
    first = await record(store.db, store.corpus, "A")
    saved = await call(store, "snapshot", as_of=first["observed_at"])
    assert saved.snapshot is not None
    assert saved.snapshot.version_ids == [first["id"]]
    assert saved.state.snapshot == saved.snapshot.id
    assert saved.state.as_of == saved.state.valid_at == first["observed_at"]
    original = canonical(saved)
    for action in ("modify", "delete", "restore"):
        if action == "delete":
            await memory.record_deletion(store.db, store.corpus, "note.md")
        else:
            await record(store.db, store.corpus, "B" if action == "modify" else "C")
        replay = await call(store, "replay", snapshot_id=saved.snapshot.id)
        assert canonical(replay) == original
        assert_grounded(replay)
    repeated = await call(store, "snapshot", as_of=first["observed_at"])
    assert canonical(repeated) == original
    latest = await call(store, "snapshot")
    assert latest.snapshot.id != saved.snapshot.id
    assert len(latest.snapshot.version_ids) == 4
    assert [change.event for change in latest.changes] == ["NEW", "MODIFIED", "DELETED", "RESTORED"]
    packed = await call(store, "pack", snapshot_id=saved.snapshot.id, budget=128000)
    assert packed.state == saved.state
    assert [claim.value for claim in packed.current_memories] == ["A"]
    assert packed.changes == saved.changes
    assert len(canonical(packed)) <= 128000
    assert_grounded(packed)


async def test_corpus_isolation_and_foreign_claim_snapshot_path(public_db):
    store = public_db
    await record(store.db, store.corpus, "A")
    await record(store.db, store.foreign, "FOREIGN", path="foreign-only.md")
    foreign_current = await memory_public.execute_in_session(
        store.db, "current", MemoryRequest(corpus=store.foreign_name)
    )
    foreign_snapshot = await memory_public.execute_in_session(
        store.db, "snapshot", MemoryRequest(corpus=store.foreign_name)
    )
    foreign_claim = foreign_current.current_memories[0]
    for operation, selectors, code in (
        ("evidence", {"claim_id": foreign_claim.id}, "claim_not_found"),
        ("replay", {"snapshot_id": foreign_snapshot.snapshot.id}, "snapshot_not_found"),
        ("pack", {"snapshot_id": foreign_snapshot.snapshot.id}, "snapshot_not_found"),
        ("current", {"path": "foreign-only.md"}, "document_not_found"),
        ("history", {"path": "foreign-only.md"}, "document_not_found"),
    ):
        with pytest.raises(memory_public.MemoryError) as error:
            await call(store, operation, **selectors)
        assert (error.value.code, error.value.status_code) == (code, 404)
    for operation in ("current", "history", "changes", "snapshot", "pack"):
        response = await call(store, operation)
        assert "FOREIGN" not in canonical(response)
        assert foreign_claim.id not in {claim.id for claim in all_claims(response)}
        assert all(item.path == "note.md" for item in response.evidence)
        assert_grounded(response)
    assert not all_claims(await call(store, "history", query="FOREIGN"))


@pytest.mark.parametrize(
    "operation,selectors,code,message",
    [
        ("as-of", {}, "invalid_request", "as_of is required"),
        ("evidence", {}, "invalid_request", "claim_id is required"),
        ("replay", {}, "invalid_request", "snapshot_id is required"),
        ("unknown", {}, "invalid_operation", "Unknown memory operation"),
        ("current", {"as_of": "2020-01-01T00:00:00Z"}, "invalid_request", "as_of"),
        ("snapshot", {"query": "A"}, "invalid_request", "entire corpus"),
        ("evidence", {"claim_id": "a" * 64, "path": "note.md"}, "invalid_request", "path"),
        (
            "pack",
            {"snapshot_id": "a" * 64, "as_of": "2020-01-01T00:00:00Z"},
            "invalid_request",
            "temporal cutoffs",
        ),
        (
            "pack",
            {"snapshot_id": "a" * 64, "valid_at": "2020-01-01T00:00:00Z"},
            "invalid_request",
            "temporal cutoffs",
        ),
    ],
)
async def test_required_and_unsupported_selectors(public_db, operation, selectors, code, message):
    with pytest.raises(memory_public.MemoryError) as error:
        await call(public_db, operation, **selectors)
    assert (error.value.code, error.value.status_code) == (code, 422)
    assert message in error.value.message


@pytest.mark.parametrize("field", ["as_of", "valid_at"])
@pytest.mark.parametrize("value", ["2026-01-01", "2026-01-01T12:00:00", "not-a-time"])
def test_timestamp_contract_requires_an_aware_timestamp(field, value):
    with pytest.raises(ValidationError) as error:
        MemoryRequest(corpus="docs", **{field: value})
    assert error.value.errors()[0]["loc"] == (field,)


async def test_snapshot_future_cutoff_is_rejected(public_db):
    with pytest.raises(memory_public.MemoryError) as error:
        await call(public_db, "snapshot", as_of=datetime.now(timezone.utc) + timedelta(days=1))
    assert (error.value.code, error.value.status_code) == ("invalid_timestamp", 422)
    assert "future" in error.value.message


async def test_ids_and_paths_missing_at_selected_time(public_db):
    store = public_db
    first = await record(store.db, store.corpus, "A")
    claim = (await call(store, "current")).current_memories[0]
    before = first["observed_at"] - timedelta(microseconds=1)
    empty_snapshot = await call(store, "snapshot", as_of=before)
    for operation, fields, code in (
        ("evidence", {"claim_id": claim.id, "as_of": before}, "claim_not_found"),
        ("as-of", {"path": "note.md", "as_of": before}, "document_not_found"),
        (
            "replay",
            {"snapshot_id": empty_snapshot.snapshot.id, "path": "note.md"},
            "document_not_found",
        ),
    ):
        with pytest.raises(memory_public.MemoryError) as error:
            await call(store, operation, **fields)
        assert (error.value.code, error.value.status_code) == (code, 404)


async def test_project_and_pack_claim_evidence_source_unicode_boundaries(public_db):
    store = public_db
    version = await authored(store, 'notes/雪-"quoted".md', "café 🙂")
    request = MemoryRequest(corpus=store.name, query="café", valid_at=version["observed_at"])
    versions = await memory._load(store.db, store.corpus)
    state = State(valid_at=request.valid_at)
    full = memory_public.project(versions, request, "current", state)
    assert full == await memory_public.execute_in_session(store.db, "current", request)
    assert len(full.current_memories) == len(full.evidence) == len(full.sources) == 1
    encoded = canonical(full)
    assert "雪" in encoded and "🙂" in encoded and "café" in encoded
    assert "\\n" in encoded and '\\"' in encoded and "\\\\" in encoded
    assert len(encoded.encode("utf-8")) > len(encoded)
    evidence = full.evidence[0]
    chunk = versions[0]["chunks"][0]["text"]
    assert chunk[evidence.start_offset : evidence.end_offset] == evidence.text
    assert evidence.start_offset == len("Préface 🙂\n")

    reserved = full.model_copy(update={"truncated": True})
    boundary = len(canonical(reserved))
    original = canonical(full)
    for budget, count, truncated in (
        (boundary - 1, 0, True),
        (boundary, 1, True),
        (boundary + 1, 1, False),
    ):
        result = memory_public.bounded_pack(full, budget)
        assert len(canonical(result)) <= budget
        assert len(result.current_memories) == count
        assert len(result.evidence) == len(result.sources) == count
        assert result.truncated is truncated
        if count:
            assert result.current_memories == full.current_memories
            assert result.evidence == full.evidence
            assert result.sources == full.sources
        assert_grounded(result)
        assert result == memory_public.bounded_pack(full, budget)
    assert canonical(full) == original

    empty = MemoryResponse(query=full.query, corpus=full.corpus, state=state, truncated=True)
    envelope = len(canonical(empty))
    with pytest.raises(memory_public.MemoryError) as error:
        memory_public.bounded_pack(full, envelope - 1)
    assert (error.value.code, error.value.status_code) == ("invalid_budget", 422)
    assert memory_public.bounded_pack(full, envelope) == empty
    assert memory_public.bounded_pack(empty, envelope).truncated is True
    assert memory_public.bounded_pack(empty, envelope + 1).truncated is False


async def test_pack_keeps_all_conflict_sides_even_for_one_matching_path(public_db):
    store = public_db
    await authored(store, "left.md", "red 雪")
    right = await authored(store, "right.md", "blue café")
    request = MemoryRequest(
        corpus=store.name, query="red", path="left.md", valid_at=right["observed_at"]
    )
    full = await memory_public.execute_in_session(store.db, "current", request)
    assert full.current_memories == []
    assert len(full.conflicts) == 1
    assert {claim.path for claim in full.conflicts[0].claims} == {"left.md", "right.md"}
    assert {claim.status for claim in full.conflicts[0].claims} == {"CONFLICTING"}
    assert len(full.evidence) == len(full.sources) == 2
    boundary = len(canonical(full.model_copy(update={"truncated": True})))
    omitted = memory_public.bounded_pack(full, boundary - 1)
    assert omitted.conflicts == omitted.evidence == omitted.sources == []
    selected = memory_public.bounded_pack(full, boundary)
    assert selected.conflicts == full.conflicts
    assert selected.evidence == full.evidence
    assert selected.sources == full.sources
    assert len(canonical(selected)) == boundary
    assert_grounded(selected)
    for claim in full.conflicts[0].claims:
        evidence = await call(store, "evidence", claim_id=claim.id)
        assert evidence.current_memories == []
        assert evidence.conflicts == full.conflicts
        assert_grounded(evidence)

    # The actual pack includes changes before conflicts; every returned group,
    # including overlapping pairwise groups, must still keep all its sides.
    await authored(store, "third.md", "green 🙂")
    complete = await call(store, "history", query="red", path="left.md", valid_at=request.valid_at)
    expected = {group.id: group for group in complete.conflicts}
    assert len(expected) == 3  # All pairwise alternatives, including right ↔ third.
    assert len(complete.changes) == 1
    assert any(claim.status == "CONFLICTING" for claim in complete.changes[0].current)
    # This budget fits the change alone, but not its connected alternatives.
    change_only = complete.model_copy(deep=True)
    change_only.conflicts = []
    change_only.truncated = True
    ids = {eid for claim in all_claims(change_only) for eid in claim.evidence_ids}
    change_only.evidence = [item for item in complete.evidence if item.id in ids]
    versions = {item.version_id for item in change_only.evidence}
    change_only.sources = [item for item in complete.sources if item.version_id in versions]
    change_boundary = len(canonical(change_only))
    for budget in (512, boundary - 1, boundary, change_boundary, 8000, 16000, 128000):
        packed = await call(
            store, "pack", query="red", path="left.md", valid_at=request.valid_at, budget=budget
        )
        assert len(canonical(packed)) <= budget
        selected = {group.id: group for group in packed.conflicts}
        for group in packed.conflicts:
            assert group == expected[group.id]
        for claim in all_claims(packed):
            if claim.status != "CONFLICTING":
                continue
            connected = {claim.id}
            while True:
                expanded = connected | {
                    side.id
                    for group in complete.conflicts
                    if any(side.id in connected for side in group.claims)
                    for side in group.claims
                }
                if expanded == connected:
                    break
                connected = expanded
            required = {
                group.id
                for group in complete.conflicts
                if any(side.id in connected for side in group.claims)
            }
            assert required <= selected.keys()
            assert connected <= {side.id for group in packed.conflicts for side in group.claims}
        if budget == change_boundary:
            assert packed.changes == []
        if budget == 128000:
            assert packed.conflicts == complete.conflicts
            assert packed.truncated is False
        assert_grounded(packed)


async def test_path_filter_preserves_transitively_connected_alternatives(public_db):
    store = public_db
    await authored(store, "left.md", "red")
    await authored(store, "right.md", "blue")
    last = await authored(store, "same-as-left.md", "red")
    full = await call(store, "history", valid_at=last["observed_at"])
    assert len(full.conflicts) == 2
    expected_ids = {claim.id for group in full.conflicts for claim in group.claims}
    assert len(expected_ids) == 3
    packed = await call(store, "pack", path="left.md", valid_at=last["observed_at"], budget=128000)
    assert packed.truncated is False
    assert {change.path for change in packed.changes} == {"left.md"}
    assert_grounded(packed)
    assert {claim.id for group in packed.conflicts for claim in group.claims} == expected_ids
    assert packed.conflicts == full.conflicts


async def test_pack_change_previous_and_current_are_atomic(public_db):
    store = public_db
    await authored(store, "note.md", "A 雪")
    version = await authored(store, "note.md", "B café")
    full = await call(store, "changes", valid_at=version["observed_at"])
    expected = {change.version_id: change for change in full.changes}
    boundary = len(canonical(full.model_copy(update={"truncated": True})))
    first_only = full.model_copy(deep=True)
    first_only.changes = full.changes[:1]
    ids = {eid for claim in all_claims(first_only) for eid in claim.evidence_ids}
    first_only.evidence = [item for item in full.evidence if item.id in ids]
    versions = {item.version_id for item in first_only.evidence}
    first_only.sources = [item for item in full.sources if item.version_id in versions]
    first_only.truncated = True
    first_boundary = len(canonical(first_only))
    for budget in (first_boundary - 1, first_boundary, boundary - 1, boundary, boundary + 1):
        packed = memory_public.bounded_pack(full, budget)
        assert len(canonical(packed)) <= budget
        assert_grounded(packed)
        for change in packed.changes:
            assert change == expected[change.version_id]
        if budget == first_boundary:
            assert packed == first_only
        if budget >= boundary:
            assert packed.changes == full.changes
    complete = await call(store, "history", valid_at=version["observed_at"])
    public = await call(store, "pack", valid_at=version["observed_at"], budget=8000)
    assert public == memory_public.bounded_pack(complete, 8000)


@pytest.fixture
async def interfaces(public_db, monkeypatch):
    from typer.testing import CliRunner

    import mcp_server
    from api.main import app
    from cli.main import app as cli_app
    from mindpalace_sdk import MindPalace

    class SavepointSession:
        def begin(self):
            return public_db.db.begin_nested()

        def __getattr__(self, name):
            return getattr(public_db.db, name)

    @asynccontextmanager
    async def session_scope():
        # execute() owns begin(), but the imported fixture already owns the
        # outer transaction. A real savepoint keeps both ownership rules intact.
        yield SavepointSession()

    monkeypatch.setattr(memory_public, "session_scope", session_scope)
    loop = asyncio.get_running_loop()
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://memory-test"
    ) as rest:

        def post(url, **kwargs):
            # SDK/CLI run in a worker; ASGI and asyncpg stay on the fixture loop.
            future = asyncio.run_coroutine_threadsafe(rest.post(url, **kwargs), loop)
            return future.result(timeout=30)

        monkeypatch.setattr(httpx, "post", post)
        sdk = MindPalace(base_url="http://memory-test").memory

        async def sdk_call(operation, request):
            fields = request.model_dump(mode="json", exclude_unset=True)
            method = {"as-of": "as_of", "replay": "replay_snapshot"}.get(operation, operation)
            if operation == "as-of":
                fields["timestamp"] = fields.pop("as_of")
            return await asyncio.to_thread(partial(getattr(sdk, method), **fields))

        async def cli_call(operation, request):
            args = ["memory", operation, "--base-url", "http://memory-test"]
            for name, value in request.model_dump(mode="json", exclude_unset=True).items():
                args.extend(["--" + name.replace("_", "-"), str(value)])
            return await asyncio.to_thread(CliRunner().invoke, cli_app, args)

        yield SimpleNamespace(rest=rest, sdk=sdk_call, cli=cli_call, mcp=mcp_server.mcp)


@pytest.mark.parametrize(
    "operation",
    ["current", "history", "changes", "evidence", "as-of", "snapshot", "replay", "pack"],
)
async def test_real_rest_sdk_mcp_cli_consistency(public_db, interfaces, operation):
    store = public_db
    first = await authored(store, "note.md", "A 雪")
    old = (await call(store, "current")).current_memories[0]
    await authored(store, "note.md", "B café")
    await memory.record_deletion(store.db, store.corpus, "note.md")
    await authored(store, "note.md", "C 🙂")
    last = await authored(store, "conflict.md", "D 雪")
    selectors = {
        "current": {"valid_at": last["observed_at"]},
        "history": {"as_of": last["observed_at"]},
        "changes": {"as_of": last["observed_at"]},
        "evidence": {"claim_id": old.id, "as_of": last["observed_at"]},
        "as-of": {"as_of": first["observed_at"]},
        "snapshot": {"as_of": last["observed_at"]},
        "pack": {"as_of": last["observed_at"], "budget": 8000},
    }
    if operation == "replay":
        saved = await call(store, "snapshot", as_of=first["observed_at"])
        selectors["replay"] = {"snapshot_id": saved.snapshot.id}
    request = MemoryRequest(corpus=store.name, **selectors[operation])
    expected = await memory_public.execute(operation, request)
    assert all_claims(expected)
    assert_grounded(expected)
    rest = await interfaces.rest.post(
        f"/api/memory/{operation}", json=request.model_dump(mode="json")
    )
    assert rest.status_code == 200, rest.text
    assert rest.json() == expected.model_dump(mode="json")
    tool = await interfaces.mcp.call_tool(
        "memory_" + operation.replace("-", "_"), {"request": request.model_dump(mode="json")}
    )
    assert not tool.is_error
    assert tool.structured_content == expected.model_dump(mode="json")
    assert json.loads(tool.content[0].text) == tool.structured_content
    sdk = await interfaces.sdk(operation, request)
    assert canonical(sdk) == canonical(expected)
    cli = await interfaces.cli(operation, request)
    assert cli.exit_code == 0, (cli.output, cli.exception)
    assert cli.stdout.strip() == canonical(expected)


@pytest.mark.parametrize(
    "operation,fields,code,message",
    [
        ("current", {"corpus": "does-not-exist"}, "corpus_not_found", "Corpus not found"),
        (
            "current",
            {"path": "absent.md"},
            "document_not_found",
            "Document not found in this corpus/state",
        ),
        (
            "evidence",
            {"claim_id": "0" * 64},
            "claim_not_found",
            "Claim not found in this corpus/state",
        ),
        (
            "replay",
            {"snapshot_id": "0" * 64},
            "snapshot_not_found",
            "Snapshot not found in this corpus",
        ),
    ],
)
async def test_missing_ids_errors_across_real_interfaces(
    public_db, interfaces, operation, fields, code, message
):
    from mindpalace_sdk import MemoryClientError

    request = MemoryRequest(**{"corpus": public_db.name, **fields})
    with pytest.raises(memory_public.MemoryError) as error:
        await memory_public.execute(operation, request)
    assert (error.value.code, error.value.message, error.value.status_code) == (code, message, 404)
    expected = {"detail": {"code": code, "message": message}}
    rest = await interfaces.rest.post(
        f"/api/memory/{operation}", json=request.model_dump(mode="json")
    )
    assert rest.status_code == 404
    assert rest.json() == expected
    tool = await interfaces.mcp.call_tool(
        "memory_" + operation.replace("-", "_"), {"request": request.model_dump(mode="json")}
    )
    assert tool.is_error
    assert tool.structured_content == expected
    with pytest.raises(MemoryClientError) as error:
        await interfaces.sdk(operation, request)
    assert (error.value.code, error.value.message, error.value.status_code) == (code, message, 404)
    cli = await interfaces.cli(operation, request)
    assert cli.exit_code == 1
    assert f"{code}: {message}" in cli.output


@pytest.mark.parametrize(
    "statement,sqlstate,code,message",
    [
        (
            "SELECT * FROM pg_catalog.memory_private_missing_table",
            "42P01",
            "memory_unavailable",
            "Memory schema unavailable; apply migrations",
        ),
        (
            "SELECT memory_private_missing_column FROM pg_catalog.pg_class",
            "42703",
            "memory_unavailable",
            "Memory schema unavailable; apply migrations",
        ),
        (
            "SELECT 1 / 0 AS memory_private_query",
            "22012",
            "database_unavailable",
            "Memory database request failed",
        ),
    ],
)
async def test_execute_translates_real_backend_failure(
    public_db, monkeypatch, statement, sqlstate, code, message
):
    attempted = []

    class FailingSession:
        def begin(self):
            return public_db.db.begin_nested()

        async def execute(self, query, *args, **kwargs):
            attempted.append(str(query))
            return await public_db.db.execute(text(statement))

    @asynccontextmanager
    async def session_scope():
        yield FailingSession()

    monkeypatch.setattr(memory_public, "session_scope", session_scope)
    with pytest.raises(memory_public.MemoryError) as error:
        await memory_public.execute("current", MemoryRequest(corpus=public_db.name))
    assert len(attempted) == 1
    assert (error.value.code, error.value.message, error.value.status_code) == (code, message, 503)
    assert isinstance(error.value.__cause__, DBAPIError)
    assert error.value.__cause__.orig.sqlstate == sqlstate
    assert "memory_private" in str(error.value.__cause__)
    assert str(error.value) == message
    assert "memory_private" not in repr(error.value)
    assert statement not in error.value.message
    # The failed request rolled back its savepoint, not the fixture's transaction.
    assert (await public_db.db.execute(text("SELECT 1"))).scalar_one() == 1


async def test_missing_timestamp_validation_through_real_rest_and_mcp(public_db, interfaces):
    from mcp.server.mcpserver.exceptions import ToolError

    body = {"corpus": public_db.name}
    rest = await interfaces.rest.post("/api/memory/as-of", json=body)
    assert rest.status_code == 422
    assert rest.json() == {"detail": {"code": "invalid_request", "message": "as_of is required"}}
    tool = await interfaces.mcp.call_tool("memory_as_of", {"request": body})
    assert tool.is_error
    assert tool.structured_content == rest.json()
    for timestamp in ("2026-01-01", "2026-01-01T12:00:00", "invalid"):
        invalid = {**body, "as_of": timestamp}
        response = await interfaces.rest.post("/api/memory/as-of", json=invalid)
        assert response.status_code == 422
        assert response.json()["detail"]["code"] == "invalid_request"
        with pytest.raises(ToolError):
            await interfaces.mcp.call_tool("memory_as_of", {"request": invalid})


async def test_performance_100_documents_three_versions(public_db, record_property, capsys):
    store = public_db
    for index in range(100):
        for version in range(3):
            last = await authored(
                store, f"document-{index:03d}.md", f"revision {version} 雪", key=f"decision-{index}"
            )
    current = await call(store, "current", valid_at=last["observed_at"])
    assert len(current.current_memories) == 100
    claim_id = current.current_memories[0].id
    saved = await call(store, "snapshot", as_of=last["observed_at"])
    selectors = {
        "current": {"valid_at": last["observed_at"]},
        "history": {"as_of": last["observed_at"]},
        "changes": {"as_of": last["observed_at"]},
        "evidence": {"claim_id": claim_id, "as_of": last["observed_at"]},
        "as-of": {"as_of": last["observed_at"]},
        "pack": {"as_of": last["observed_at"], "budget": 16000},
        "replay": {"snapshot_id": saved.snapshot.id},
    }
    timings = {}
    results = {}
    for operation, fields in selectors.items():
        started = perf_counter()
        results[operation] = await call(store, operation, **fields)
        timings[operation] = perf_counter() - started
        record_property(f"memory_{operation}_seconds", timings[operation])
        print(f"memory_public {operation}: {timings[operation]:.6f}s (100 docs, 300 versions)")
    captured = capsys.readouterr().out
    assert len(captured.splitlines()) == len(selectors)
    for operation in selectors:
        assert f"memory_public {operation}:" in captured
    # Retain the captured measurements in pytest output even on a successful run.
    with capsys.disabled():
        print(captured, end="")
    assert len(results["history"].historical_memories) == 200
    assert len(results["changes"].changes) == 300
    assert len(results["current"].current_memories) == 100
    assert len(results["as-of"].current_memories) == 100
    assert canonical(results["replay"]) == canonical(saved)
    assert {claim.id for claim in all_claims(results["evidence"])} == {claim_id}
    assert results["pack"].truncated is True
    assert len(canonical(results["pack"])) <= 16000
    for response in results.values():
        assert_grounded(response)
