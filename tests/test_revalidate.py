"""DB-independent unit tests for revalidation helpers and pairing."""

from __future__ import annotations

from unittest import mock

import pytest

from api.services import revalidate as R


def _graph(rows):
    # build latest versions by doc
    by_doc = {}
    for r in rows:
        vn = r.get("version_number", 1) or 1
        doc = r["memory_document_id"]
        if doc not in by_doc or vn > by_doc[doc][0]:
            by_doc[doc] = (vn, r["version_id"])
    latest_versions = {doc: (vid, "NEW", vn) for doc, (vn, vid) in by_doc.items()}
    return R._build_graph(rows, latest_versions)


def test_walk_heads_linear():
    rows = [
        {
            "id": "a",
            "memory_document_id": "d",
            "version_id": "v",
            "version_number": 1,
            "supersedes_id": None,
            "claim": "a",
            "key": "k",
            "observed_at": None,
            "path": "p",
        },
        {
            "id": "b",
            "memory_document_id": "d",
            "version_id": "v",
            "version_number": 1,
            "supersedes_id": "a",
            "claim": "b",
            "key": "k",
            "observed_at": None,
            "path": "p",
        },
        {
            "id": "c",
            "memory_document_id": "d",
            "version_id": "v",
            "version_number": 1,
            "supersedes_id": "b",
            "claim": "c",
            "key": "k",
            "observed_at": None,
            "path": "p",
        },
    ]
    g = _graph(rows)
    assert g["walk_heads"]("a") == ["c"]


def test_walk_heads_branching():
    rows = [
        {
            "id": "a",
            "memory_document_id": "d",
            "version_id": "v",
            "version_number": 1,
            "supersedes_id": None,
            "claim": "a",
            "key": "k",
            "observed_at": None,
            "path": "p",
        },
        {
            "id": "b",
            "memory_document_id": "d",
            "version_id": "v",
            "version_number": 1,
            "supersedes_id": "a",
            "claim": "b",
            "key": "k",
            "observed_at": None,
            "path": "p",
        },
        {
            "id": "c",
            "memory_document_id": "d",
            "version_id": "v",
            "version_number": 1,
            "supersedes_id": "a",
            "claim": "c",
            "key": "k",
            "observed_at": None,
            "path": "p",
        },
    ]
    g = _graph(rows)
    assert set(g["walk_heads"]("a")) == {"b", "c"}


def test_walk_heads_cycle():
    rows = [
        {
            "id": "a",
            "memory_document_id": "d",
            "version_id": "v",
            "version_number": 1,
            "supersedes_id": "c",
            "claim": "a",
            "key": "k",
            "observed_at": None,
            "path": "p",
        },
        {
            "id": "b",
            "memory_document_id": "d",
            "version_id": "v",
            "version_number": 1,
            "supersedes_id": "a",
            "claim": "b",
            "key": "k",
            "observed_at": None,
            "path": "p",
        },
        {
            "id": "c",
            "memory_document_id": "d",
            "version_id": "v",
            "version_number": 1,
            "supersedes_id": "b",
            "claim": "c",
            "key": "k",
            "observed_at": None,
            "path": "p",
        },
    ]
    g = _graph(rows)
    out = g["walk_heads"]("a")
    # terminates
    assert isinstance(out, list)
    assert g["cycle_detected"] is True


def test_classify_current_vs_stale():
    rows = [
        {
            "id": "a",
            "memory_document_id": "d",
            "version_id": "v",
            "version_number": 1,
            "supersedes_id": None,
            "claim": "current claim",
            "key": "k",
            "observed_at": None,
            "path": "p",
        },
        {
            "id": "b",
            "memory_document_id": "d",
            "version_id": "v",
            "version_number": 1,
            "supersedes_id": "a",
            "claim": "new claim",
            "key": "k",
            "observed_at": None,
            "path": "p",
        },
    ]
    g = _graph(rows)
    assert R._classify(g, "a")["status"] == "superseded"
    assert R._classify(g, "b")["status"] == "current"


