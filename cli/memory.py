# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 Raunak Dey

"""Memory subcommands; validation and semantics live in the central API.

These run in-process by default, against the same local database as `recall`,
`explain`, `history`, `receipt` and `ingest-repo`. `--base-url` (or
`MIND_PALACE_BASE_URL`) switches the command to the HTTP client for a service
someone else runs.
"""

from __future__ import annotations

import os
from typing import Annotated

import typer
from typing import TYPE_CHECKING

# `mindpalace_sdk` reaches httpx, asyncio and yaml, which measured 28 ms of the
# ~400 ms `mindpalace --help` on this machine -- and `memory` is a subcommand
# that `--help` never runs. So it is imported inside the functions that use it,
# and named here for a type checker only.
#
# Not a module-level `__getattr__`: PEP 562 covers attribute access on the module
# object, and a global name inside a function in this module is looked up in the
# module's `__dict__` and then builtins, without consulting it. Every command here
# would raise NameError the first time it caught an exception. That failure is
# invisible until an error occurs, which is the worst time to find it.
if TYPE_CHECKING:  # pragma: no cover - names the lazy imports for a checker
    import mindpalace_sdk

    MemoryClientError = mindpalace_sdk.MemoryClientError
    MindPalace = mindpalace_sdk.MindPalace


app = typer.Typer(
    name="memory",
    help=(
        "Authoritative persistent corpus memory operations "
        "(current, history, evidence, feed, snapshots, packs)."
    ),
)

Corpus = Annotated[str, typer.Option("--corpus", help="Corpus name (required).")]
Query = Annotated[str, typer.Option("--query")]
BaseURL = Annotated[
    str | None,
    typer.Option(
        "--base-url",
        help=(
            "Use a running Mind Palace service instead of the local database, "
            "for example http://127.0.0.1:8000. Omit to run in-process."
        ),
    ),
]
AsOf = Annotated[str | None, typer.Option("--as-of", help="Aware ISO-8601 observation cutoff.")]
ValidAt = Annotated[str | None, typer.Option("--valid-at", help="Aware ISO-8601 validity time.")]
Path = Annotated[str | None, typer.Option("--path")]

#: A remote service can also be named for the process, so a shell or a CI job
#: that talks to one host does not have to repeat `--base-url` on every command.
#: The flag wins when both are present, because it is the more specific intent.
BASE_URL_ENV = "MIND_PALACE_BASE_URL"


def _base_url(base_url: str | None) -> str | None:
    """The remote service to use, or None to run in-process.

    Only a remote URL -- an explicit flag or an explicit environment variable --
    selects HTTP. A missing value is the local database, not a default server on
    port 8000: that default was the reason this command needed a server that no
    other core command needed.
    """
    return base_url or os.getenv(BASE_URL_ENV) or None


def _dispatch(method: str, corpus: str, query: str, base_url: str | None, **fields) -> None:
    # Reuse the CLI's one local/remote client builder: it applies the same local
    # Postgres recovery, lexical-mode handling and warm-runtime choice as
    # `recall` and `explain`, so these commands cannot drift from them.
    from cli.main import _client, _fail, _run
    from mindpalace_sdk import MemoryClientError

    try:
        client = _client(_base_url(base_url))
    except Exception as exc:  # noqa: BLE001 - every failure becomes guidance
        _fail(exc, False)
        return
    try:
        response = _run(getattr(client.memory, method), corpus=corpus, query=query, **fields)
        typer.echo(response.canonical_json())
    except MemoryClientError as exc:
        typer.echo(f"{exc.code}: {exc.message}", err=True)
        raise typer.Exit(code=1) from exc
    except ValueError as exc:
        typer.echo(str(exc), err=True)
        raise typer.Exit(code=2) from exc
    except Exception as exc:  # noqa: BLE001 - every failure becomes guidance
        _fail(exc, False)


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
    base_url: BaseURL = None,
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
    base_url: BaseURL = None,
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
    base_url: BaseURL = None,
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
    base_url: BaseURL = None,
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
    base_url: BaseURL = None,
) -> None:
    """Consume the durable corpus-scoped change feed."""
    from cli.main import _client, _fail, _run
    from mindpalace_sdk import MemoryClientError

    try:
        client = _client(_base_url(base_url))
    except Exception as exc:  # noqa: BLE001 - every failure becomes guidance
        _fail(exc, False)
        return
    try:
        response = _run(client.memory.feed, corpus=corpus, page_size=page_size, cursor=cursor)
        typer.echo(response.canonical_json())
    except MemoryClientError as exc:
        typer.echo(f"{exc.code}: {exc.message}", err=True)
        raise typer.Exit(code=1) from exc
    except Exception as exc:  # noqa: BLE001 - every failure becomes guidance
        _fail(exc, False)


@app.command()
def evidence(
    corpus: Corpus,
    claim_id: Annotated[str, typer.Option("--claim-id")],
    query: Query = "",
    as_of: AsOf = None,
    valid_at: ValidAt = None,
    path: Path = None,
    base_url: BaseURL = None,
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
    base_url: BaseURL = None,
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
    base_url: BaseURL = None,
) -> None:
    """Create a memory snapshot."""
    _dispatch("snapshot", corpus, query, base_url, as_of=as_of, valid_at=valid_at, path=path)


@app.command()
def replay(
    corpus: Corpus,
    snapshot_id: Annotated[str, typer.Option("--snapshot-id")],
    query: Query = "",
    base_url: BaseURL = None,
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
    base_url: BaseURL = None,
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
