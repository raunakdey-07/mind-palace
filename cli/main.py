# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 Raunak Dey

"""mindpalace CLI — Typer-based command-line interface."""

from __future__ import annotations

from typing import Optional

import typer
import yaml

app = typer.Typer(name="mindpalace", help="Mind Palace CLI")

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
        typer.echo(f"������✅ {data.get('message', 'Ingested')}")
    except FileNotFoundError:
        typer.echo(f"������❌ File not found: {path}", err=True)
        raise typer.Exit(code=1)
    except httpx.HTTPError as e:
        typer.echo(f"������❌ API error: {e}", err=True)
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
        typer.echo(f"������✅ {data.get('message', 'Ingested')}")
    except httpx.HTTPError as e:
        typer.echo(f"������❌ API error: {e}", err=True)
        raise typer.Exit(code=1)


@app.command()
def search(
    query: str = typer.Argument(..., help="Search query"),
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
        typer.echo(f'\n���������🔍 Search: "{data["query"]}" ({data["total"]} results)\n')
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
        typer.echo(f"������❌ API error: {e}", err=True)
        raise typer.Exit(code=1)


@app.command()
def ask(
    question: str = typer.Argument(..., help="Question to ask"),
    k: int = typer.Option(5, "--k", "-k", help="Number of retrieved chunks"),
    document_type: Optional[str] = typer.Option(
        None, "--type", "-t", help="Filter by document type"
    ),
    tags: Optional[str] = typer.Option(None, "--tags", help="Comma-separated tags to filter by"),
) -> None:
    """Ask a question using RAG (requires Ollama running)."""
    import httpx

    url = "http://localhost:8000/api/query/ask"
    payload = {"question": question, "k": k}
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
                f"\n���������🤖 Answer ({data['latency_ms']}ms,"
                f" {data['retrieved_chunks']} chunks):\n"
                f"{data['answer']}\n"
            )
        )
        if data["sources"]:
            typer.echo(f"���������📎 Sources: {', '.join(data['sources'])}")
    except httpx.HTTPError as e:
        typer.echo(f"������❌ API error: {e}", err=True)
        raise typer.Exit(code=1)


@app.command()
def summarize(
    document_id: str = typer.Argument(..., help="Document ID to summarize"),
    max_length: int = typer.Option(500, "--max-length", help="Max summary length"),
) -> None:
    """Summarize a specific document."""
    import httpx

    url = "http://localhost:8000/api/query/summarize"
    try:
        resp = httpx.post(
            url,
            json={"document_id": document_id, "max_length": max_length},
            timeout=120,
        )
        resp.raise_for_status()
        data = resp.json()
        typer.echo(f"\n���������📝 Summary ({data['latency_ms']}ms):\n{data['answer']}\n")
        if data["sources"]:
            typer.echo(f"���������📎 Source: {', '.join(data['sources'])}")
    except httpx.HTTPError as e:
        typer.echo(f"������❌ API error: {e}", err=True)
        raise typer.Exit(code=1)


@app.command()
def interview(
    document_id: str = typer.Argument(..., help="Document ID to generate questions from"),
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
                "num_questions": num_questions,
                "difficulty": difficulty,
            },
            timeout=120,
        )
        resp.raise_for_status()
        data = resp.json()
        typer.echo(
            f"\n���������🎤 Interview Questions ({data['latency_ms']}ms):\n{data['answer']}\n"
        )
        if data["sources"]:
            typer.echo(f"���������📎 Source: {', '.join(data['sources'])}")
    except httpx.HTTPError as e:
        typer.echo(f"������❌ API error: {e}", err=True)
        raise typer.Exit(code=1)


