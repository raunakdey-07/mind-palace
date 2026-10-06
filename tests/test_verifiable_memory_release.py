"""M015.1: the release demonstration, end to end, on a real corpus.

Every other test checks one property. This one walks the whole product story a
developer actually lives, and it is the workflow the README shows:

    query the archive            -> an authoritative answer, with a Memory Receipt
    export the artifact          -> files, not a database connection
    leave the runtime            -> a clean environment, no server, no model
    verify                       -> VERIFIED
    change one authoritative byte
    verify                       -> REJECTED, naming the invariant that failed
    restore                      -> VERIFIED again
    ask what it used to know     -> the earlier authoritative state
    verify that too              -> VERIFIED

The corpus is the repository's own `project_corpus`, not a fixture invented for
this test: `docs/storage.md` is written twice, and the second write supersedes
the first. MySQL becoming PostgreSQL is a real supersession chain, so the
historical step has something true to recover.

Retrieval is not what is under test, so the questions are chosen to retrieve
deterministically. The claim of interest is the one the supersession happened on.
"""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pytest
from sqlalchemy import text

from api.models.memory import MemoryResponse
from api.services.parser import parse_markdown
from memory_proof_cli import EXIT_OK, EXIT_REJECTED

try:
    from scripts.benchmark import project_corpus
    from tests import test_memory_core as core
    from tests import test_memory_public as public
except ImportError:  # pragma: no cover - import-shape fallback
    import test_memory_core as core
    import test_memory_public as public

    from scripts.benchmark import project_corpus

memory_db = core.memory_db
public_db = public.public_db

ROOT = Path(__file__).resolve().parents[1]
KEY = "architecture.database"
QUESTION = "What is the current database?"
BEFORE = "What database was used previously?"


async def _record(store, path, document_id, content):
    """Author one document version through the production write path.

    Parsing goes through the repository's own frontmatter reader, so these
    documents enter the archive exactly as `sync_repo` would write them --
    including the ones that carry several claims, and the untrusted import that
    carries none.
    """
    metadata, body = parse_markdown(content)
    claims = metadata.get("claims") or []
    return await public.memory.record_version(
        store.db,
        store.corpus,
        path,
        document_id,
        content,
        {"claims": list(claims)},
        [{"text": body.strip(), "heading_path": path, "order_index": 0}],
    )


@pytest.fixture
async def store(public_db):
    """One real document, written twice, exactly as `project_corpus` declares it.

    Only the storage pair is ingested. The other six corpus documents bring a
    live conflict and an untrusted import that are irrelevant here, and a
    supersession chain is what this test is about.
    """
    first = await _record(public_db, "docs/storage.md", "doc-storage", project_corpus.STORAGE_V1)
    await _record(public_db, "docs/storage.md", "doc-storage", project_corpus.STORAGE_V2)
    # The shared fixture builds only migration 004, so without the cache table
    # the query path degrades and leaves the transaction aborted.
    await public_db.db.execute(
        text(
            "CREATE TABLE IF NOT EXISTS memory_claim_embeddings ("
            " corpus_id CHAR(64) NOT NULL, claim_id CHAR(64) NOT NULL,"
            " representation_hash CHAR(64) NOT NULL, embedding_model TEXT NOT NULL,"
            " embedding_dimension INTEGER NOT NULL, embedding_version INTEGER NOT NULL,"
            " embedding public.vector NOT NULL, PRIMARY KEY (corpus_id, claim_id))"
        )
    )
    return public_db, first


async def _answer(store, question, **selectors):
    return await public.call(store, "query", query=question, include_receipt=True, **selectors)


def _verify(pack_path, receipt_path, cwd):
    return subprocess.run(
        [
            sys.executable,
            str(ROOT / "memory_proof_cli.py"),
            "verify",
            receipt_path.name,
            "--pack",
            pack_path.name,
        ],
        capture_output=True,
        text=True,
        cwd=str(cwd),
    )


