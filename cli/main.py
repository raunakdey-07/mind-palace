# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 Raunak Dey

"""mindpalace CLI — Typer-based command-line interface."""

from __future__ import annotations

import json
import time
from typing import Annotated, Optional

import typer
import yaml

from cli.memory import app as memory_app

#: The five product commands, in the order a newcomer meets them.
help = "Mind Palace: memory that remembers what changed, what was true when, and why to believe it."

app = typer.Typer(name="mindpalace", help=help)
app.add_typer(memory_app)

# The local runtime. `mindpalace runtime status` for when a command feels slow, and
# `mindpalace runtime stop` to be sure nothing is running. Neither is needed to use
# Mind Palace: every command works with no runtime at all.
from cli.runtime import app as runtime_app  # noqa: E402

app.add_typer(runtime_app)

eval_app = typer.Typer(name="eval", help="Evaluation commands")
app.add_typer(eval_app)

# The default corpus. A newcomer should not have to understand multi-tenant
# corpus scoping to record their first memory; `--corpus` remains for scoping.
DEFAULT_CORPUS = "default"

State = Annotated[
    bool,
    typer.Option("--verbose", "-v", help="Show the underlying error as well as the guidance."),
]
BaseURL = Annotated[
    str | None,
    typer.Option(
        "--base-url",
        help=(
            "Use a running Mind Palace service instead of the local database, "
            "for example http://127.0.0.1:8000"
        ),
    ),
]


def _fail(exc: Exception, verbose: bool) -> None:
    """Print actionable failure text and exit non-zero."""
    from cli.errors import explain, render

    advice = explain(exc)
    typer.echo(render(advice, exc, verbose=verbose), err=True)
    raise typer.Exit(code=advice.status if advice.status > 1 else 1)


def _run(call, *args, **kwargs):
    """Run one service call, from a script or from inside an event loop.

    A command is usually the whole process, so this is a plain call. When the CLI
    is driven from an already-running loop -- an application embedding it, or a
    test invoking it in-process -- the work moves to a worker thread that owns its
    own event loop, instead of failing with "attached to a different loop".
    """
    import asyncio

    try:
        asyncio.get_running_loop()
    except RuntimeError:
        return call(*args, **kwargs)

    import threading

    from api.services.db import async_engine

    outcome: list = []

    def worker():
        try:
            outcome.append(call(*args, **kwargs))
        except BaseException as exc:  # noqa: BLE001 - re-raised on the caller's loop
            outcome.append(exc)
        finally:
            # asyncpg connections bind to the loop that opened them, and the engine
            # is a module-level singleton shared with the caller's loop. Disposing
            # it here closes the worker's connections rather than leaving them for
            # a loop that will never run again.
            try:
                asyncio.run(async_engine.dispose())
            except (RuntimeError, OSError):
                pass

    thread = threading.Thread(target=worker)
    thread.start()
    thread.join()
    result = outcome[0] if outcome else None
    if isinstance(result, BaseException):
        raise result
    return result


#: How many source documents `explain` traces. An answer normally has one; a bound
#: keeps the command's cost predictable when a question matches many. When the bound
#: bites, `explain` says so rather than quietly showing a partial history.
MAX_EXPLAIN_SOURCES = 5


# --------------------------------------------------------------------------
# The product surface: remember, recall, explain, history, receipt, verify.
# Each command is one call on the SDK, which is one call on the service, so the
# CLI cannot drift from the SDK, REST or MCP.
# --------------------------------------------------------------------------


def _corpus_or_fail(corpus: str) -> str:
    from api.services.corpora import validate_corpus_name

    try:
        return validate_corpus_name(corpus)
    except ValueError as exc:
        raise typer.BadParameter(str(exc)) from exc


