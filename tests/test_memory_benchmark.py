"""Offline unit tests and gated real PostgreSQL benchmark round trips."""

import copy
import json
import os
from pathlib import Path

import pytest
import yaml
from sqlalchemy import text
from sqlalchemy.engine import make_url
from sqlalchemy.ext.asyncio import create_async_engine
from sqlalchemy.pool import NullPool

from api.models.memory import MemoryRequest, MemoryResponse
from api.services.corpora import corpus_id_for_name
from api.services.memory_benchmark import (
    FixtureEmbedder,
    _performance,
    budget_baseline,
    format_report,
    latency_summary,
    load_memory_benchmark,
    load_spec,
    memory_benchmark_workload,
    run_memory_benchmark,
    score_expectations,
    text_availability,
)
from api.services.memory_public import MemoryError


def source(value):
    claim = f"The database is {value}."
    meta = {
        "title": "Database",
        "claims": [{"key": "database", "value": value, "claim": claim, "evidence": claim}],
    }
    return "---\n" + yaml.safe_dump(meta) + "---\n# Database\n\n" + claim + "\n"


@pytest.fixture
def workload_file(tmp_path):
    stages = []
    for alias in "ABCD":
        directory = tmp_path / alias
        directory.mkdir()
        stages.append({"id": alias, "directory": alias, "delete": []})
    (tmp_path / "A" / "db.md").write_text(source("Redis"))
    (tmp_path / "B" / "db.md").write_text(source("PostgreSQL"))
    (tmp_path / "B" / "other.md").write_text(source("SQLite"))
    stages[2]["delete"] = ["db.md", "other.md"]
    (tmp_path / "D" / "db.md").write_text(source("PostgreSQL"))

    def query(id, stage, operation, **labels):
        return {
            "id": id,
            "stage": stage,
            "operation": operation,
            "question": "Which database?",
            "query": "database",
            "query_type": {
                "initial": "current",
                "conflict": "conflict",
                "past": "temporal",
                "proof": "provenance",
                "deletion": "deletion",
                "events": "historical",
                "replay": "snapshot",
                "restored": "current",
                "budget": "current",
                "deliberate_failure": "current",
            }[id],
            **labels,
        }

    queries = [
        query(
            "initial",
            "A",
            "current",
            expected_current_claims=["The database is Redis."],
            expected_historical_claims=[],
            expected_sources=["db.md"],
            expected_evidence=["The database is Redis."],
            expected_conflicts=[],
        ),
        query(
            "conflict",
            "B",
            "history",
            expected_current_claims=[],
            expected_historical_claims=["The database is Redis."],
            expected_conflicts=[["The database is PostgreSQL.", "The database is SQLite."]],
        ),
        query(
            "past",
            "B",
            "as-of",
            as_of="A",
            expected_current_claims=["The database is Redis."],
            expected_state={"as_of": "A", "valid_at": "A", "snapshot": None},
        ),
        query(
            "proof",
            "B",
            "evidence",
            claim="The database is Redis.",
            expected_evidence=["The database is Redis."],
            expected_sources=["db.md"],
        ),
        query(
            "deletion",
            "C",
            "current",
            expected_current_claims=[],
            expected_conflicts=[],
            expected_evidence=[],
            expected_sources=[],
        ),
        query(
            "events", "C", "changes", path="db.md", expected_events=["NEW", "MODIFIED", "DELETED"]
        ),
        query(
            "replay",
            "D",
            "replay",
            snapshot="A",
            expected_current_claims=["The database is Redis."],
            expected_state={"as_of": "A", "valid_at": "A", "snapshot": "A"},
        ),
        query("restored", "D", "current", expected_current_claims=["The database is PostgreSQL."]),
        query("budget", "D", "pack", budget=1000, expected_current_claims=[]),
        query(
            "deliberate_failure", "D", "current", expected_current_claims=["Not the real database."]
        ),
    ]
    path = tmp_path / "bench.yaml"
    path.write_text(yaml.safe_dump({"version": 1, "stages": stages, "queries": queries}))
    return path


def test_loader_preserves_omitted_and_empty_labels(workload_file):
    loaded = load_memory_benchmark(workload_file)
    assert loaded["stages"][0]["directory"] == str(workload_file.parent / "A")
    assert loaded["queries"][0]["expected_conflicts"] == []
    assert "expected_conflicts" not in loaded["queries"][2]


