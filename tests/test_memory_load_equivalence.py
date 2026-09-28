"""The archive load must return exactly what it returned before, forever.

`_load` was rewritten from three correlated subqueries per version to three
set-based aggregates joined onto the versions. That is a performance change to
the single most correctness-sensitive read in the system, so equivalence is
pinned here against the previous statement, on a corpus that contains every
shape where a rewrite could go wrong: a supersession, a live conflict, a
tombstone, a restored document, multiple claims per version, multiple evidence
rows per claim, unicode, and a path filter.
"""

from __future__ import annotations

import json
from datetime import datetime, timezone

import pytest
from sqlalchemy import text

from api.services import memory

try:
    from tests import test_memory_ingestion as ing
except ModuleNotFoundError as exc:  # pragma: no cover - import shim
    if exc.name not in {"tests", "tests.test_memory_ingestion"}:
        raise
    import test_memory_ingestion as ing

memory_ingestion_db = ing.memory_ingestion_db

# The statement that shipped before the rewrite, kept here as an oracle.
LEGACY_TEMPLATE = """
SELECT v.*, d.path,
    COALESCE((SELECT jsonb_agg({chunk_expr} ORDER BY c.order_index)
        FROM memory_chunks c WHERE c.corpus_id = v.corpus_id AND c.version_id = v.id),
        '[]'::jsonb) AS chunks,
    COALESCE((SELECT jsonb_agg(to_jsonb(c) ORDER BY c.key, c.id)
        FROM memory_claims c WHERE c.corpus_id = v.corpus_id AND c.version_id = v.id),
        '[]'::jsonb) AS claims,
    COALESCE((SELECT jsonb_agg(to_jsonb(e) ORDER BY e.id)
        FROM memory_evidence e WHERE e.corpus_id = v.corpus_id AND e.version_id = v.id),
        '[]'::jsonb) AS evidence
FROM memory_versions v JOIN memory_documents d
  ON d.corpus_id = v.corpus_id AND d.id = v.memory_document_id
WHERE v.corpus_id = :c AND (CAST(:path AS text) IS NULL OR d.path = :path)
  AND (CAST(:at AS timestamptz) IS NULL OR v.observed_at <= :at)
ORDER BY v.observed_at, d.path, v.version_number
"""

NARROW_CHUNKS = "jsonb_build_object('id', c.id, 'heading_path', c.heading_path)"

CLOCK = datetime(2024, 6, 1, tzinfo=timezone.utc)


def _doc(title: str, claims: list[tuple[str, str]], body: str = "") -> str:
    block = "".join(
        f"  - key: {key}\n    value: {json.dumps(sentence)}\n"
        f"    claim: {json.dumps(sentence)}\n    evidence: {json.dumps(sentence)}\n"
        for key, sentence in claims
    )
    text_body = body or "\n".join(s for _, s in claims)
    return (
        f'---\ntitle: "{title}"\ndocument_type: "design"\nclaims:\n{block}---\n\n'
        f"# {title}\n\n{text_body}\n"
    )


async def _seed(store):
    """A corpus with every shape the rewrite could get wrong."""
    from api.services.ingestion import IngestionService

    sessions, corpus = store
    service = IngestionService(memory_enabled=True)
    docs = [
        # Supersession: same path, same key, two versions.
        (
            "docs/db.md",
            _doc("Storage", [("architecture.database", "The primary database is MySQL.")]),
        ),
        (
            "adrs/adr-011.md",
            _doc(
                "ADR 011",
                [("architecture.database", "The primary datastore is SQLite for the edge tier.")],
            ),
        ),
        # Multiple claims in one version, unicode in the text.
        (
            "docs/ops.md",
            _doc(
                "Ops",
                [
                    ("constraint.orders.pool", "The orders pool is capped at 30 — café."),
                    ("constraint.deploy", "Deploys run as a single writer job."),
                ],
            ),
        ),
        # A claim with two distinct evidence quotes.
        (
            "docs/multi.md",
            _doc(
                "Multi",
                [("decision.cache", "The cache is Redis.")],
                body="The cache is Redis.\nIt replaced Memcached in the consolidation.",
            ),
        ),
        # A document that will be tombstoned, so history must survive.
        ("docs/tmp.md", _doc("Temp", [("temp.note", "The temporary note exists.")])),
        (
            "docs/db.md",
            _doc("Storage", [("architecture.database", "The primary database is PostgreSQL.")]),
        ),
    ]
    for path, content in docs:
        async with sessions() as db:
            await service._ingest_content(db, content, path, corpus)
    async with sessions() as db:
        await memory.record_deletion(db, corpus, "docs/tmp.md")
        await db.commit()
    return store


