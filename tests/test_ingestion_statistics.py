"""The ingest hook that refreshes planner statistics, and that has to be provable.

Without statistics PostgreSQL guesses a row count, guesses it badly, and then
picks ``idx_memory_claims_validity`` for the production ``memory._load`` query:
one index lookup per version that returns every claim in the corpus and throws
999 of 1000 away. ``IngestionService._refresh_planner_statistics`` runs an
ANALYZE once per bulk sync so the planner is not guessing.

Six properties matter here and none of them may be assumed:

    structural  after a bulk sync the five memory tables carry real row counts
    plan shape  ``_load`` puts version_id in the Index Cond, not in a Filter
    bounded     the whole-corpus load reads a bounded number of shared buffers
    authority   the rows returned are byte-identical either way
    non-fatal   a failing ANALYZE does not fail an otherwise successful ingest
    cheap       a sync below the row threshold never runs an ANALYZE at all

Every measurement here is catalog state, plan shape, a shared-buffer count or a
row hash. There is no wall-clock threshold anywhere in this file: timings do not
reproduce across machines, and the property that actually regressed was which
index the planner chose, not how long the query took.

Isolation: a throwaway schema created inside one transaction that is ROLLED BACK
at the end. Nothing in ``public`` is read for statistics or written, and the
statistics cannot leak out -- statistics are keyed by relation OID, and every run
creates a new schema whose tables get new OIDs.

One reading note, because it silently produces wrong evidence: the ``pg_stat_*``
views cache their snapshot for the lifetime of a transaction, so a reader that
looked at ``last_analyze`` before the ANALYZE keeps returning the stale NULL
afterwards. Every reader in this file calls ``pg_stat_clear_snapshot()`` first.
"""

from __future__ import annotations

import hashlib
import importlib.util
import json
import logging
import os
import re
import shutil
import tempfile
from contextlib import asynccontextmanager
from pathlib import Path
from uuid import uuid4

import pytest
import pytest_asyncio
import sqlalchemy as sa
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine
from sqlalchemy.pool import NullPool

from api.services import ingestion
from api.services.ingestion import STATISTICS_REFRESH_MINIMUM, IngestionService

#: Claims, evidence rows, chunks, versions and documents: one of each per file.
BULK_CLAIMS = 1000

#: Ceiling on shared buffers for the whole-corpus narrow ``_load`` at
#: BULK_CLAIMS. Measured on this schema: ~9,190 with statistics and ~1,079,038
#: without, a 117x difference that comes from the plan choice alone. 50,000 sits
#: ~20x below the failing side, so a regression cannot hide inside it, and ~5x
#: above the passing side, so a different block size or vacuum state cannot fail
#: it. A shared-buffer count is how many distinct pages the plan touched -- a
#: deterministic property of the plan, not a duration.
SHARED_BUFFER_CEILING = 50_000

TABLES = (
    "memory_versions",
    "memory_documents",
    "memory_chunks",
    "memory_claims",
    "memory_evidence",
)

_HOOK_EVENTS = {"PLANNER_STATISTICS_REFRESHED", "PLANNER_STATISTICS_REFRESH_FAILED"}


class FixedEmbedder:
    """Deterministic vectors: the shape matters, the values do not."""

    model_name = "test-only"
    dimension = 384

    def embed(self, texts):
        return [[1.0] + [0.0] * 383 for _ in texts]


def source(i: int) -> str:
    """One authored claim per document, so the corpus really is BULK_CLAIMS claims."""
    body = f"Component {i:05d} stores state in PostgreSQL."
    return (
        "---\n"
        f'title: "Component {i:05d}"\n'
        "document_type: design\n"
        "claims:\n"
        f"  - key: architecture.component{i:05d}\n"
        f'    value: "{body}"\n'
        f'    claim: "{body}"\n'
        f'    evidence: "{body}"\n'
        "---\n"
        f"# Component {i:05d}\n\n"
        f"{body}\n"
    )


def _db_available() -> bool:
    url = os.getenv("DATABASE_URL", "")
    if not url:
        return False
    try:
        engine = sa.create_engine(url.replace("postgresql+psycopg://", "postgresql+psycopg2://"))
        with engine.connect():
            engine.dispose()
        return True
    except Exception:
        return False


requires_db = pytest.mark.skipif(not _db_available(), reason="no reachable DATABASE_URL")