@pytest.mark.parametrize(
    "mutate",
    [
        lambda d: d.update(version=True),
        lambda d: d.update(unknown=True),
        lambda d: d["queries"][0].update(unknown=True),
        lambda d: d["queries"][0].update(query_type="unknown"),
        lambda d: d["queries"][0].update(stage="Z"),
        lambda d: d["stages"][0].update(id="Z"),
        lambda d: d["queries"].append(copy.deepcopy(d["queries"][0])),
        lambda d: d["queries"][0].update(as_of="D"),
        lambda d: d["queries"][0].update(expected_evidence=None),
        lambda d: d["queries"][0].update(expected_conflicts=["wrong"]),
        lambda d: d["queries"][0].update(expected_state={"invented": True}),
        lambda d: d["queries"][0].update(operation="unknown"),
        lambda d: d["queries"][0].update(budget=True),
        lambda d: d["stages"][0].update(delete=["../unsafe.md"]),
        lambda d: d["queries"][0].update(expected_supersession=[{"previous": "x"}]),
    ],
)
def test_loader_rejects_invalid_spec(workload_file, mutate):
    data = yaml.safe_load(workload_file.read_text())
    mutate(data)
    workload_file.write_text(yaml.safe_dump(data))
    with pytest.raises(ValueError):
        load_memory_benchmark(workload_file)


def test_fixture_vectors_are_local_deterministic_and_explicit():
    embedder = FixtureEmbedder()
    assert "fixture" in embedder.model_name
    a = embedder.embed_single("database Redis")
    assert len(a) == 384
    assert a == FixtureEmbedder().embed_single("database Redis")
    assert a != embedder.embed_single("database PostgreSQL")
    assert sum(v * v for v in a) == pytest.approx(1)


def test_scores_do_not_treat_absent_labels_or_baseline_as_semantic_wins():
    response = MemoryResponse(query="", corpus="test")
    normalized = response.model_dump(mode="json")
    assert score_expectations({}, response, normalized, {}) == {}
    scores = score_expectations(
        {"expected_current_claims": [], "expected_evidence": ["missing"]}, response, normalized, {}
    )
    assert scores["expected_current_claims"]["passed"]
    assert scores["expected_current_claims"]["recall"] is None
    assert not scores["expected_evidence"]["passed"]
    baseline = text_availability(
        {"expected_current_claims": [], "expected_historical_claims": ["old"]}, ["old"], []
    )
    assert baseline["expected_current_claims"]["recall"] is None
    assert "passed" not in baseline["expected_current_claims"]
    assert baseline["expected_historical_claims"]["recall"] == 1


def test_baseline_budget_counts_envelope_and_preserves_whole_unicode_text():
    q = {"question": "Unicode?", "expected_evidence": ["é" * 100, "x" * 1000]}
    candidates = [{"path": "a.md", "text": "é" * 100}, {"path": "b.md", "text": "x" * 1000}]
    result = budget_baseline(q, candidates, 512)
    assert result["within_budget"]
    assert result["response"]["chunks"] == candidates[:1]
    assert result["text_availability"]["expected_evidence"]["recall"] == 0.5
    assert result["response"]["truncated"]


@pytest.mark.parametrize(
    "violation",
    [
        None,
        "within_budget",
        "conflict_closed",
        "truncated_correct",
        "provenance",
        "errors",
        "performance",
    ],
)
def test_gate_includes_all_pack_safety_and_performance_not_recall(violation):
    from api.services.memory_benchmark import _apply_gate

    pack = {
        "budget": 1000,
        "within_budget": True,
        "conflict_closed": True,
        "truncated_correct": True,
        "provenance": {"passed": True},
        "errors": [],
        "scores": {"expected_current_claims": {"passed": False, "recall": 0}},
    }
    report = {
        "queries": [{"id": "passing-primary", "passed": True, "packs": [pack]}],
        "replay_checks": [{"passed": True}],
        "operation_measurements": {},
        "performance": [{"documents": 2, "measurements": {"current": {"errors": []}}}],
    }
    if violation == "performance":
        report["performance"][0]["measurements"]["current"]["errors"] = ["SQL failed"]
    elif violation == "provenance":
        pack["provenance"]["passed"] = False
    elif violation == "errors":
        pack["errors"] = ["operation failed"]
    elif violation:
        pack[violation] = False
    _apply_gate(report)
    assert report["passed"] is (violation is None)
    assert bool(report["safety_failures"]) is (violation is not None)