@app.command()
def init(
    database_url: Annotated[
        str | None,
        typer.Option(
            "--database-url",
            help=(
                "PostgreSQL URL for this run, instead of $DATABASE_URL. A process "
                "that already imported the database module keeps its connection, so "
                "export DATABASE_URL before running a second command."
            ),
        ),
    ] = None,
) -> None:
    """Check that memory storage is ready, and report the next command.

    Verifies connectivity and that the schema is applied, creates the default
    corpus if needed, then prints one command to try. Safe to run repeatedly.
    """
    import asyncio
    import os

    if database_url:
        os.environ["DATABASE_URL"] = database_url

    from sqlalchemy import text
    from sqlalchemy.exc import SQLAlchemyError

    from api.services.db import session_scope

    async def check() -> list[bool]:
        async with session_scope() as db:
            row = (
                await db.execute(
                    text(
                        "SELECT to_regclass('corpora') IS NOT NULL, "
                        "to_regclass('memory_versions') IS NOT NULL"
                    )
                )
            ).one()
        return list(row)

    try:
        tables = _run(lambda: asyncio.run(check()))
    except (SQLAlchemyError, OSError, TimeoutError) as exc:
        from api.errors import storage_unavailable

        typer.echo(storage_unavailable(exc), err=True)
        raise typer.Exit(code=1) from exc

    if not all(tables):
        typer.echo(
            "Mind Palace is installed, but the memory schema is not applied yet.\n"
            "\nWhat to do:\n"
            "  python -m alembic -c migrations/alembic.ini upgrade head",
            err=True,
        )
        raise typer.Exit(code=1)

    async def ensure() -> str:
        from api.services.corpora import get_or_create_corpus

        async with session_scope() as db:
            row = await get_or_create_corpus(db, DEFAULT_CORPUS)
        return row["name"]

    _run(lambda: asyncio.run(ensure()))

    typer.echo("Mind Palace is ready.")
    typer.echo(f"  corpus: {DEFAULT_CORPUS}")
    _warm_runtime()
    typer.echo("\nRecord your first memory:\n")
    typer.echo('  mindpalace remember "Production uses PostgreSQL."')
    typer.echo("\nThen ask about it:\n")
    typer.echo('  mindpalace recall "What database does production use?"')


@app.command()
def remember(
    statement: Annotated[str | None, typer.Argument(help="The fact to remember.")] = None,
    file: Annotated[
        str | None, typer.Option("--file", "-f", help="Archive a Markdown file.")
    ] = None,
    key: Annotated[
        str | None,
        typer.Option(
            "--key",
            help=(
                "Stable name for this fact. Remembering the same key again is how "
                "memory records that the fact changed."
            ),
        ),
    ] = None,
    corpus: Annotated[str, typer.Option("--corpus", help="Corpus name.")] = DEFAULT_CORPUS,
    base_url: BaseURL = None,
    json_out: Annotated[
        bool, typer.Option("--json", help="Print the write result as JSON.")
    ] = False,
    verbose: State = False,
) -> None:
    """Record a fact. It becomes memory with evidence, not a search index entry.

    Remembering the same fact twice changes nothing. Remembering the same key with
    new text supersedes the old value, which is what `history` then shows.
    """
    from cli.render import render_remember

    corpus = _corpus_or_fail(corpus)
    # Through a runtime this costs about 0.2 s instead of 0.7 s, because the process
    # that owns the database pool is already running and this one does not have to
    # import it. `--file` is a local read and stays here whatever else is happening.
    client = _client(base_url)
    try:
        result = _run(client.memory.remember, statement, corpus=corpus, key=key, file=file)
    except Exception as exc:  # noqa: BLE001 - every failure becomes guidance
        _fail(exc, verbose)
        return
    if json_out:
        import json as json_module
        from dataclasses import asdict

        typer.echo(json_module.dumps(asdict(result), indent=2, sort_keys=True))
        return
    typer.echo(
        render_remember(
            {
                "event": result.event,
                "version_id": result.version_id,
                "path": result.path,
            },
            corpus=corpus,
        )
    )
    _warm_runtime()