def _migrate(sync_conn):
    """Apply the real migrations, so the corpus can only exist via production paths."""
    for path in sorted((Path(__file__).parents[1] / "migrations/versions").glob("*.py")):
        spec = importlib.util.spec_from_file_location(path.stem, path)
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        from alembic.migration import MigrationContext
        from alembic.operations import Operations

        with Operations.context(MigrationContext.configure(sync_conn)):
            module.upgrade()


class _SQLCapture:
    """A session stand-in that records the statement ``memory._load`` emits."""

    def __init__(self):
        self.sql = None

    async def execute(self, statement, params=None):
        self.sql = str(statement)
        return _Empty()


class _Empty:
    def mappings(self):
        return []


class _LogCapture(logging.Handler):
    """Records the named events the hook emits, ignoring per-document noise."""

    def __init__(self):
        super().__init__(level=logging.INFO)
        self.events = []

    def emit(self, record):
        event = getattr(record, "event", None)
        if event in _HOOK_EVENTS:
            self.events.append((event, dict(record.__dict__)))


async def _capture_load_sql() -> str:
    """The literal statement the public projection runs, not a retype of it."""
    from api.services import memory

    capture = _SQLCapture()
    await memory._load(capture, "0" * 64, chunk_text=False, version_text=False)
    assert "memory_claims" in capture.sql
    return capture.sql


async def _catalog(db) -> dict:
    """reltuples / last_analyze / live row count for the five memory tables."""
    await db.execute(text("SELECT pg_stat_clear_snapshot()"))
    rows = (
        await db.execute(
            text(
                "SELECT c.relname, c.reltuples, s.last_analyze, s.analyze_count"
                " FROM pg_class c JOIN pg_stat_all_tables s ON s.relid = c.oid"
                " WHERE c.relnamespace = current_schema()::regnamespace"
                " AND c.relname = ANY(:names) ORDER BY c.relname"
            ),
            {"names": list(TABLES)},
        )
    ).fetchall()
    assert len(rows) == len(TABLES), "the throwaway schema must hold all five memory tables"
    counted = {}
    for table in TABLES:
        counted[table] = (await db.execute(text(f"SELECT count(*) FROM {table}"))).scalar()
    return {
        r[0]: {
            "reltuples": r[1],
            "last_analyze": None if r[2] is None else str(r[2]),
            "analyze_count": r[3],
            "rows": counted[r[0]],
        }
        for r in rows
    }


def _walk(node):
    yield node
    for child in node.get("Plans") or []:
        yield from _walk(child)


def _scan_info(plan, relation: str) -> dict:
    """The access path the planner chose for one relation, index scan preferred."""
    fallback = None
    for node in _walk(plan):
        if node.get("Relation Name") != relation:
            continue
        info = {
            "node": node.get("Node Type"),
            "index": node.get("Index Name"),
            "index_cond": node.get("Index Cond"),
            "filter": node.get("Filter"),
            "rows_removed_by_filter": node.get("Rows Removed by Filter"),
            "plan_rows": node.get("Plan Rows"),
            "actual_rows": node.get("Actual Rows"),
            "loops": node.get("Actual Loops"),
        }
        if info["index"]:
            return info
        fallback = fallback or info
    return fallback or {}


def _canonical(value) -> str:
    import datetime as dt
    import decimal as dec

    if value is None:
        return "\x00NULL"
    if isinstance(value, str):
        return value
    if isinstance(value, (dt.datetime, dt.date, dt.time)):
        return value.isoformat()
    if isinstance(value, dec.Decimal):
        return str(value)
    if isinstance(value, (bytes, bytearray, memoryview)):
        return bytes(value).hex()
    return repr(value)


async def _explain(db, sql: str, params: dict) -> dict:
    """EXPLAIN (ANALYZE, BUFFERS, FORMAT JSON). Execution Time is present and unused."""
    doc = (
        await db.execute(text("EXPLAIN (ANALYZE, BUFFERS, FORMAT JSON) " + sql), params)
    ).scalar()
    if isinstance(doc, str):
        doc = json.loads(doc)
    root = doc[0]["Plan"] if isinstance(doc, list) else doc["Plan"]
    return {
        "shared_hit": root.get("Shared Hit Blocks"),
        "shared_read": root.get("Shared Read Blocks"),
        "claims": _scan_info(root, "memory_claims"),
        "evidence": _scan_info(root, "memory_evidence"),
        "chunks": _scan_info(root, "memory_chunks"),
        "discarding_filters": [
            {"filter": n.get("Filter"), "removed": n.get("Rows Removed by Filter")}
            for n in _walk(root)
            if n.get("Filter") and n.get("Rows Removed by Filter")
        ],
    }


