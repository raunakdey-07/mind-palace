# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 Raunak Dey

"""mindpalace CLI — Typer-based command-line interface."""

from __future__ import annotations

import json
from typing import Optional

import typer
import yaml

from cli.memory import app as memory_app

app = typer.Typer(name="mindpalace", help="Mind Palace CLI")
app.add_typer(memory_app)

eval_app = typer.Typer(name="eval", help="Evaluation commands")
app.add_typer(eval_app)


@app.command()
def ingest(
    path: str = typer.Argument(..., help="Path to a Markdown file or directory"),
) -> None:
    """Ingest Markdown file(s) into the knowledge store."""
    import httpx

    path = path.rstrip("/")
    url = "http://localhost:8000/api/ingest/file"

    try:
        with open(path, "r", encoding="utf-8") as f:
            content = f.read()
        resp = httpx.post(url, json={"content": content, "path": path}, timeout=30)
        resp.raise_for_status()
        data = resp.json()
        typer.echo(f"[OK] {data.get('message', 'Ingested')}")
    except FileNotFoundError:
        typer.echo(f"[ERROR] File not found: {path}", err=True)
        raise typer.Exit(code=1)
    except httpx.HTTPError as e:
        typer.echo(f"[ERROR] API error: {e}", err=True)
        raise typer.Exit(code=1)


@app.command()
def ingest_repo(
    path: str = typer.Argument(..., help="Path to the content repository"),
) -> None:
    """Batch ingest all Markdown files from a repository."""
    import httpx

    url = "http://localhost:8000/api/ingest/repo"
    try:
        resp = httpx.post(url, json={"repo_path": path}, timeout=300)
        resp.raise_for_status()
        data = resp.json()
        typer.echo(f"[OK] {data.get('message', 'Ingested')}")
    except httpx.HTTPError as e:
        typer.echo(f"[ERROR] API error: {e}", err=True)
        raise typer.Exit(code=1)


@app.command()
def search(
    query: str = typer.Argument(..., help="Search query"),
    corpus: Optional[str] = typer.Option(
        None, "--corpus", help="Corpus name (required when multiple corpora exist)"
    ),
    k: int = typer.Option(5, "--k", "-k", help="Number of results"),
    document_type: Optional[str] = typer.Option(
        None, "--type", "-t", help="Filter by document type"
    ),
    tags: Optional[str] = typer.Option(None, "--tags", help="Comma-separated tags to filter by"),
    hybrid: bool = typer.Option(False, "--hybrid", help="Enable hybrid search"),
) -> None:
    """Semantic search over ingested content."""
    import httpx

    url = "http://localhost:8000/api/search"
    params = {"q": query, "k": k}
    if corpus:
        params["corpus"] = corpus
    if document_type:
        params["document_type"] = document_type
    if tags:
        params["tags"] = tags
    if hybrid:
        params["hybrid"] = "true"

    try:
        resp = httpx.get(url, params=params, timeout=30)
        resp.raise_for_status()
        data = resp.json()
        typer.echo(f'\n[SEARCH] Search: "{data["query"]}" ({data["total"]} results)\n')
        for i, r in enumerate(data["results"], 1):
            typer.echo(f"--- Result {i} (score: {r['score']:.3f}) ---")
            source = r.get("source_title") or r.get("source_id", "unknown")
            if r.get("heading_path"):
                source += f" > {r['heading_path']}"
            typer.echo(f"Source: {source}")
            if r.get("document_type"):
                typer.echo(f"Type: {r['document_type']}")
            typer.echo(f"{r['text'][:300]}")
            typer.echo()
    except httpx.HTTPError as e:
        typer.echo(f"[ERROR] API error: {e}", err=True)
        raise typer.Exit(code=1)