@app.command()
def recall(
    question: Annotated[str, typer.Argument(help="What you want to know.")],
    corpus: Annotated[str, typer.Option("--corpus", help="Corpus name.")] = DEFAULT_CORPUS,
    as_of: Annotated[
        str | None,
        typer.Option(
            "--as-of",
            help='Answer as of an earlier instant, e.g. "2025-02-01T00:00:00Z".',
        ),
    ] = None,
    explain_result: Annotated[
        bool, typer.Option("--explain", help="Also show source, evidence and history.")
    ] = False,
    base_url: BaseURL = None,
    json_out: Annotated[
        bool, typer.Option("--json", help="Print the canonical response JSON.")
    ] = False,
    verbose: State = False,
) -> None:
    """Answer a question from memory, with its source and the time it was true.

    This is the shortest path to value: one command in, an answer out. It never
    invents an answer, so an unfamiliar question abstains rather than guesses.
    """
    client = _client(base_url, runtime=True)
    try:
        response = _run(
            client.memory.query,
            question,
            corpus=corpus,
            as_of=as_of,
            include_receipt=explain_result,
        )
    except Exception as exc:  # noqa: BLE001
        _fail(exc, verbose)
        return
    if json_out:
        typer.echo(response.canonical_json())
        return
    from cli.render import render_recall

    typer.echo(render_recall(response, explain=explain_result))


@app.command()
def explain(
    question: Annotated[str, typer.Argument(help="The question you want justified.")],
    corpus: Annotated[str, typer.Option("--corpus", help="Corpus name.")] = DEFAULT_CORPUS,
    as_of: Annotated[
        str | None, typer.Option("--as-of", help="Justify an answer from an earlier instant.")
    ] = None,
    base_url: BaseURL = None,
    json_out: Annotated[
        bool,
        typer.Option(
            "--json",
            help=(
                "Print the canonical response and its receipt as one JSON object, "
                "the same shape `mindpalace receipt` writes."
            ),
        ),
    ] = False,
    verbose: State = False,
) -> None:
    """Show why Mind Palace returned that answer.

    Reads the recorded provenance: the source document and version, the time the
    answer was true, the exact evidence characters, the supersession history, and
    whether a receipt is available to export. This is not a reasoning trace, and
    it does not claim the original source was factually correct.
    """
    client = _client(base_url, runtime=True)
    try:
        # Two reads, both existing contracts: the answer with its receipt, and the
        # change set behind the documents it came from. The CLI composes reads; it
        # does not invent semantics, and it does not mutate the response -- the
        # receipt's digest covers the response as the service returned it, so
        # attaching history to it afterwards would make the printed digest
        # describe something other than what was printed.
        #
        # History is selected by source document, not by the question: the archive
        # matches every word of a query, and a question is not a history selector.
        response = _run(
            client.memory.query, question, corpus=corpus, as_of=as_of, include_receipt=True
        )
        changes = _history_for(client, corpus, response)
    except Exception as exc:  # noqa: BLE001
        _fail(exc, verbose)
        return
    if json_out:
        typer.echo(_explain_json(response))
        return
    from cli.render import render_explain

    typer.echo(render_explain(response, changes=changes))


def _explain_json(response) -> str:
    """The canonical response and the receipt that covers it, in one object.

    `canonical_json()` omits the receipt on purpose -- the receipt's digest is
    computed from that JSON, so including it would be circular. But a script asking
    `explain --json` for the basis of an answer wants the digest, and dropping it
    would make the machine-readable form strictly less useful than the human one.

    The shape is the one `mindpalace receipt` already writes, so there is one bundle
    format rather than two.
    """
    import json as json_module

    payload = {"response": json_module.loads(response.canonical_json())}
    if response.receipt:
        payload["receipt"] = response.receipt
    return json_module.dumps(payload, indent=2, sort_keys=True, ensure_ascii=False)


@app.command("history")
def history(
    question: Annotated[str, typer.Argument(help="A key or question identifying the memory.")],
    corpus: Annotated[str, typer.Option("--corpus", help="Corpus name.")] = DEFAULT_CORPUS,
    base_url: BaseURL = None,
    json_out: Annotated[
        bool, typer.Option("--json", help="Print the canonical response JSON.")
    ] = False,
    verbose: State = False,
) -> None:
    """Show what this memory said before, and what replaced it.

    Memory is not only what is true now. This walks the real supersession chain in
    the archive; nothing here is reconstructed or illustrated.
    """
    client = _client(base_url, runtime=True)
    try:
        response = _run(client.memory.history, corpus=corpus, query=question)
    except Exception as exc:  # noqa: BLE001
        _fail(exc, verbose)
        return
    if json_out:
        typer.echo(response.canonical_json())
        return
    from cli.render import render_history

    typer.echo(render_history(response))