async def _rows(db, sql: str, params: dict) -> dict:
    """SHA-256 over every column of every row, so 'same answer' means byte-identical."""
    result = await db.execute(text(sql), params)
    columns = list(result.keys())
    rows = result.fetchall()
    digest = hashlib.sha256()
    for row in rows:
        for name, value in zip(columns, row):
            digest.update(repr((name, _canonical(value))).encode())
            digest.update(b"\x1f")
        digest.update(b"\x1e")
    return {"n_rows": len(rows), "columns": columns, "sha256": digest.hexdigest()}


async def _measure(db, sql: str, params: dict) -> dict:
    return {"plan": await _explain(db, sql, params), "rows": await _rows(db, sql, params)}


def _sessions(conn):
    """Sessions on the test's own connection, so the throwaway search_path holds."""

    @asynccontextmanager
    async def sessions():
        async with AsyncSession(
            bind=conn, join_transaction_mode="create_savepoint", expire_on_commit=False
        ) as db:
            yield db

    return sessions


async def _provision(schema: str, corpus: str, conn) -> None:
    await conn.execute(text(f'CREATE SCHEMA "{schema}"'))
    await conn.execute(text(f'SET LOCAL search_path TO "{schema}", public'))
    await conn.run_sync(_migrate)
    await conn.execute(
        text("INSERT INTO corpora(id, name) VALUES (:id, :name)"), {"id": corpus, "name": schema}
    )
    # Autovacuum must not be able to produce the result the hook is judged on.
    for table in TABLES:
        await conn.execute(text(f"ALTER TABLE {table} SET (autovacuum_enabled = false)"))


def _write_repo(repo: Path, count: int) -> Path:
    repo.mkdir(parents=True, exist_ok=True)
    for i in range(count):
        (repo / f"component-{i:05d}.md").write_text(source(i))
    return repo


# --------------------------------------------------------------------------- #
# The bulk corpus: built once, measured on both sides of the hook.
# --------------------------------------------------------------------------- #


async def _build_bulk_corpus() -> dict:
    url = os.environ["DATABASE_URL"].replace("postgresql://", "postgresql+asyncpg://")
    engine = create_async_engine(url, poolclass=NullPool)
    schema = "ingestion_stats_" + uuid4().hex[:12]
    corpus = hashlib.sha256(uuid4().bytes).hexdigest()
    repo = _write_repo(Path(tempfile.mkdtemp(prefix="mp-stats-bulk-")), BULK_CLAIMS)

    # The production hook, captured before anything is installed over it.
    hook = IngestionService._refresh_planner_statistics
    saved_scope, saved_embedder = ingestion.session_scope, ingestion.Embedder
    logger = logging.getLogger("api.services.ingestion")
    saved_level = logger.level
    # The hook reports at INFO and the root logger is at WARNING, so without this
    # the event is filtered out before it ever reaches the handler.
    logger.setLevel(logging.INFO)
    log_capture = _LogCapture()
    logger.addHandler(log_capture)
    seen: dict = {}
    try:
        async with engine.connect() as conn:
            outer = await conn.begin()
            try:
                await _provision(schema, corpus, conn)
                sessions = _sessions(conn)
                ingestion.session_scope = sessions
                ingestion.Embedder = FixedEmbedder
                sql = await _capture_load_sql()
                params = {"c": corpus, "path": None, "at": None}
                # Identical on both sides, so statistics are the only variable.
                await conn.execute(text("SET LOCAL plan_cache_mode = force_custom_plan"))

                async def spy(self, corpus_id, touched):
                    """Record what the planner believes, then let the hook run."""
                    async with sessions() as db:
                        seen["before"] = await _measure(db, sql, params)
                        seen["touched"] = touched
                    await hook(self, corpus_id, touched)
                    async with sessions() as db:
                        seen["after"] = await _measure(db, sql, params)

                IngestionService._refresh_planner_statistics = spy
                try:
                    service = IngestionService(memory_enabled=True)
                    async with sessions() as db:
                        before_catalog = await _catalog(db)
                    result = await service.sync_repo(str(repo), corpus)
                    async with sessions() as db:
                        after_catalog = await _catalog(db)
                finally:
                    IngestionService._refresh_planner_statistics = hook
            finally:
                await outer.rollback()
    finally:
        IngestionService._refresh_planner_statistics = hook
        ingestion.session_scope, ingestion.Embedder = saved_scope, saved_embedder
        logger.removeHandler(log_capture)
        logger.setLevel(saved_level)
        shutil.rmtree(repo, ignore_errors=True)
        await engine.dispose()

    return {
        "schema": schema,
        "corpus": corpus,
        "sync": {k: result[k] for k in ("success", "added", "changed", "deleted", "failed")},
        "events": [e for e, _ in log_capture.events],
        "touched": seen.get("touched"),
        "catalog_before": before_catalog,
        "catalog_after": after_catalog,
        "before": seen.get("before"),
        "after": seen.get("after"),
    }