async def test_generation_route_wiring_not_quality(workload_file, postgres_url, monkeypatch):
    """Mock provider + fixture vectors test actual ask wiring ONLY, not quality."""
    from unittest.mock import patch

    from api.services import memory_benchmark as benchmark

    class WiringProvider:
        model_name = "MOCK-WIRING-NOT-QUALITY"
        provider = object()
        prompts = []

        async def generate(self, prompt):
            self.prompts.append(prompt)
            return "Mock wiring answer, not a quality measurement"

    provider = WiringProvider()
    monkeypatch.setattr("api.services.llm_service.LLMService", lambda: provider)
    monkeypatch.setenv("LLM_PROVIDER", "wiring-only")
    with patch("api.services.embedder.Embedder", return_value=FixtureEmbedder()):
        from api.routers import query as route
    bindings = (route.session_scope, route.embedder, route.llm_service)
    async with memory_benchmark_workload(workload_file, database_url=postgres_url) as workload:
        q = workload.spec["queries"][0]
        await workload.apply_stage("A")
        # Public opt-in refuses fixture vectors, but direct harness is testable offline.
        skipped = await benchmark._capture_generation_baseline(workload, q, True)
        assert skipped["status"] == "NOT RUN"
        result = await benchmark._ask_route_baseline(workload, q)
        assert result["route"] == "/api/query/ask" and result["top_k"] == 5
        assert result["response"]["model"] == "MOCK-WIRING-NOT-QUALITY"
        assert "The database is Redis." in provider.prompts[-1]
        assert "The database is PostgreSQL." not in provider.prompts[-1]
        await workload.apply_stage("B")
        await benchmark._ask_route_baseline(workload, {**q, "stage": "B"})
        assert "The database is PostgreSQL." in provider.prompts[-1]
        assert "The database is Redis." not in provider.prompts[-1]
        assert bindings == (route.session_scope, route.embedder, route.llm_service)

        async def fail(prompt):
            raise RuntimeError("mock failure")

        monkeypatch.setattr(provider, "generate", fail)
        with pytest.raises(RuntimeError, match="mock failure"):
            await benchmark._ask_route_baseline(workload, q)
        assert bindings == (route.session_scope, route.embedder, route.llm_service)
        assert not benchmark._ASK_HARNESS_LOCK.locked()


def test_cached_embedder_requests_local_files_only(monkeypatch):
    from api.services.memory_benchmark import CachedEmbedder

    seen = []

    class LocalModel:
        def __init__(self, name, **kwargs):
            seen.append(kwargs)

        def get_embedding_dimension(self):
            return 384

    monkeypatch.setattr("sentence_transformers.SentenceTransformer", LocalModel)
    assert CachedEmbedder().dimension == 384
    assert seen == [{"local_files_only": True}]


def test_latency_quantiles_require_twenty_samples():
    assert latency_summary([1])["p95_ms"] is None
    summary = latency_summary(list(range(1, 21)))
    assert summary["p50_ms"] == 10.5
    assert summary["p95_ms"] == 19
    assert summary["n"] == 20


@pytest.mark.parametrize("repetitions", [0, -1, True, False, 1001, 1.0])
async def test_reject_invalid_repetitions(workload_file, repetitions):
    with pytest.raises(ValueError, match="repetitions"):
        await run_memory_benchmark(workload_file, repetitions=repetitions)


@pytest.fixture
def postgres_url():
    url = os.getenv("MEMORY_TEST_DATABASE_URL") or os.getenv("DATABASE_URL")
    if not url:
        pytest.skip("requires MEMORY_TEST_DATABASE_URL or DATABASE_URL (PostgreSQL + pgvector)")
    return url