def _client(base_url: str | None, *, runtime: bool = True):
    """A client for local memory, or for a Mind Palace service someone else runs.

    Both go through the same SDK, so the CLI cannot drift from the SDK, the REST
    API or MCP: there is one client and one service underneath all four.

    ``runtime`` lets a local command use the warm background runtime when one is
    running, and fall back to this process when one is not. The user is never asked
    which they are getting: a one-shot `mindpalace recall` costs 6.5 s because it has
    to import the embedding stack, and that is 89% startup and 61 ms of memory work.
    Only `recall`-shaped commands ask for it, because a command that does not read
    memory has nothing to gain and a writer is better off owning its own connection.

    Under `MIND_PALACE_LEXICAL=1` the runtime is bypassed -- it exists to hold the
    model, and a model-free read does not want it -- and the mode is stated once on
    stderr, so it never contaminates an answer or a JSON document.
    """
    from mindpalace_sdk import MindPalace

    if base_url:
        return MindPalace(base_url=base_url)
    from api import runtime as runtime_module
    from api.services.retrieval_mode import NOTE, lexical_requested

    if lexical_requested():
        import sys

        print(NOTE, file=sys.stderr)
        return MindPalace(runtime=False)
    return MindPalace(runtime=runtime and runtime_module.enabled())


def _warm_runtime() -> None:
    """Start a background runtime if none is running. Never waits, never fails.

    Called from the commands that are already fast. By the time the user reaches for
    the next `recall`, the model the runtime is loading in the background is already
    resident -- which is the only way the 5 s import ever disappears from the journey,
    since no single command can avoid paying it and finish sooner.
    """
    from api import runtime as runtime_module

    if not runtime_module.enabled():
        return
    try:
        runtime_module.spawn()
    except Exception:  # noqa: BLE001 - a runtime is an optimisation, never a requirement
        pass


def _history_for(client, corpus: str, response) -> list:
    """The change set for the documents this answer came from, oldest first."""
    from datetime import datetime, timezone

    from api.models.memory import Change

    paths = sorted({claim.path for claim in response.current_memories if claim.path})
    changes = []
    for path in paths[:MAX_EXPLAIN_SOURCES]:
        history = _run(client.memory.history, corpus=corpus, path=path)
        changes.extend(change for change in history.changes if change not in changes)
    changes.sort(key=lambda change: change.observed_at)
    if len(paths) > MAX_EXPLAIN_SOURCES:
        # Say what was left out. A partial history that looks complete is worse
        # than a bounded one that admits its bound.
        changes.append(
            Change(
                version_id="",
                predecessor_id=None,
                event="TRUNCATED",
                path=(
                    f"{len(paths) - MAX_EXPLAIN_SOURCES} more source document(s) not "
                    "traced; narrow the question to see their history"
                ),
                observed_at=changes[-1].observed_at if changes else datetime.now(timezone.utc),
                memory_changed=False,
                relationship="LIFECYCLE",
                previous=[],
                current=[],
            )
        )
    return changes


