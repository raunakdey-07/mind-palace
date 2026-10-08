# SPDX-License-Identifier: Apache-2.0
"""`mindpalace runtime` — control and inspect the local runtime.

The runtime is an implementation detail. `mindpalace recall` uses one when it is
there and does not care when it is not, so these commands exist for the two moments
a person actually needs them: "is it running, and why is this slow?" and "stop
using it".
"""

from __future__ import annotations

from typing import Annotated

import typer

app = typer.Typer(
    name="runtime",
    help="The local memory runtime that keeps the model warm. Optional.",
    no_args_is_help=True,
)


@app.command("start")
def start(
    wait: Annotated[bool, typer.Option("--wait", help="Wait until the model is ready.")] = False,
) -> None:
    """Start a background runtime, or report that one is already running.

    Safe to run repeatedly. The usual way to use Mind Palace never runs this: the
    first command you run starts a runtime for you.
    """
    from api import runtime

    current = runtime.status()
    if current.state in {"ready", "loading", "starting"}:
        typer.echo(current.line())
        typer.echo("  (already running)")
        return
    if not runtime.spawn():
        typer.echo("Could not start a runtime. Commands still work; they will be slower.")
        raise typer.Exit(code=1)
    typer.echo("Runtime starting. It loads the model in the background.")
    if wait:
        _await_ready()
    else:
        typer.echo("  status: mindpalace runtime status")


@app.command("stop")
def stop() -> None:
    """Stop the runtime. Any command still works; it will just be slower."""
    from api import runtime

    current = runtime.status()
    if current.state == "unreachable":
        typer.echo("Runtime is not running.")
        return
    try:
        runtime.call({"op": "shutdown"}, timeout=5.0)
    except Exception as exc:  # noqa: BLE001 - reported as text, never a traceback
        typer.echo(f"Could not reach the runtime to stop it cleanly: {exc}")
        typer.echo(f"  stop it with: kill {current.pid}")
        raise typer.Exit(code=1)
    typer.echo("Runtime stopping. It exits within a second of finishing any request.")


@app.command("status")
def status(
    json_out: Annotated[
        bool, typer.Option("--json", help="Print machine-readable output.")
    ] = False,
) -> None:
    """Whether a runtime is running, and whether its model is loaded."""
    from api import runtime

    current = runtime.status()
    if json_out:
        import json

        typer.echo(
            json.dumps(
                {
                    "state": current.state,
                    "pid": current.pid,
                    "uptime_s": current.uptime_s,
                    "model": current.model,
                    "requests": current.requests,
                    "detail": current.detail,
                    "protocol": runtime.PROTOCOL,
                    "socket": str(runtime.socket_path()),
                    "enabled": runtime.enabled(),
                },
                indent=2,
                sort_keys=True,
            )
        )
        return
    if current.state == "unreachable":
        typer.echo("Runtime is not running.")
        typer.echo("  commands still work; each one starts its own runtime if you want a warm one")
        typer.echo("  start it with: mindpalace runtime start")
        return
    typer.echo(current.line())
    if current.detail:
        typer.echo(f"  reason: {current.detail}")
    if current.state != "ready":
        typer.echo("  the model is still loading; the first query would pay for it")


def _await_ready(timeout: float = 120.0) -> None:
    """Poll until the runtime is ready, then report. Used only by `start --wait`.

    A freshly spawned runtime needs a moment to bind its socket and then to import
    the model, so "not answering yet" is the expected first few seconds and not a
    failure. Only concluding it died -- by polling on without ever seeing it answer --
    is a failure.
    """
    import time

    from api import runtime

    deadline = time.monotonic() + timeout
    grace = time.monotonic() + 10.0
    while time.monotonic() < deadline:
        current = runtime.status()
        if current.state == "ready":
            typer.echo(current.line())
            return
        if current.state == "failed":
            typer.echo(f"Runtime failed: {current.detail or 'unknown reason'}")
            raise typer.Exit(code=1)
        if current.state == "unreachable" and time.monotonic() > grace:
            typer.echo("Runtime did not start. Check with: mindpalace runtime status")
            raise typer.Exit(code=1)
        time.sleep(0.25)
    typer.echo("Still loading. Check with: mindpalace runtime status")