async def test_sandbox_ingestion_rollback_and_identity(workload_file, postgres_url):
    fingerprints = []
    schemas = []
    for _ in range(2):
        async with memory_benchmark_workload(workload_file, database_url=postgres_url) as workload:
            schemas.append(workload.schema)
            assert workload.corpus_id == corpus_id_for_name(workload.corpus)
            assert workload.spec_hash in workload.corpus
            captured = await workload.apply_stage("A")
            fingerprints.append(workload.normalize(captured))
            with pytest.raises(ValueError, match="declared order"):
                await workload.apply_stage("D")
            async with workload.sessions() as db:
                assert (await db.execute(text("SELECT count(*) FROM chunks"))).scalar_one() == 1
    assert schemas[0] != schemas[1]
    assert fingerprints[0] == fingerprints[1]
    engine = create_async_engine(
        make_url(postgres_url).set(drivername="postgresql+asyncpg"), poolclass=NullPool
    )
    try:
        async with engine.connect() as conn:
            for schema in schemas:
                assert (
                    await conn.execute(
                        text("SELECT 1 FROM pg_namespace WHERE nspname=:s"), {"s": schema}
                    )
                ).first() is None
    finally:
        await engine.dispose()


async def test_real_benchmark_reports_failures_not_fake_wins(workload_file, postgres_url):
    report = await run_memory_benchmark(workload_file, repetitions=1)
    rows = {q["id"]: q for q in report["queries"]}
    for id, row in rows.items():
        assert not row["memory"]["errors"], row
        if id != "deliberate_failure":
            assert all(s["passed"] for s in row["memory"]["scores"].values()), row
        assert row["memory"]["provenance"]["passed"]
        assert row["generation"]["status"] == "NOT RUN"
        assert report["environment"]["observations"][f"queries/{id}/baseline/latency"]["n"] == 1
        assert row["baseline"]["status_distinguishable"] is False
        assert "text_recoverable" in row["baseline"]
        assert set(row["memory"]["scores"]) == set(row["expectations"])
        assert not row["baseline"]["errors"]
        for pack in row["packs"]:
            assert not pack["errors"], pack
            assert pack["within_budget"] and pack["conflict_closed"]
            assert pack["truncated_correct"]
            assert pack["provenance"]["passed"]
    assert all(check["passed"] for check in report["replay_checks"])
    assert any(
        f.get("query") == "deliberate_failure" and f["strategy"] == "memory"
        for f in report["failures"]
    )
    assert report["counts"]["memory_versions"] == 6
    assert report["counts"]["memory_claims"] == 4
    assert report["counts"]["lifecycle"] == {"NEW": 2, "MODIFIED": 1, "DELETED": 2, "RESTORED": 1}
    assert report["metrics"]["current_exact_set_accuracy"]["mean"] < 1
    assert "Artificial" in report["embedding_warning"]
    assert report["performance"] == []
    assert report["metrics"]["provenance_completeness"]["mean"] == 1
    assert rows["initial"]["executed_operation"] == "as-of"
    assert rows["deletion"]["passed"]
    assert rows["restored"]["passed"]
    assert rows["replay"]["memory"]["snapshot_reproducible"]
    assert "Generation: NOT RUN" in format_report(report)
    assert set(report["environment"]["latency_by_operation"]) == {
        "current",
        "history",
        "changes",
        "evidence",
        "as-of",
        "snapshot",
        "replay",
        "pack",
    }
    assert all(
        t["n"] == 1 and t["p50_ms"] is None and t["p95_ms"] is None
        for t in report["environment"]["latency_by_operation"].values()
    )
    assert all(r["baseline"]["generation"]["status"] == "NOT RUN" for r in report["queries"])
    assert rows["initial"]["natural_language"]["current_exact_set"]["passed"] is False
    second = await run_memory_benchmark(workload_file, repetitions=1)
    json.dumps(report, allow_nan=False)
    assert {k: v for k, v in report.items() if k != "environment"} == {
        k: v for k, v in second.items() if k != "environment"
    }


async def test_failed_stage_also_rolls_back(workload_file, postgres_url):
    (workload_file.parent / "A" / "db.md").write_text(
        source("Redis")
        .replace("# Database", "# Database\n")
        .replace("\nThe database is Redis.\n", "\nNo evidence.\n")
    )
    with pytest.raises(ValueError, match="evidence"):
        async with memory_benchmark_workload(workload_file, database_url=postgres_url) as workload:
            await workload.apply_stage("A")