@app.command()
def receipt(
    question: Annotated[str, typer.Argument(help="The question the receipt answers.")],
    out: Annotated[
        str | None, typer.Option("--out", "-o", help="Write the receipt here (default stdout).")
    ] = None,
    corpus: Annotated[str, typer.Option("--corpus", help="Corpus name.")] = DEFAULT_CORPUS,
    as_of: Annotated[
        str | None, typer.Option("--as-of", help="Reconstruct the answer from an earlier instant.")
    ] = None,
    base_url: BaseURL = None,
    verbose: State = False,
) -> None:
    """Export a Memory Receipt: what was returned, and on what basis.

    The receipt is the response's own canonical form plus a proof of it, so
    verifying it later needs no server, database or model. Write it to a file to
    hand to someone else.
    """
    client = _client(base_url, runtime=True)
    try:
        response = _run(
            client.memory.query, question, corpus=corpus, as_of=as_of, include_receipt=True
        )
    except Exception as exc:  # noqa: BLE001
        _fail(exc, verbose)
        return
    if not response.receipt:
        typer.echo(
            "Nothing matched that question, so there is no answer to attest to.\n"
            "\nA receipt records what was actually returned; with no answer there is "
            "nothing to record.",
            err=True,
        )
        raise typer.Exit(code=1)

    import json as json_module

    payload = json_module.dumps(
        {"response": response.model_dump(mode="json"), "receipt": response.receipt},
        indent=2,
        sort_keys=True,
        ensure_ascii=False,
    )
    if out:
        try:
            from pathlib import Path

            Path(out).write_text(payload + "\n", encoding="utf-8")
        except OSError as exc:
            typer.echo(f"Could not write {out}: {exc.strerror or exc}", err=True)
            raise typer.Exit(code=2) from exc
        typer.echo(f"Receipt written to {out}")
        typer.echo(f"Verify it anywhere with: mindpalace verify {out}")
        return
    typer.echo(payload)


@app.command()
def verify(
    receipt_file: Annotated[
        str, typer.Argument(help="A receipt, a proof, or a file written by `mindpalace receipt`.")
    ],
    pack: Annotated[
        str | None,
        typer.Option(
            "--pack", help="The authoritative Memory Pack, for a bare receipt or a proof."
        ),
    ] = None,
    trusted_digest: Annotated[
        str | None,
        typer.Option(
            "--trusted-digest",
            help=(
                "A digest you pinned out of band. Only with this can authenticity "
                "be established."
            ),
        ),
    ] = None,
    json_out: Annotated[
        bool, typer.Option("--json", help="Print a machine-readable verdict.")
    ] = False,
) -> None:
    """Verify a Memory Receipt offline: no server, database, model or network.

    Accepts every artifact `mindpalace-proof verify` accepts, and checks them with
    the same code, so there is one verification contract rather than two. A file
    written by `mindpalace receipt` needs no `--pack`.

    VERIFIED means the artifact still represents the state the receipt describes.
    It does not mean the original claim was factually true, and without a trust
    anchor you pinned yourself it does not mean the receipt came from anyone in
    particular. A REJECTED receipt exits non-zero.
    """
    if pack is not None:
        _verify_with_pack(receipt_file, pack, trusted_digest, json_out)
        return
    _verify_response_bundle(receipt_file, trusted_digest, json_out)


def _verify_with_pack(
    receipt_file: str, pack: str, trusted_digest: str | None, json_out: bool
) -> None:
    """Delegate to the dependency-free verifier, so one contract decides one verdict."""
    from memory_proof_cli import build_parser

    argv = ["verify", receipt_file, "--pack", pack]
    if json_out:
        argv.append("--json")
    args = build_parser().parse_args(argv)
    if trusted_digest:
        args.trusted_digest = trusted_digest
    code = args.func(args)
    raise typer.Exit(code=code)