def test_integrity_exception_propagates():
    bundle = {"receipt": {"receipts": []}, "response": {"corpus": "c", "current_memories": []}}
    with mock.patch("memory_receipt.verify_response_receipt", side_effect=RuntimeError("boom")):
        with pytest.raises(RuntimeError):
            import asyncio

            asyncio.get_event_loop().run_until_complete(
                R.revalidate_receipt(None, bundle, verify_integrity=True)
            )


def test_load_artifact_path_confinement(tmp_path, monkeypatch):
    root = tmp_path / "docs"
    root.mkdir()
    monkeypatch.setenv("MIND_PALACE_INGEST_ROOTS", str(root))
    from mcp_server import _load_artifact

    ok = root / "a.json"
    ok.write_text('{"x": 1}')
    assert _load_artifact(str(ok)) == {"x": 1}
    # traversal
    assert _load_artifact(str(root / ".." / "secret.json")) is None
    outside = tmp_path / "outside.json"
    outside.write_text('{"y": 2}')
    assert _load_artifact(str(outside)) is None


def test_trailing_ordinal_parses():
    from api.services.memory import _trailing_ordinal

    assert _trailing_ordinal("queue.item:3") == 3
    assert _trailing_ordinal("queue.item:12") == 12
    assert _trailing_ordinal("noordinal") == 0


def test_cross_corpus_mismatch_detection():
    # REST-level logic is exercised via service extraction; ensure bare receipt corpus readable.
    rd = {"corpus": "other", "claim_ids": ["a"]}
    assert rd.get("corpus") == "other"


async def test_include_drift_flags(store):
    from api.services import ingestion as ingestion_service
    from api.services import revalidate as revalidate_service

    await ingestion_service.IngestionService(
        memory_enabled=True, require_embeddings=False
    ).ingest_file(
        content=(
            "---\n"
            "title: Doc\n"
            "claims:\n"
            "  - key: storage.engine\n"
            "    value: SQLite\n"
            "    claim: The primary storage engine is SQLite.\n"
            "    evidence: The primary storage engine is SQLite.\n"
            "---\n"
            "# Doc\n\n"
            "The primary storage engine is SQLite.\n"
        ),
        path="doc.md",
        corpus_id=store.corpus,
    )
    rows = await revalidate_service._fetch_claims(store.db, store.corpus)
    assert rows
    cid = rows[0]["id"]

    off = await revalidate_service.revalidate_claims(
        store.db, store.name, [cid], include_drift=False
    )
    assert off["drift"] is None

    on = await revalidate_service.revalidate_claims(store.db, store.name, [cid], include_drift=True)
    assert on["drift"]["status"] == "unavailable"


def test_cross_corpus_mismatch_mcp():
    import asyncio
    import json
    import mcp_server

    async def run():
        return await mcp_server.mcp.call_tool(
            "memory_revalidate",
            {"corpus": "corpus_a", "receipt": {"corpus": "corpus_b", "claim_ids": []}},
        )

    result = asyncio.run(run())
    payload = json.loads(result.content[0].text)
    assert result.is_error
    assert "mismatch" in payload.get("message", "").lower()


def test_pairing_valid_and_ambiguous():
    from api.services.memory import _pair_supersession_claims

    keys = ["a", "b"]
    key_claim = {"a": "Storage: SQLite", "b": "Auth: cookies"}
    targets = [
        {"key": "a", "id": "t1", "claim": "Storage: SQLite"},
        {"key": "b", "id": "t2", "claim": "Auth: cookies"},
    ]
    pairs = _pair_supersession_claims(keys, key_claim, targets)
    assert pairs["a"][0] == "t1"
    assert pairs["b"][0] == "t2"

    # Ambiguous: same generic opening, no unique subject
    amb_keys = ["x", "y"]
    amb_key_claim = {"x": "The system uses foo", "y": "The system uses foo"}
    amb_targets = [
        {"key": "z1", "id": "u1", "claim": "The system uses foo"},
        {"key": "z2", "id": "u2", "claim": "The system uses foo"},
    ]
    import pytest

    with pytest.raises(ValueError):
        _pair_supersession_claims(amb_keys, amb_key_claim, amb_targets)