@app.command()
def ask(
    question: str = typer.Argument(..., help="Question to ask"),
    corpus: Optional[str] = typer.Option(
        None, "--corpus", help="Corpus name (required when multiple corpora exist)"
    ),
    k: int = typer.Option(5, "--k", "-k", help="Number of retrieved chunks"),
    document_type: Optional[str] = typer.Option(
        None, "--type", "-t", help="Filter by document type"
    ),
    tags: Optional[str] = typer.Option(None, "--tags", help="Comma-separated tags to filter by"),
) -> None:
    """Optional LLM-backed answer generation; use `memory` for authoritative corpus memory."""
    import httpx

    url = "http://localhost:8000/api/query/ask"
    payload = {"question": question, "k": k}
    if corpus:
        payload["corpus"] = corpus
    if document_type:
        payload["document_type"] = document_type
    if tags:
        payload["tags"] = [t.strip() for t in tags.split(",")]

    try:
        resp = httpx.post(url, json=payload, timeout=120)
        resp.raise_for_status()
        data = resp.json()
        typer.echo(
            (
                f"\n[ANSWER] Answer ({data['latency_ms']}ms,"
                f" {data['retrieved_chunks']} chunks):\n"
                f"{data['answer']}\n"
            )
        )
        if data["sources"]:
            typer.echo(f"[SOURCES] Sources: {', '.join(data['sources'])}")
    except httpx.HTTPError as e:
        typer.echo(f"[ERROR] API error: {e}", err=True)
        raise typer.Exit(code=1)


@app.command()
def summarize(
    document_id: str = typer.Argument(..., help="Document ID to summarize"),
    corpus: Optional[str] = typer.Option(
        None, "--corpus", help="Corpus name (required when multiple corpora exist)"
    ),
    max_length: int = typer.Option(500, "--max-length", help="Max summary length"),
) -> None:
    """Summarize a specific document."""
    import httpx

    url = "http://localhost:8000/api/query/summarize"
    try:
        resp = httpx.post(
            url,
            json={"document_id": document_id, "corpus": corpus, "max_length": max_length},
            timeout=120,
        )
        resp.raise_for_status()
        data = resp.json()
        typer.echo(f"\n[SUMMARY] Summary ({data['latency_ms']}ms):\n{data['answer']}\n")
        if data["sources"]:
            typer.echo(f"[SOURCES] Source: {', '.join(data['sources'])}")
    except httpx.HTTPError as e:
        typer.echo(f"[ERROR] API error: {e}", err=True)
        raise typer.Exit(code=1)


@app.command()
def interview(
    document_id: str = typer.Argument(..., help="Document ID to generate questions from"),
    corpus: Optional[str] = typer.Option(
        None, "--corpus", help="Corpus name (required when multiple corpora exist)"
    ),
    num_questions: int = typer.Option(5, "--num", "-n", help="Number of questions"),
    difficulty: str = typer.Option(
        "medium", "--difficulty", "-d", help="Difficulty: easy, medium, hard"
    ),
) -> None:
    """Generate interview questions from a document."""
    import httpx

    url = "http://localhost:8000/api/query/interview"
    try:
        resp = httpx.post(
            url,
            json={
                "document_id": document_id,
                "corpus": corpus,
                "num_questions": num_questions,
                "difficulty": difficulty,
            },
            timeout=120,
        )
        resp.raise_for_status()
        data = resp.json()
        typer.echo(
            f"\n[INTERVIEW] Interview Questions ({data['latency_ms']}ms):\n{data['answer']}\n"
        )
        if data["sources"]:
            typer.echo(f"[SOURCES] Source: {', '.join(data['sources'])}")
    except httpx.HTTPError as e:
        typer.echo(f"[ERROR] API error: {e}", err=True)
        raise typer.Exit(code=1)


@app.command()
def related(
    document_id: str = typer.Argument(..., help="Document ID to find related docs for"),
    corpus: Optional[str] = typer.Option(
        None, "--corpus", help="Corpus name (required when multiple corpora exist)"
    ),
    k: int = typer.Option(5, "--k", "-k", help="Number of related documents"),
) -> None:
    """Find documents related to a given document."""
    import httpx

    url = "http://localhost:8000/api/query/related"
    try:
        resp = httpx.post(
            url,
            json={"document_id": document_id, "corpus": corpus, "k": k},
            timeout=30,
        )
        resp.raise_for_status()
        data = resp.json()
        typer.echo(f"\n[RELATED] Related Documents ({data['latency_ms']}ms):\n{data['answer']}\n")
    except httpx.HTTPError as e:
        typer.echo(f"[ERROR] API error: {e}", err=True)
        raise typer.Exit(code=1)


