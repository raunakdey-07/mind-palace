# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 Raunak Dey

"""Remote-only memory commands; validation and semantics live in the central API."""

from __future__ import annotations

from typing import Annotated

import typer

from mindpalace_sdk import MemoryClientError, MindPalace

app = typer.Typer(
    name="memory",
    help=(
        "Authoritative persistent corpus memory operations "
        "(current, history, evidence, feed, snapshots, packs)."
    ),
)

Corpus = Annotated[str, typer.Option("--corpus", help="Corpus name (required).")]
Query = Annotated[str, typer.Option("--query")]
BaseURL = Annotated[str, typer.Option("--base-url")]
AsOf = Annotated[str | None, typer.Option("--as-of", help="Aware ISO-8601 observation cutoff.")]
ValidAt = Annotated[str | None, typer.Option("--valid-at", help="Aware ISO-8601 validity time.")]
Path = Annotated[str | None, typer.Option("--path")]
DEFAULT_BASE_URL = "http://127.0.0.1:8000"


def _dispatch(method: str, corpus: str, query: str, base_url: str, **fields) -> None:
    try:
        memory = MindPalace(base_url=base_url).memory
        response = getattr(memory, method)(corpus=corpus, query=query, **fields)
        typer.echo(response.canonical_json())
    except MemoryClientError as exc:
        typer.echo(f"{exc.code}: {exc.message}", err=True)
        raise typer.Exit(code=1) from exc
    except ValueError as exc:
        typer.echo(str(exc), err=True)
        raise typer.Exit(code=2) from exc


@app.command("query")
def query_memory(
    corpus: Corpus,
    query: Query,
    intent: Annotated[
        str,
        typer.Option(
            "--intent",
            help="auto, current, historical, temporal, change, conflict, or provenance.",
        ),
    ] = "auto",
    budget: Annotated[int, typer.Option("--budget")] = 8000,
    as_of: AsOf = None,
    valid_at: ValidAt = None,
    path: Path = None,
    base_url: BaseURL = DEFAULT_BASE_URL,
    claim_id: Annotated[str | None, typer.Option("--claim-id")] = None,
    snapshot_id: Annotated[str | None, typer.Option("--snapshot-id")] = None,
) -> None:
    """Query memory with intent-aware, budgeted evidence."""
    _dispatch(
        "query",
        corpus,
        query,
        base_url,
        intent=intent,
        budget=budget,
        as_of=as_of,
        valid_at=valid_at,
        path=path,
        claim_id=claim_id,
        snapshot_id=snapshot_id,
    )


@app.command()
def current(
    corpus: Corpus,
    query: Query = "",
    valid_at: ValidAt = None,
    path: Path = None,
    base_url: BaseURL = DEFAULT_BASE_URL,
) -> None:
    """Read current memory."""
    _dispatch("current", corpus, query, base_url, valid_at=valid_at, path=path)


@app.command()
def history(
    corpus: Corpus,
    query: Query = "",
    as_of: AsOf = None,
    valid_at: ValidAt = None,
    path: Path = None,
    base_url: BaseURL = DEFAULT_BASE_URL,
) -> None:
    """Read historical memory."""
    _dispatch("history", corpus, query, base_url, as_of=as_of, valid_at=valid_at, path=path)


@app.command()
def changes(
    corpus: Corpus,
    query: Query = "",
    as_of: AsOf = None,
    valid_at: ValidAt = None,
    path: Path = None,
    base_url: BaseURL = DEFAULT_BASE_URL,
) -> None:
    """Read memory changes."""
    _dispatch("changes", corpus, query, base_url, as_of=as_of, valid_at=valid_at, path=path)


@app.command()
def feed(
    corpus: Corpus,
    page_size: Annotated[
        int, typer.Option("--page-size", "--limit", min=1, max=500, help="Items per page (1-500).")
    ] = 50,
    cursor: Annotated[str | None, typer.Option("--cursor")] = None,
    base_url: BaseURL = DEFAULT_BASE_URL,
) -> None:
    """Consume the durable corpus-scoped change feed."""
    try:
        response = MindPalace(base_url=base_url).memory.feed(
            corpus=corpus, page_size=page_size, cursor=cursor
        )
        typer.echo(response.canonical_json())
    except MemoryClientError as exc:
        typer.echo(f"{exc.code}: {exc.message}", err=True)
        raise typer.Exit(code=1) from exc


@app.command()
def evidence(
    corpus: Corpus,
    claim_id: Annotated[str, typer.Option("--claim-id")],
    query: Query = "",
    as_of: AsOf = None,
    valid_at: ValidAt = None,
    path: Path = None,
    base_url: BaseURL = DEFAULT_BASE_URL,
) -> None:
    """Read evidence for a claim."""
    _dispatch(
        "evidence",
        corpus,
        query,
        base_url,
        claim_id=claim_id,
        as_of=as_of,
        valid_at=valid_at,
        path=path,
    )


@app.command("as-of")
def as_of(
    corpus: Corpus,
    timestamp: Annotated[str, typer.Option("--as-of", help="Aware ISO-8601 cutoff (required).")],
    query: Query = "",
    valid_at: ValidAt = None,
    path: Path = None,
    base_url: BaseURL = DEFAULT_BASE_URL,
) -> None:
    """Read memory at an observation cutoff."""
    _dispatch("as_of", corpus, query, base_url, timestamp=timestamp, valid_at=valid_at, path=path)


@app.command()
def snapshot(
    corpus: Corpus,
    query: Query = "",
    as_of: AsOf = None,
    valid_at: ValidAt = None,
    path: Path = None,
    base_url: BaseURL = DEFAULT_BASE_URL,
) -> None:
    """Create a memory snapshot."""
    _dispatch("snapshot", corpus, query, base_url, as_of=as_of, valid_at=valid_at, path=path)


@app.command()
def replay(
    corpus: Corpus,
    snapshot_id: Annotated[str, typer.Option("--snapshot-id")],
    query: Query = "",
    base_url: BaseURL = DEFAULT_BASE_URL,
    path: Path = None,
) -> None:
    """Replay a saved snapshot."""
    _dispatch("replay_snapshot", corpus, query, base_url, snapshot_id=snapshot_id, path=path)


@app.command()
def pack(
    corpus: Corpus,
    query: Query = "",
    budget: Annotated[int, typer.Option("--budget")] = 8000,
    as_of: AsOf = None,
    valid_at: ValidAt = None,
    path: Path = None,
    base_url: BaseURL = DEFAULT_BASE_URL,
    snapshot_id: Annotated[str | None, typer.Option("--snapshot-id")] = None,
) -> None:
    """Pack memory into a budgeted response."""
    _dispatch(
        "pack",
        corpus,
        query,
        base_url,
        budget=budget,
        as_of=as_of,
        valid_at=valid_at,
        path=path,
        snapshot_id=snapshot_id,
    )
