"""mindpalace CLI — Typer-based command-line interface."""

from __future__ import annotations

from typing import Optional

import typer

app = typer.Typer(name="mindpalace", help="Mind Palace CLI")


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
        typer.echo(f"✅ {data.get('message', 'Ingested')}")
    except FileNotFoundError:
        typer.echo(f"❌ File not found: {path}", err=True)
        raise typer.Exit(code=1)
    except httpx.HTTPError as e:
        typer.echo(f"❌ API error: {e}", err=True)
        raise typer.Exit(code=1)


@app.command()
def search(
    query: str = typer.Argument(..., help="Search query"),
    k: int = typer.Option(5, "--k", "-k", help="Number of results"),
) -> None:
    """Semantic search over ingested content."""
    import httpx

    url = "http://localhost:8000/api/search"
    try:
        resp = httpx.get(url, params={"q": query, "k": k}, timeout=30)
        resp.raise_for_status()
        data = resp.json()
        typer.echo(f'\n🔍 Search: "{data["query"]}" ({data["total"]} results)\n')
        for i, r in enumerate(data["results"], 1):
            typer.echo(f"--- Result {i} (score: {r['score']:.3f}) ---")
            typer.echo(
                f"Source: {r.get('source_title') or r.get('source_id', 'unknown')}"
            )
            typer.echo(f"{r['text'][:300]}")
            typer.echo()
    except httpx.HTTPError as e:
        typer.echo(f"❌ API error: {e}", err=True)
        raise typer.Exit(code=1)


@app.command()
def chat(
    question: str = typer.Argument(..., help="Question to ask the AI"),
    k: int = typer.Option(5, "--k", "-k", help="Number of retrieved chunks"),
) -> None:
    """Ask a question using RAG (requires Ollama running)."""
    import httpx

    url = "http://localhost:8000/api/query"
    try:
        resp = httpx.post(url, json={"question": question, "k": k}, timeout=120)
        resp.raise_for_status()
        data = resp.json()
        typer.echo(f"\n🤖 Answer:\n{data['answer']}\n")
        if data["sources"]:
            typer.echo(f"📎 Sources: {', '.join(data['sources'])}")
    except httpx.HTTPError as e:
        typer.echo(f"❌ API error: {e}", err=True)
        raise typer.Exit(code=1)


@app.command()
def serve() -> None:
    """Start the local development server."""
    typer.echo("Run: uvicorn api.main:app --reload")
    typer.echo("Then visit http://localhost:8000/docs")