async def test_the_release_workflow_from_query_to_offline_verification(store, tmp_path):
    published, first = store
    where = tmp_path / "export"
    where.mkdir()

    # --- 1. What the archive says now, and the receipt that says why ---------
    current = await _answer(published, QUESTION)
    assert [c.claim for c in current.current_memories] == [
        "The primary database is PostgreSQL."
    ], current.current_memories
    receipt = current.receipt
    assert receipt, "a query that answers must issue a receipt"
    entry = receipt["receipts"][0]
    assert entry["answer"]["claim_key"] == KEY
    assert entry["lineage"]["supersedes"], "the current claim replaced one"
    assert entry["source"]["path"] == "docs/storage.md"
    assert entry["evidence"]
    assert entry["integrity"]["memory_pack_digest"] == receipt["memory_pack_digest"]

    # --- 2. The artifact leaves as files, not as a database connection ------
    # A REST/SDK response carries a receipt BUNDLE, an envelope over one receipt
    # per answered key. The CLI's contract, and the one the README documents, is
    # the receipt itself -- the same object either way, so a developer holding
    # the bundle can still verify any receipt inside it.
    (where / "pack.json").write_text(
        MemoryResponse.model_validate(current.model_dump(mode="json")).canonical_json(),
        encoding="utf-8",
    )
    (where / "receipt.json").write_text(
        json.dumps(entry, indent=2, sort_keys=True), encoding="utf-8"
    )
    pristine = (where / "pack.json").read_text(encoding="utf-8")

    # --- 3. Someone else verifies it, with no Mind Palace runtime -----------
    verified = _verify(where / "pack.json", where / "receipt.json", where)
    assert verified.returncode == EXIT_OK, verified.stderr + verified.stdout
    assert "VERIFIED" in verified.stdout
    assert "authenticity" in verified.stdout.lower()

    # --- 4. One authoritative byte changes -----------------------------------
    (where / "pack.json").write_text(
        pristine.replace("The primary database is PostgreSQL.", "The primary database is MySQL."),
        encoding="utf-8",
    )
    rejected = _verify(where / "pack.json", where / "receipt.json", where)
    assert rejected.returncode == EXIT_REJECTED
    assert "REJECTED" in rejected.stdout
    # The reason is the invariant that actually failed, not a bare INVALID.
    assert "memory pack digest mismatch" in rejected.stdout
    assert "Traceback" not in rejected.stderr

    # --- 5. Restoring the artifact restores the verdict ----------------------
    (where / "pack.json").write_text(pristine, encoding="utf-8")
    reverified = _verify(where / "pack.json", where / "receipt.json", where)
    assert reverified.returncode == EXIT_OK, reverified.stderr + reverified.stdout

    # --- 6. What it used to know, and a receipt for that too -----------------
    historical = await _answer(published, BEFORE, as_of=first["observed_at"])
    old = historical.current_memories
    assert [c.claim for c in old] == ["The primary database is MySQL."], old
    old_entry = historical.receipt["receipts"][0]
    assert old_entry["answer"]["claim"] == "The primary database is MySQL."
    assert old_entry["state"]["as_of"], "a historical receipt says which instant it describes"
    assert old_entry["state"]["as_of"] != entry["state"]["as_of"]
    assert old_entry["answer"]["claim_id"] in entry["lineage"]["supersedes"]

    (where / "before.json").write_text(
        MemoryResponse.model_validate(historical.model_dump(mode="json")).canonical_json(),
        encoding="utf-8",
    )
    (where / "before-receipt.json").write_text(
        json.dumps(old_entry, indent=2, sort_keys=True), encoding="utf-8"
    )
    past = _verify(where / "before.json", where / "before-receipt.json", where)
    assert past.returncode == EXIT_OK, past.stderr + past.stdout
    assert "MySQL" in past.stdout

    # The two receipts describe different authoritative states of the same key,
    # and each is only valid against the artifact it was cut from.
    assert entry["integrity"]["memory_pack_digest"] != old_entry["integrity"]["memory_pack_digest"]
    crossed = _verify(where / "before.json", where / "receipt.json", where)
    assert crossed.returncode == EXIT_REJECTED