async def test_tiny_budget_and_atomic_conflicts(workload_file, postgres_url):
    async with memory_benchmark_workload(workload_file, database_url=postgres_url) as workload:
        await workload.apply_stage("A")
        await workload.apply_stage("B")
        full = await workload.execute(
            "pack",
            MemoryRequest(corpus=workload.corpus, budget=128000, as_of=workload.cutoffs["B"]),
        )
        for budget in (512, 1000, 4000, 16000, 128000):
            request = MemoryRequest(
                corpus=workload.corpus, budget=budget, as_of=workload.cutoffs["B"]
            )
            pack = await workload.execute("pack", request)
            assert len(pack.canonical_json()) <= budget
            assert pack.truncated == (len(full.canonical_json()) > budget)
            ids = {e.id for e in pack.evidence}
            claims = pack.current_memories + pack.historical_memories + pack.uncertain_memories
            claims += [c for group in pack.conflicts for c in group.claims]
            claims += [c for change in pack.changes for c in change.previous + change.current]
            assert all(c.evidence_ids and set(c.evidence_ids) <= ids for c in claims)
            alternatives = {"The database is PostgreSQL.", "The database is SQLite."}
            selected = {c.claim for c in claims}
            assert not selected & alternatives or alternatives <= selected
        with pytest.raises(MemoryError, match="Budget"):
            # Long query fills the response envelope even at the public minimum.
            await workload.execute(
                "pack", MemoryRequest(corpus=workload.corpus, query="x" * 1000, budget=512)
            )
    spec = yaml.safe_load(workload_file.read_text())
    spec["queries"] = [{**spec["queries"][-2], "budget": 1}]
    workload_file.write_text(yaml.safe_dump(spec))
    assert load_spec(workload_file)["queries"][0]["budget"] == 1
    report = await run_memory_benchmark(workload_file, repetitions=1)
    row = report["queries"][0]
    assert not row["passed"]
    assert set(row["memory"]["scores"]) == set(row["expectations"])
    assert next(p for p in row["packs"] if p["budget"] == 1)["budget_rejected"]


async def test_supersession_checks_status_and_linkage(workload_file, postgres_url):
    (workload_file.parent / "B" / "other.md").unlink()
    async with memory_benchmark_workload(workload_file, database_url=postgres_url) as workload:
        await workload.apply_stage("A")
        await workload.apply_stage("B")
        response = await workload.execute("history", MemoryRequest(corpus=workload.corpus))
        labels = {
            "expected_supersession": [
                {"previous": "The database is Redis.", "current": "The database is PostgreSQL."}
            ]
        }
        texts = {c.id: c.claim for c in response.current_memories + response.historical_memories}
        assert score_expectations(labels, response, workload.normalize(response), texts)[
            "expected_supersession"
        ]["passed"]
        response.current_memories[0].supersedes_id = None
        for change in response.changes:
            for claim in change.current:
                claim.supersedes_id = None
        assert not score_expectations(labels, response, workload.normalize(response), texts)[
            "expected_supersession"
        ]["passed"]


async def test_snapshot_timing_creates_fresh_rows_and_rolls_back(
    workload_file, postgres_url, monkeypatch
):
    from api.services import memory_benchmark as benchmark

    original = benchmark.execute_in_session
    snapshots = []

    async def observe(db, operation, request):
        response = await original(db, operation, request)
        if operation == "snapshot":
            snapshots.append((request.as_of, response.snapshot.id))
            assert (
                await db.execute(
                    text("SELECT count(*) FROM memory_snapshots WHERE id=:id"),
                    {"id": response.snapshot.id},
                )
            ).scalar_one() == 1
        return response

    async with memory_benchmark_workload(workload_file, database_url=postgres_url) as workload:
        await workload.apply_stage("A")
        before = (await workload.counts())["memory_snapshots"]
        monkeypatch.setattr(benchmark, "execute_in_session", observe)
        results = await benchmark._operation_timings(workload, 1)
        # One setup snapshot remains for replay; warmup and measured writes do not.
        assert (await workload.counts())["memory_snapshots"] == before + 1
        assert len(snapshots) == 3
        assert all(cutoff is None for cutoff, _ in snapshots)
        assert len({id for _, id in snapshots}) == 3
        assert results["snapshot"]["latency"]["n"] == 1
        assert results["snapshot"]["latency"]["p95_ms"] is None
        assert results["snapshot"]["result"]["claim_count"] == 1
        assert results["snapshot"]["sql"]["statement_counts"][0] > 0