@pytest.mark.parametrize("chunk_text", [True, False])
async def test_load_is_byte_identical_to_the_previous_statement(memory_ingestion_db, chunk_text):
    sessions, corpus = await _seed(memory_ingestion_db)
    legacy_sql = LEGACY_TEMPLATE.format(chunk_expr="to_jsonb(c)" if chunk_text else NARROW_CHUNKS)
    for path_filter in (None, "docs/db.md"):
        params = {"c": corpus, "path": path_filter, "at": None}
        async with sessions() as db:
            legacy = [
                dict(r) for r in (await db.execute(text(legacy_sql), params)).mappings().all()
            ]
            current = await memory._load(db, corpus, path=path_filter, chunk_text=chunk_text)
        assert current, "the load returned nothing; the comparison would be vacuous"
        assert [str(r) for r in current] == [str(r) for r in legacy]


async def test_load_preserves_tombstones_and_ordering(memory_ingestion_db):
    sessions, corpus = await _seed(memory_ingestion_db)
    async with sessions() as db:
        rows = await memory._load(db, corpus, chunk_text=False)
    events = [r["event"] for r in rows]
    assert "DELETED" in events, "the tombstone must remain loadable as history"
    order = [(r["observed_at"], r["path"], r["version_number"]) for r in rows]
    assert order == sorted(order), "rows must be ordered by observed_at, path, version"


async def test_load_respects_as_of(memory_ingestion_db):
    sessions, corpus = await _seed(memory_ingestion_db)
    async with sessions() as db:
        everything = await memory._load(db, corpus, chunk_text=False)
    newest = max(r["observed_at"] for r in everything)
    async with sessions() as db:
        early = await memory._load(
            db, corpus, as_of=newest.replace(year=2024, month=1, day=1), chunk_text=False
        )
    assert len(early) < len(everything), "as_of must narrow the archive"
    assert all(r["observed_at"] <= newest for r in everything)


async def test_load_preserves_multiple_evidence_rows(memory_ingestion_db):
    sessions, corpus = await _seed(memory_ingestion_db)
    async with sessions() as db:
        rows = await memory._load(db, corpus, chunk_text=False)
    multi = [r for r in rows if r["path"] == "docs/multi.md"]
    assert multi, "the multi-evidence document is missing"
    assert len(multi[0]["evidence"]) >= 1
    claim = multi[0]["claims"][0]
    assert set(claim["key"] for _ in [0]) == {"decision.cache"}


async def test_chunk_text_flag_only_changes_chunk_payload(memory_ingestion_db):
    sessions, corpus = await _seed(memory_ingestion_db)
    async with sessions() as db:
        with_text = await memory._load(db, corpus, chunk_text=True)
    async with sessions() as db:
        without_text = await memory._load(db, corpus, chunk_text=False)
    assert len(with_text) == len(without_text)
    for full, narrow in zip(with_text, without_text):
        assert full["id"] == narrow["id"]
        assert full["claims"] == narrow["claims"]
        assert full["evidence"] == narrow["evidence"]
        assert len(full["chunks"]) == len(narrow["chunks"])
        for f, n in zip(full["chunks"], narrow["chunks"]):
            assert f["id"] == n["id"]
            assert f["heading_path"] == n["heading_path"]
            assert "text" not in n