def _verify_response_bundle(receipt_file: str, trusted_digest: str | None, json_out: bool) -> None:
    """Verify a file written by `mindpalace receipt`: the response it describes is in it."""
    import json as json_module
    from pathlib import Path

    from memory_receipt import ReceiptError, verify_response_receipt, verify_trust

    try:
        raw = Path(receipt_file).read_text(encoding="utf-8")
    except OSError as exc:
        from cli.errors import explain as explain_error
        from cli.errors import render as render_error

        typer.echo(
            render_error(
                explain_error(OSError(f"{receipt_file}: {exc.strerror or exc}")),
                exc,
            ),
            err=True,
        )
        raise typer.Exit(code=2) from exc
    try:
        payload = json_module.loads(raw)
    except json_module.JSONDecodeError as exc:
        typer.echo(f"{receipt_file} is not valid JSON: {exc}", err=True)
        typer.echo(
            "\nWhat to do:\n  A receipt is written by: "
            'mindpalace receipt "<question>" -o receipt.json'
        )
        raise typer.Exit(code=2) from exc

    bundle = payload.get("receipt") if isinstance(payload, dict) else None
    artifact = payload.get("response") if isinstance(payload, dict) else None
    if not isinstance(bundle, dict) or not isinstance(artifact, dict):
        typer.echo(
            f"{receipt_file} needs the artifact it describes.\n"
            "\nWhat to do:\n"
            f'  For a file from `mindpalace receipt`: mindpalace receipt "<question>"'
            f" -o {receipt_file}\n"
            f"  For a bare receipt or a proof: add --pack <memory-pack.json>",
            err=True,
        )
        raise typer.Exit(code=2)

    # Instrumented here rather than inside the verifier: `memory_receipt` is
    # standard-library-only by contract, so that anyone who installs the package
    # can verify a receipt without installing anything.
    from api.services import telemetry

    started = time.perf_counter()
    with telemetry.span(
        "memory.verify",
        receipts=len(bundle.get("receipts", []) or []),
        trusted_anchor=bool(trusted_digest),
    ) as active:
        try:
            result = verify_response_receipt(bundle, artifact)
        except ReceiptError as exc:
            typer.echo(f"That receipt cannot be read: {exc}", err=True)
            raise typer.Exit(code=2) from exc
    if active is not None:
        active.set_attribute("verified", result["verified"])
        active.set_attribute("duration_ms", (time.perf_counter() - started) * 1000)

    # A digest you pinned yourself is the only thing that can establish who wrote
    # this. Without one, authenticity stays explicitly unestablished rather than
    # being implied by a digest that anyone could recompute.
    trust = verify_trust({}, trusted_digest or "", bundle.get("memory_pack_digest", ""))
    authenticated = trust.get("authenticated", False)
    failed_anchor = bool(trusted_digest) and not authenticated
    if failed_anchor:
        result["verified"] = False

    if json_out:
        typer.echo(json_module.dumps({**result, "authenticity": trust}, indent=2, sort_keys=True))
    elif result["verified"]:
        typer.echo("VERIFIED\n")
        typer.echo("Integrity:      verified")
        typer.echo("Provenance:     verified")
        typer.echo("Temporal state: verified")
        typer.echo(f"Supersession:   verified ({result['receipts_checked']} receipt(s))")
        typer.echo(
            "Authenticity:   verified against your trust anchor"
            if authenticated
            else "Authenticity:   not established (supply --trusted-digest to check it)"
        )
        typer.echo(
            "\nThis means the receipt still matches the memory it describes. "
            "It does not mean the original claim was factually true."
        )
    else:
        typer.echo("REJECTED\n")
        typer.echo("Reason:")
        # One root cause at a time. If the artifact digest is already wrong, every
        # receipt over it will fail too, and listing them all just buries the cause.
        if failed_anchor:
            typer.echo("  the recorded digest does not match the trust anchor you supplied")
            typer.echo(
                "\nSomeone can recompute a matching digest; only a value you pinned "
                "out of band says who wrote this."
            )
        elif not result["memory_pack_digest"]:
            typer.echo("  the artifact no longer matches the digest the receipt recorded")
            typer.echo(
                "\nThe receipt describes memory that has since changed, or the artifact "
                "has been altered.\nRe-issue it with: "
                'mindpalace receipt "<question>" -o receipt.json'
            )
        elif not result["query_binding"]:
            typer.echo("  the receipt answers a different question than the artifact")
        else:
            for failure in result["failures"]:
                typer.echo(
                    f"  receipt {str(failure.get('receipt_id', ''))[:12]} no longer matches "
                    f"the artifact"
                )

    raise typer.Exit(code=0 if result["verified"] else 1)


@app.command()
def corpora() -> None:
    """List the corpora you can read and write."""
    import asyncio

    from api.services.corpora import list_corpora
    from api.services.db import session_scope

    async def run():
        async with session_scope() as db:
            return await list_corpora(db)

    try:
        rows = _run(lambda: asyncio.run(run()))
    except Exception as exc:  # noqa: BLE001
        _fail(exc, False)
        return
    if not rows:
        typer.echo("No corpora yet. Record a memory to create one:\n")
        typer.echo('  mindpalace remember "Production uses PostgreSQL."')
        return
    for row in rows:
        typer.echo(row["name"])


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