@app.command()
def timeline(
    corpus: Optional[str] = typer.Option(
        None, "--corpus", help="Corpus name (required when multiple corpora exist)"
    ),
    document_type: Optional[str] = typer.Option(
        None, "--type", "-t", help="Filter by document type"
    ),
    start_date: Optional[str] = typer.Option(None, "--start", help="Start date (YYYY-MM-DD)"),
    end_date: Optional[str] = typer.Option(None, "--end", help="End date (YYYY-MM-DD)"),
    limit: int = typer.Option(50, "--limit", "-l", help="Number of documents"),
) -> None:
    """Show chronological document timeline."""
    import httpx

    url = "http://localhost:8000/api/query/timeline"
    params: dict[str, int | str] = {"limit": limit}
    if corpus:
        params["corpus"] = corpus
    if document_type:
        params["document_type"] = document_type
    if start_date:
        params["start_date"] = start_date
    if end_date:
        params["end_date"] = end_date

    try:
        resp = httpx.get(url, params=params, timeout=30)
        resp.raise_for_status()
        data = resp.json()
        typer.echo(f"\n[TIMELINE] Timeline ({data['latency_ms']}ms):\n{data['answer']}\n")
    except httpx.HTTPError as e:
        typer.echo(f"[ERROR] API error: {e}", err=True)
        raise typer.Exit(code=1)


@app.command()
def reindex(
    corpus: str = typer.Argument(..., help="Corpus name to rebuild from the archive"),
) -> None:
    """Rebuild live search rows from archived source versions.

    This writes only the derived live projection. It does not create memory
    versions, claims, or feed events.
    """
    import asyncio
    import json

    from api.services.corpora import get_corpus_by_name
    from api.services.db import async_engine, session_scope
    from api.services.rehydrate import rehydrate_corpus

    async def run() -> dict:
        try:
            async with session_scope() as db, db.begin():
                corpus_row = await get_corpus_by_name(db, corpus)
                if not corpus_row:
                    raise ValueError(f"corpus '{corpus}' not found")
                return await rehydrate_corpus(db, corpus_row["id"])
        finally:
            await async_engine.dispose()

    try:
        result = asyncio.run(run())
    except (ValueError, OSError, RuntimeError) as exc:
        typer.echo(f"[ERROR] Reindex failed: {exc}", err=True)
        raise typer.Exit(code=1) from exc
    typer.echo(json.dumps(result, sort_keys=True))


@app.command()
def doctor() -> None:
    """Run system health checks."""

    import httpx

    checks_passed = 0
    total_checks = 0

    def check(name: str, condition: bool, fix_hint: str = ""):
        nonlocal checks_passed, total_checks
        total_checks += 1
        if condition:
            typer.echo(f"[OK] {name}")
            checks_passed += 1
        else:
            typer.echo(f"[ERROR] {name}")
            if fix_hint:
                typer.echo(f"   - Hint: {fix_hint}")

    # Check 1: API liveness
    try:
        resp = httpx.get("http://localhost:8000/health/live", timeout=5)
        check(
            "API liveness",
            resp.status_code == 200,
            "Start the API with: uvicorn api.main:app --reload",
        )
    except Exception:
        check("API liveness", False, "Start the API with: uvicorn api.main:app --reload")

    # Check 2: API readiness (the database-aware health signal)
    try:
        resp = httpx.get("http://localhost:8000/health/ready", timeout=5)
        check(
            "API readiness",
            resp.status_code == 200,
            "Check PostgreSQL connectivity and run migrations",
        )
    except Exception:
        check("API readiness", False, "Check PostgreSQL connectivity and run migrations")

    # Check 3: Postgres connectivity
    try:
        import asyncio

        from sqlalchemy import text

        from api.services.db import async_engine

        async def check_pg():
            async with async_engine.begin() as conn:
                await conn.execute(text("SELECT 1"))
            return True

        result = asyncio.run(check_pg())
        check(
            "Postgres connectivity",
            result,
            "Check docker-compose is running: docker-compose ps",
        )
    except Exception as e:
        check(
            "Postgres connectivity",
            False,
            f"Check docker-compose is running: docker-compose ps. Error: {e}",
        )

    # Check 4: Ollama availability
    try:
        resp = httpx.get("http://localhost:11434/api/tags", timeout=5)
        check(
            "Ollama availability",
            resp.status_code == 200,
            "Start Ollama: docker exec ollama ollama serve",
        )
    except Exception:
        check(
            "Ollama availability",
            False,
            "Start Ollama: docker exec ollama ollama serve",
        )

    # Check 5: Embedding model
    try:
        from api.services.embedder import Embedder

        embedder = Embedder()
        check(
            "Embedding model loaded",
            embedder.dimension > 0,
            "Check sentence-transformers is installed",
        )
    except Exception as e:
        check(
            "Embedding model loaded",
            False,
            f"Check sentence-transformers is installed. Error: {e}",
        )

    # Check 6: Content directory
    import os

    content_exists = os.path.exists("./content") and len(os.listdir("./content")) > 0
    check(
        "Content directory has files",
        content_exists,
        "Add Markdown files to ./content/",
    )

    # Check 7: Migrations (simplified)
    try:
        from alembic.config import Config

        Config("migrations/alembic.ini")
        # Just check if we can load the config
        check("Alembic config loads", True, "Run: alembic upgrade head")
    except Exception:
        check("Alembic config loads", False, "Run: alembic upgrade head")

    typer.echo(f"\n[HEALTH] Health Check: {checks_passed}/{total_checks} passed")
    if checks_passed == total_checks:
        typer.echo("[SUCCESS] All systems go!")
        raise typer.Exit(code=0)
    else:
        typer.echo("[WARN]  Some checks failed. See hints above.")
        raise typer.Exit(code=1)