@app.command()
def related(
    document_id: str = typer.Argument(..., help="Document ID to find related docs for"),
    k: int = typer.Option(5, "--k", "-k", help="Number of related documents"),
) -> None:
    """Find documents related to a given document."""
    import httpx

    url = "http://localhost:8000/api/query/related"
    try:
        resp = httpx.post(url, json={"document_id": document_id, "k": k}, timeout=30)
        resp.raise_for_status()
        data = resp.json()
        typer.echo(f"\n���������🔗 Related Documents ({data['latency_ms']}ms):\n{data['answer']}\n")
    except httpx.HTTPError as e:
        typer.echo(f"������❌ API error: {e}", err=True)
        raise typer.Exit(code=1)


@app.command()
def timeline(
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
    params = {"limit": limit}
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
        typer.echo(f"\n���������📅 Timeline ({data['latency_ms']}ms):\n{data['answer']}\n")
    except httpx.HTTPError as e:
        typer.echo(f"������❌ API error: {e}", err=True)
        raise typer.Exit(code=1)


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
            typer.echo(f"������✅ {name}")
            checks_passed += 1
        else:
            typer.echo(f"������❌ {name}")
            if fix_hint:
                typer.echo(f"   ���� �� �� 💡 {fix_hint}")

    # Check 1: API health
    try:
        resp = httpx.get("http://localhost:8000/health", timeout=5)
        check(
            "API health",
            resp.status_code == 200,
            "Start the API with: uvicorn api.main:app --reload",
        )
    except Exception:
        check("API health", False, "Start the API with: uvicorn api.main:app --reload")

    # Check 2: Postgres connectivity
    try:
        import asyncio

        from api.services.db import async_engine

        async def check_pg():
            async with async_engine.begin() as conn:
                await conn.execute("SELECT 1")
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

    # Check 3: Ollama availability
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

    # Check 4: Embedding model
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

    # Check 5: Content directory
    import os

    content_exists = os.path.exists("./content") and len(os.listdir("./content")) > 0
    check(
        "Content directory has files",
        content_exists,
        "Add Markdown files to ./content/",
    )

    # Check 6: Migrations (simplified)
    try:
        from alembic.config import Config

        Config("migrations/alembic.ini")
        # Just check if we can load the config
        check("Alembic config loads", True, "Run: alembic upgrade head")
    except Exception:
        check("Alembic config loads", False, "Run: alembic upgrade head")

    typer.echo(f"\n���������📊 Health Check: {checks_passed}/{total_checks} passed")
    if checks_passed == total_checks:
        typer.echo("���������🎉 All systems go!")
        raise typer.Exit(code=0)
    else:
        typer.echo("������⚠������️  Some checks failed. See hints above.")
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

    typer.echo(f"\n���������📊 Running retrieval evaluation on {total} queries (k={k})...\n")

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
            typer.echo(f"  ���� �� �� ❌ Query {i}: API error - {e}")
            continue

        retrieved_titles = {r.get("source_title", "") for r in results}
        hits = expected & retrieved_titles
        precision = len(hits) / len(expected) if expected else 1.0
        total_precision += precision

        status = (
            "������✅" if precision == 1.0 else "������⚠������️" if precision > 0 else "������❌"
        )
        typer.echo(f'  {status} Query {i} [{category}]: "{query}"')
        typer.echo(f"      Expected: {', '.join(expected)}")
        typer.echo(f"      Retrieved: {', '.join(retrieved_titles) or 'none'}")
        typer.echo(f"      Precision@{k}: {precision:.2f}")

        if precision == 1.0:
            passed += 1

    avg_precision = total_precision / total if total > 0 else 0
    typer.echo(
        f"\n���������📈 Results: {passed}/{total} perfect, Avg Precision@{k}: {avg_precision:.2f}"
    )

    if passed == total:
        typer.echo("���������🎉 All queries retrieved expected documents!")
    else:
        typer.echo(
            "������⚠������️  Some queries missed expected documents — consider tuning "
            "chunking/embedding"
        )


@app.command()
def serve() -> None:
    """Start the local development server."""
    typer.echo("Run: uvicorn api.main:app --reload")
    typer.echo("Then visit http://localhost:8000/docs")