@pytest_asyncio.fixture(scope="module", loop_scope="module")
async def bulk_sync():
    if not _db_available():
        pytest.skip("no reachable DATABASE_URL")
    return await _build_bulk_corpus()


# --------------------------------------------------------------------------- #
# T1 structural, T2 plan shape, T3 shared buffers, T4 authority
# --------------------------------------------------------------------------- #


@requires_db
def test_bulk_sync_leaves_the_five_memory_tables_analysed(bulk_sync):
    """reltuples must be the row count, not -1, and the ANALYZE must be recorded."""
    before, after = bulk_sync["catalog_before"], bulk_sync["catalog_after"]
    for table in TABLES:
        assert before[table]["reltuples"] == -1, f"{table} was analysed before the sync"
        assert before[table]["last_analyze"] is None, f"{table} had last_analyze before the sync"
    assert bulk_sync["sync"]["success"] is True
    assert bulk_sync["sync"]["added"] == BULK_CLAIMS
    assert bulk_sync["touched"] == BULK_CLAIMS
    assert bulk_sync["events"] == ["PLANNER_STATISTICS_REFRESHED"], bulk_sync["events"]

    for table in TABLES:
        assert after[table]["rows"] == BULK_CLAIMS, f"{table} does not hold the corpus"
        assert (
            int(after[table]["reltuples"]) == BULK_CLAIMS
        ), f"{table} reltuples={after[table]['reltuples']}, expected {BULK_CLAIMS}"
        assert after[table]["last_analyze"] is not None, f"{table} was never analysed"
        # Once per sync, not once per document.
        assert after[table]["analyze_count"] == 1


@requires_db
def test_load_query_makes_version_id_an_index_condition_not_a_filter(bulk_sync):
    """The whole point: (corpus_id, version_id) has to reach the Index Cond."""
    plan = bulk_sync["after"]["plan"]
    for relation in ("claims", "evidence"):
        info = plan[relation]
        assert info.get("index_cond"), f"memory_{relation} was not reached by an index scan: {info}"
        assert "version_id" in info["index_cond"], (
            f"memory_{relation} scans {info['index']} on {info['index_cond']},"
            f" so version_id is still being filtered away"
        )
        assert not info.get("filter"), f"memory_{relation} still filters above the index: {info}"
        assert not info.get("rows_removed_by_filter"), info

    # The exact shape the hook exists to remove: every version's scan reads the
    # whole corpus and discards the other BULK_CLAIMS - 1 rows.
    assert not [
        f
        for f in plan["discarding_filters"]
        if re.search(r"version_id\s*=\s*v\.id", f["filter"]) and f["removed"] == BULK_CLAIMS - 1
    ], plan["discarding_filters"]


@requires_db
def test_load_reads_a_bounded_number_of_shared_buffers(bulk_sync):
    """A buffer ceiling, not a stopwatch. See SHARED_BUFFER_CEILING for the rationale."""
    before, after = bulk_sync["before"]["plan"], bulk_sync["after"]["plan"]
    assert after["shared_hit"] < SHARED_BUFFER_CEILING, (
        f"narrow _load read {after['shared_hit']} shared buffers at"
        f" BULK_CLAIMS={BULK_CLAIMS}; the ceiling is {SHARED_BUFFER_CEILING}"
    )
    # The contrast the ceiling is calibrated against, so the number stays honest.
    assert before["shared_hit"] > SHARED_BUFFER_CEILING, (
        f"without statistics the same query read only {before['shared_hit']} buffers;"
        f" this test no longer measures the regression it was written for"
    )


@requires_db
def test_statistics_do_not_change_a_single_returned_row(bulk_sync):
    """ANALYZE changes the plan and the cost. It must not change the answer."""
    before, after = bulk_sync["before"]["rows"], bulk_sync["after"]["rows"]
    assert before["n_rows"] == after["n_rows"] == BULK_CLAIMS
    assert before["columns"] == after["columns"]
    assert before["sha256"] == after["sha256"]


# --------------------------------------------------------------------------- #
# T5 non-fatal, T6 bounded -- cheap, each on its own throwaway schema.
# --------------------------------------------------------------------------- #