@eval_app.command("memory")
def eval_memory(
    benchmark_file: str = typer.Option("eval/memory_benchmarks.yaml", "--file", "-f"),
    repetitions: int = typer.Option(20, "--repetitions", min=1, max=1000),
    sizes: str = typer.Option("", "--sizes", help="Optional synthetic sizes, e.g. 100,500,1000"),
    embeddings: str = typer.Option("fixture", "--embeddings", help="fixture or cached (offline)"),
    generation: bool = typer.Option(False, "--generation", help="Opt in to configured LLM costs"),
    save: Optional[str] = typer.Option(None, "--save", help="Write JSON to a NEW report file"),
) -> None:
    """Evaluate evolving memory in a rolled-back schema, never an existing corpus.

    Fixture vectors test semantics only. Use cached embeddings for live retrieval
    comparisons. Run standalone, not inside a serving process. p50/p95 need 20 runs.
    """
    import asyncio
    from pathlib import Path

    from api.services.memory_benchmark import format_report, run_memory_benchmark

    try:
        corpus_sizes = tuple(int(value.strip()) for value in sizes.split(",") if value.strip())
        if save and Path(save).exists():
            raise ValueError("Refusing to overwrite report; choose a new --save path")
        result = asyncio.run(
            run_memory_benchmark(
                benchmark_file,
                repetitions=repetitions,
                corpus_sizes=corpus_sizes,
                embeddings=embeddings,
                generation=generation,
            )
        )
        if save:
            with open(save, "x", encoding="utf-8") as report:
                json.dump(result, report, ensure_ascii=False, indent=2)
                report.write("\n")
        typer.echo(format_report(result))
    except (ValueError, OSError) as exc:
        typer.echo(f"[ERROR] {exc}", err=True)
        raise typer.Exit(code=2) from exc
    if not result["passed"]:
        raise typer.Exit(code=1)


@eval_app.command("strategies")
def eval_strategies(
    benchmark_file: str = typer.Option("eval/retrieval_benchmarks.yaml", "--file", "-f"),
    candidates: str = typer.Option(
        "20", "--candidates", "-c", help="Comma-separated reranker candidate pool sizes"
    ),
    details: bool = typer.Option(False, "--details", "-d", help="Show failure diagnostics"),
) -> None:
    """Compare vector/hybrid/RRF/reranked retrieval on the benchmark.

    Requires a live PostgreSQL + pgvector database with an ingested corpus
    (set DATABASE_URL). Does not require the API server.
    """
    import asyncio

    from api.services.benchmark import (
        format_failure_details,
        format_report,
        run_benchmark,
    )
    from api.services.confidence import format_comparison_table

    try:
        candidate_sizes = tuple(int(c.strip()) for c in candidates.split(",") if c.strip())
    except ValueError:
        typer.echo(f"[ERROR] Invalid candidate sizes: {candidates}", err=True)
        raise typer.Exit(code=1)

    try:
        results, failures, samples = asyncio.run(
            run_benchmark(benchmark_file, candidate_sizes=candidate_sizes)
        )
    except Exception as e:
        typer.echo(f"[ERROR] Benchmark failed: {e}", err=True)
        raise typer.Exit(code=1)

    typer.echo("\n[RESULTS] Retrieval strategy comparison\n")
    typer.echo(format_report(results, failures=failures))

    ordered = [samples[n] for n in ("vector", "hybrid", "hybrid+rrf") if n in samples]
    rerank = [samples[n] for n in samples if n.startswith("hybrid+rrf+rerank")]
    if "hybrid+rrf" in samples and (ordered or rerank):
        base = samples["hybrid+rrf"]
        others = [s for s in ordered + rerank if s.strategy != "hybrid+rrf"]
        typer.echo("\n[STATS] Paired bootstrap comparison (95% CI, resampled per query)\n")
        typer.echo(format_comparison_table(base, others, k=3, metric="recall"))
    if details and failures:
        typer.echo("\n[DEBUG] Failure diagnostics\n")
        typer.echo(format_failure_details(failures))
    typer.echo()