async def test_scaling_is_bounded_and_covers_public_operations(workload_file, postgres_url):
    with pytest.raises(ValueError, match="cap"):
        await run_memory_benchmark(workload_file, corpus_sizes=(3000, 3000))
    result = await _performance(workload_file, "fixture", 2, 1)
    assert result["counts"]["memory_versions"] == 2
    assert set(result["measurements"]) == {
        "current",
        "history",
        "changes",
        "evidence",
        "as-of",
        "snapshot",
        "replay",
        "pack",
    }
    assert all(not m["errors"] for m in result["measurements"].values())
    assert all(m["latency"]["n"] == 1 for m in result["measurements"].values())
    assert all(m["result"]["characters"] > 0 for m in result["measurements"].values())
    assert all(
        m["result"]["utf8_bytes"] >= m["result"]["characters"]
        for m in result["measurements"].values()
    )
    assert all(m["sql"]["statement_counts"][0] > 0 for m in result["measurements"].values())


@pytest.fixture
def real_spec_file(postgres_url):
    file = Path(__file__).resolve().parents[1] / "eval/memory_benchmarks.yaml"
    if not file.is_file():
        pytest.skip(
            "M006 shared eval/memory_benchmarks.yaml not present yet; dataset agent pending"
        )
    raw = yaml.safe_load(file.read_text())
    missing = [s["directory"] for s in raw["stages"] if not (file.parent / s["directory"]).is_dir()]
    if missing:
        pytest.skip("M006 shared stage corpus not present yet: " + ", ".join(missing))
    return file


async def test_shared_yaml_complete_run_and_determinism(real_spec_file):
    spec = load_spec(real_spec_file)
    report = await run_memory_benchmark(real_spec_file, repetitions=1)
    assert len(report["queries"]) == len(spec["queries"])
    assert report["passed"], report["safety_failures"]
    assert report["natural_language_diagnostics"]["nonempty_labels"]["mean"] < 1
    assert not report["natural_language_diagnostics"]["in_gate"]
    for query, row in zip(spec["queries"], report["queries"]):
        assert row["id"] == query["id"]
        assert set(row["memory"]["scores"]) == {k for k in query if k.startswith("expected_")}
        assert row["memory"]["provenance"]["fraction"] == 1
        assert row["baseline"]["stage"] == query["stage"]
        assert "text_recoverable" in row["baseline"]
    for category in {q["query_type"] for q in spec["queries"]}:
        assert report["metrics"][f"category_{category}_accuracy"]["mean"] == 1
    # Independent file-state accounting: repeated full-state files are unchanged,
    # tombstones have no claims, and restores produce new archived evidence.
    live, deleted = {}, set()
    versions = claims = evidence = 0
    lifecycle = {}
    for stage in spec["stages"]:
        root = Path(stage["directory"])
        for path in sorted(root.rglob("*.md")):
            name, content = path.relative_to(root).as_posix(), path.read_text()
            if live.get(name) == content:
                continue
            event = "RESTORED" if name in deleted else "MODIFIED" if name in live else "NEW"
            versions += 1
            lifecycle[event] = lifecycle.get(event, 0) + 1
            metadata = yaml.safe_load(content.split("---", 2)[1])
            authored = metadata.get("claims", [])
            claims += len(authored)
            evidence += sum(
                len(c["evidence"]) if isinstance(c, dict) and isinstance(c["evidence"], list) else 1
                for c in authored
            )
            live[name] = content
            deleted.discard(name)
        for name in stage.get("delete", []):
            if name in live:
                versions += 1
                lifecycle["DELETED"] = lifecycle.get("DELETED", 0) + 1
                del live[name]
                deleted.add(name)
    assert report["counts"]["memory_versions"] == versions
    assert report["counts"]["memory_claims"] == claims
    assert report["counts"]["memory_evidence"] == evidence
    assert evidence > claims  # Shared corpus exercises migration 005's multiple quotes.
    assert report["counts"]["documents"] == len(live)
    assert report["counts"]["memory_snapshots"] == len(spec["stages"])
    assert report["counts"]["lifecycle"] == lifecycle
    assert all(c["passed"] for c in report["replay_checks"])
    second = await run_memory_benchmark(real_spec_file, repetitions=1)
    assert json.loads(json.dumps(report, allow_nan=False)) == report
    assert {k: v for k, v in report.items() if k != "environment"} == {
        k: v for k, v in second.items() if k != "environment"
    }