@asynccontextmanager
async def _throwaway_corpus(monkeypatch, root: Path, n_docs: int):
    """A migrated throwaway schema, a repo on disk, and the service bound to both."""
    url = os.environ["DATABASE_URL"].replace("postgresql://", "postgresql+asyncpg://")
    engine = create_async_engine(url, poolclass=NullPool)
    schema = "ingestion_stats_" + uuid4().hex[:12]
    corpus = hashlib.sha256(uuid4().bytes).hexdigest()
    repo = _write_repo(root / "repo", n_docs)
    async with engine.connect() as conn:
        outer = await conn.begin()
        try:
            await _provision(schema, corpus, conn)
            monkeypatch.setattr(ingestion, "session_scope", _sessions(conn))
            monkeypatch.setattr(ingestion, "Embedder", FixedEmbedder)
            yield _sessions(conn), corpus, IngestionService(memory_enabled=True), repo
        finally:
            await outer.rollback()
    await engine.dispose()


@pytest.fixture
async def tiny_corpus(monkeypatch, tmp_path):
    if not _db_available():
        pytest.skip("no reachable DATABASE_URL")
    async with _throwaway_corpus(monkeypatch, tmp_path / "tiny", 5) as ctx:
        yield ctx


@pytest.fixture
async def threshold_corpus(monkeypatch, tmp_path):
    if not _db_available():
        pytest.skip("no reachable DATABASE_URL")
    async with _throwaway_corpus(
        monkeypatch, tmp_path / "threshold", STATISTICS_REFRESH_MINIMUM + 1
    ) as ctx:
        yield ctx


@requires_db
async def test_a_failing_analyze_does_not_fail_the_ingest(threshold_corpus, monkeypatch):
    """Statistics are an optimisation; losing them must cost speed, not truth.

    The fault is injected at the statement the hook builds, so the production
    try/except is what is under test rather than a replacement for it.
    """
    sessions, corpus, service, repo = threshold_corpus
    real_text = ingestion.text

    def exploding_text(statement, *args, **kwargs):
        if str(statement).lstrip().upper().startswith("ANALYZE"):
            raise RuntimeError("injected ANALYZE failure")
        return real_text(statement, *args, **kwargs)

    monkeypatch.setattr(ingestion, "text", exploding_text)
    logger = logging.getLogger("api.services.ingestion")
    monkeypatch.setattr(logger, "level", logging.INFO)
    log_capture = _LogCapture()
    logger.addHandler(log_capture)
    try:
        result = await service.sync_repo(str(repo), corpus)
        async with sessions() as db:
            catalog = await _catalog(db)
    finally:
        logger.removeHandler(log_capture)

    assert result["success"] is True, result
    assert result["failed"] == 0, result
    assert result["added"] == STATISTICS_REFRESH_MINIMUM + 1, result
    assert [e for e, _ in log_capture.events] == ["PLANNER_STATISTICS_REFRESH_FAILED"]
    failure = log_capture.events[0][1]
    assert failure["error_type"] == "RuntimeError"
    assert failure["touched"] == STATISTICS_REFRESH_MINIMUM + 1
    for table in TABLES:
        assert catalog[table]["reltuples"] == -1, f"{table} was analysed despite the failure"
        assert catalog[table]["last_analyze"] is None


@requires_db
async def test_a_sync_below_the_threshold_never_runs_analyze(tiny_corpus):
    """One ANALYZE per sync, and not one at all until a sync has written enough."""
    sessions, corpus, service, repo = tiny_corpus
    result = await service.sync_repo(str(repo), corpus)

    assert result["success"] is True
    assert result["added"] + result["changed"] + result["deleted"] <= STATISTICS_REFRESH_MINIMUM
    async with sessions() as db:
        catalog = await _catalog(db)
        for table in TABLES:
            assert catalog[table]["reltuples"] == -1, f"{table} was analysed for a tiny sync"
            assert catalog[table]["last_analyze"] is None

        # The bound is inclusive at the threshold and moves one row above it.
        await service._refresh_planner_statistics(corpus, STATISTICS_REFRESH_MINIMUM)
        catalog = await _catalog(db)
        assert all(catalog[t]["last_analyze"] is None for t in TABLES), catalog

        await service._refresh_planner_statistics(corpus, STATISTICS_REFRESH_MINIMUM + 1)
        catalog = await _catalog(db)
        for table in TABLES:
            assert catalog[table]["last_analyze"] is not None, f"{table} skipped at +1 row"
            assert int(catalog[table]["reltuples"]) == 5, catalog[table]