@eval_app.command("gate")
def eval_gate(
    benchmark_file: str = typer.Option("eval/retrieval_benchmarks.yaml", "--file", "-f"),
    save: str = typer.Option(None, "--save", help="Save results as a baseline JSON file"),
    baseline: str = typer.Option(None, "--baseline", help="Compare against a baseline JSON file"),
) -> None:
    """Retrieval quality gate: machine-readable results, optional regression check.

    With --save, records the current run as a baseline. With --baseline,
    compares the current run against a saved baseline and exits non-zero on
    statistically significant regression. Noise never fails the gate.
    """
    import asyncio

    from api.services.benchmark import run_benchmark
    from api.services.quality_gate import (
        collect_gate_results,
        compare_to_baseline,
        render_gate_report,
    )

    try:
        results, _failures, samples = asyncio.run(run_benchmark(benchmark_file))
    except Exception as e:
        typer.echo(f"[ERROR] Benchmark failed: {e}", err=True)
        raise typer.Exit(code=1)

    gate = collect_gate_results(results, samples)
    comparison = None
    if baseline:
        with open(baseline) as f:
            base_data = json.load(f)
        comparison = compare_to_baseline(samples, base_data)
        gate["comparison"] = comparison

    payload = json.dumps(gate, indent=2)
    if save:
        with open(save, "w") as f:
            f.write(payload)
        typer.echo(f"[GATE] Baseline saved to {save}")

    typer.echo(render_gate_report(gate, comparison))

    if save:
        typer.echo(payload)

    if comparison and comparison["regressions"]:
        raise typer.Exit(code=1)


@eval_app.command("retrieval")
def eval_retrieval(
    benchmark_file: str = typer.Option("eval/retrieval_benchmarks.yaml", "--file", "-f"),
    k: int = typer.Option(5, "--k", "-k", help="Top-k to evaluate"),
) -> None:
    """Run retrieval evaluation against benchmark dataset."""
    import httpx

    with open(benchmark_file) as f:
        benchmarks = yaml.safe_load(f)

    if not benchmarks:
        typer.echo("No benchmarks found")
        raise typer.Exit(code=1)

    url = "http://localhost:8000/api/search"
    total = len(benchmarks)
    passed = 0
    total_precision = 0.0

    typer.echo(f"\n[HEALTH] Running retrieval evaluation on {total} queries (k={k})...\n")

    for i, bm in enumerate(benchmarks, 1):
        query = bm["query"]
        expected = set(bm["expected"])
        category = bm.get("category", "general")

        try:
            resp = httpx.get(url, params={"q": query, "k": k}, timeout=30)
            resp.raise_for_status()
            data = resp.json()
            results = data["results"]
        except httpx.HTTPError as e:
            typer.echo(f"  [ERROR] Query {i}: API error - {e}")
            continue

        retrieved_titles = {r.get("source_title", "") for r in results}
        hits = expected & retrieved_titles
        precision = len(hits) / len(expected) if expected else 1.0
        total_precision += precision

        status = "[OK]" if precision == 1.0 else "[WARN]" if precision > 0 else "[ERROR]"
        typer.echo(f'  {status} Query {i} [{category}]: "{query}"')
        typer.echo(f"      Expected: {', '.join(expected)}")
        typer.echo(f"      Retrieved: {', '.join(retrieved_titles) or 'none'}")
        typer.echo(f"      Precision@{k}: {precision:.2f}")

        if precision == 1.0:
            passed += 1

    avg_precision = total_precision / total if total > 0 else 0
    typer.echo(
        f"\n[RESULTS] Results: {passed}/{total} perfect, Avg Precision@{k}: {avg_precision:.2f}"
    )

    if passed == total:
        typer.echo("[SUCCESS] All queries retrieved expected documents!")
    else:
        typer.echo(
            "[WARN]  Some queries missed expected documents — consider tuning chunking/embedding"
        )


@app.command()
def serve() -> None:
    """Start the local development server."""
    typer.echo("Run: uvicorn api.main:app --reload")
    typer.echo("Then visit http://localhost:8000/docs")


if __name__ == "__main__":
    app()
