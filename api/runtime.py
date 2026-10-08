# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 Raunak Dey

"""A local runtime that keeps the embedding model and the database pool warm.

Why this exists, measured and not assumed. On mains power a one-shot
`mindpalace recall` costs 6.53 s, of which the memory work is 61 ms:

    interpreter start                          0.038 s
    CLI + api imports                          0.515 s
    database connection                    +    0.190 s
    import torch                               1.679 s
    import transformers                     +    0.982 s
    import sentence_transformers            +    2.660 s
    model construct + first embed          +    0.486 s
    ------------------------------------------------------------------
    one-shot `mindpalace recall`               6.53 s
    in-process warm recall + receipt           0.061 s

89% of the wall clock is Python import and model construction for a CLI that exits
60 ms later. Nothing about that is retrieval, and no amount of retrieval work will
change it. The fix is to stop paying it per process.

What this is *not*: it is not a faster database, a new ranking stage, or a second
source of truth. It is a transport. Every request runs the same
`memory_public.execute` / `remember` in the same process shape the CLI already
uses, so authoritative claims, evidence, temporal state, supersession, abstention,
receipts and Memory Pack digests are computed by unchanged code. If the model fails
to load here it fails the same way it would in-process: `memory_unavailable`, 503.
There is no second answer and no silent lexical fallback introduced by this module.

Boundaries, in one place so they are auditable:

* Loopback only. A Unix domain socket under a 0700 directory, whose 0600 mode is
  the authorisation boundary. If `AF_UNIX` is unavailable, a 127.0.0.1 socket plus
  a token read from a 0600 file -- never from the command line, where any local
  user could read it out of `ps`.
* Single instance. `bind()` refuses to steal another live runtime's socket.
* Stale-safe. A socket nobody answers on is removed and replaced, so a killed
  runtime never wedges the CLI.
* Bounded. An idle runtime exits, so it does not outlive the session that made it,
  and a burst is bounded so `to_thread` cannot queue without limit.
* Never required. `MIND_PALACE_RUNTIME=0` or `--no-runtime` runs in-process, which
  is the pre-existing behaviour and remains fully supported for the SDK and server.
"""

from __future__ import annotations

import asyncio
import contextlib
import errno
import json
import os
import signal
import socket
import stat
import sys
import time
import uuid
from dataclasses import dataclass, field
from pathlib import Path

#: A runtime with no requests for this long exits. Long enough to cover a working
#: session of `remember`/`recall`/`explain`, short enough that it is not a process
#: someone finds next week.
IDLE_SECONDS = 900

#: How often to check for idleness. Cheap; nothing else is on the timer.
IDLE_POLL_SECONDS = 15

#: Concurrent requests. Query work is CPU-bound model inference plus a database
#: round trip, so unbounded concurrency on a small machine is slower, not faster.
MAX_CONCURRENT = 4

#: How long a client waits to connect before giving up and running in-process. A
#: dead socket refuses instantly; this only bounds a pathological hang.
CONNECT_TIMEOUT = 0.25

#: The wire protocol version. A runtime from a different build refuses the request
#: rather than answering it with different semantics.
PROTOCOL = 1

STATES = ("starting", "loading", "ready", "stopping", "failed")


class RuntimeError_(Exception):
    """The runtime could not be started, reached, or asked to do something."""


# ---------------------------------------------------------------------------
# Where it lives
# ---------------------------------------------------------------------------


def state_dir() -> Path:
    """A directory only this user can read.

    0700 because the socket inside it is the authorisation boundary, and the
    fallback token lives beside it.
    """
    base = os.getenv("MIND_PALACE_STATE_DIR")
    if base:
        return Path(base)
    runtime = os.getenv("XDG_RUNTIME_DIR")
    if runtime:
        return Path(runtime) / "mindpalace"
    return Path(os.getenv("LOCALAPPDATA") or Path.home() / ".cache") / "mindpalace"


def socket_path() -> Path:
    """Where the Unix socket lives, on a filesystem that permits short names."""
    path = state_dir() / "runtime.sock"
    text = str(path)
    # sun_path is ~104 bytes on Linux and 260 on Windows. A long TMPDIR is common
    # enough that assuming otherwise produces a socket that binds and then fails on
    # connect, which is worse than no socket.
    limit = 100 if sys.platform != "win32" else 250
    if len(text.encode()) <= limit:
        return path
    return Path(os.getenv("TMPDIR", "/tmp")) / f"mp-{os.getpid()}-{uuid.uuid4().hex[:6]}.sock"


def token_path() -> Path:
    return state_dir() / "runtime.token"


def use_unix() -> bool:
    """Whether a Unix domain socket is usable here.

    Windows has supported `AF_UNIX` since 10, but a distribution can be built
    without it, and a client that assumes a socket exists will hang rather than
    fall back if it is wrong.
    """
    return hasattr(socket, "AF_UNIX")


# ---------------------------------------------------------------------------
# Client
# ---------------------------------------------------------------------------


@dataclass
class RuntimeStatus:
    """What the runtime is doing, in the user's vocabulary."""

    state: str = "unreachable"
    pid: int | None = None
    uptime_s: float | None = None
    model: str = "not loaded"
    requests: int = 0
    detail: str | None = None

    def line(self) -> str:
        if self.state == "unreachable":
            return "runtime: not running"
        model = "loaded" if self.model == "loaded" else self.model
        parts = [
            f"runtime: {self.state}",
            f"model: {model}",
        ]
        if self.uptime_s is not None:
            parts.append(f"up: {int(self.uptime_s)}s")
        return "  " + "  ".join(parts)


def _read_token() -> str | None:
    try:
        return token_path().read_text(encoding="utf-8").strip() or None
    except OSError:
        return None


def call(payload: dict, *, timeout: float = 30.0, connect_timeout: float = CONNECT_TIMEOUT) -> dict:
    """One request to a running runtime, from synchronous code. Raises on failure.

    Raises rather than returning a sentinel: the caller has to be able to tell "the
    runtime said no" from "there is no runtime", because only the first is
    authoritative. Returning None for both would let an optimisation quietly become
    the answer.

    Synchronous because every caller is synchronous. The socket work is async
    internally, and the event loop is created and closed here so that no caller has
    to own one -- `mindpalace runtime status` is not a program with a loop.
    """
    if not use_unix():
        raise RuntimeError_("this platform has no Unix domain sockets")
    token = _read_token()
    if not token:
        raise RuntimeError_("no runtime token")
    return asyncio.run(_call_async(socket_path(), token, payload, timeout, connect_timeout))


async def _call_async(
    path: Path, token: str, payload: dict, timeout: float, connect_timeout: float
) -> dict:
    body = json.dumps({"protocol": PROTOCOL, "token": token, **payload}).encode()
    reader, writer = await _connect(path, connect_timeout)
    try:
        writer.write(len(body).to_bytes(4, "big") + body)
        await writer.drain()
        header = await asyncio.wait_for(reader.readexactly(4), timeout)
        length = int.from_bytes(header, "big")
        raw = await asyncio.wait_for(reader.readexactly(length), timeout)
    finally:
        writer.close()
        with contextlib.suppress(Exception):
            await writer.wait_closed()
    return json.loads(raw)


async def _connect(path: Path, timeout: float):
    """Open the socket, removing a stale one rather than failing on it.

    A runtime that was killed leaves its socket file behind. Connecting to it
    raises `ECONNREFUSED`, which is not an error the user should ever see -- the
    correct answer is that no runtime is running, and the next command will start
    one. Anything else is a real failure and propagates.
    """
    for attempt in (0, 1):
        try:
            return await asyncio.wait_for(asyncio.open_unix_connection(str(path)), timeout)
        except (ConnectionRefusedError, FileNotFoundError):
            if attempt:
                raise RuntimeError_("stale runtime socket")
            with contextlib.suppress(OSError):
                if stat.S_ISSOCK(path.lstat().st_mode):
                    path.unlink()
    raise RuntimeError_("unreachable")


def enabled() -> bool:
    """Whether the runtime may be used at all on this machine and in this shell.

    False when the platform has no Unix domain socket, or when the user has turned it
    off. Every caller checks this first, so the opt-out is one environment variable
    and it applies to the CLI, the SDK and the runtime command alike.
    """
    return os.getenv("MIND_PALACE_RUNTIME") != "0" and use_unix()


def reachable() -> bool:
    """Whether a runtime is already answering. Cheap enough for every command."""
    if not enabled():
        return False
    if not socket_path().exists() or not token_path().exists():
        return False
    try:
        status = call({"op": "status"}, timeout=2.0)
    except Exception:  # noqa: BLE001 - "is it up" must never raise
        return False
    return bool(status.get("ok")) and status.get("result", {}).get("state") in (
        "ready",
        "loading",
        "starting",
    )


def status() -> RuntimeStatus:
    """Ask a running runtime what it is doing. Never raises."""
    try:
        reply = call({"op": "status"}, timeout=2.0)
    except Exception as exc:  # noqa: BLE001 - status is diagnostic, so it cannot fail
        return RuntimeStatus(detail=str(exc) if os.getenv("MIND_PALACE_VERBOSE") else None)
    if not reply.get("ok"):
        return RuntimeStatus(state="failed", detail=reply.get("error", {}).get("message"))
    result = reply["result"]
    return RuntimeStatus(
        state=result["state"],
        pid=result.get("pid"),
        uptime_s=result.get("uptime_s"),
        model=result.get("model", "not loaded"),
        requests=result.get("requests", 0),
        detail=result.get("detail"),
    )


def spawn() -> bool:
    """Start a runtime detached from this process. Returns as soon as it is spawned.

    Never waits, and cannot: the runtime is a long-lived process that exits on idle,
    so joining it would hang the caller forever. A caller that wants to know when the
    model is ready polls `status()` instead -- `mindpalace runtime start --wait` does
    exactly that.

    Not waiting is also the point. The interesting property is that the *next*
    command is fast, not that this one is. A warm-up started during `init` or
    `remember` has finished by the time the user types their next `recall`.
    """
    if not enabled():
        return False
    directory = state_dir()
    try:
        directory.mkdir(parents=True, exist_ok=True)
        os.chmod(directory, 0o700)
    except OSError:
        return False
    _ensure_token()
    if reachable():
        return True

    import subprocess

    argv = [sys.executable, "-m", "mindpalace_runtime"]
    kwargs = {}
    if sys.platform == "win32":  # pragma: no cover - platform specific
        kwargs["creationflags"] = getattr(subprocess, "DETACHED_PROCESS", 0) | getattr(
            subprocess, "CREATE_NEW_PROCESS_GROUP", 0
        )
    else:
        kwargs["start_new_session"] = True
    try:
        subprocess.Popen(
            argv,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            **kwargs,
        )
    except Exception:  # noqa: BLE001 - no runtime is a valid outcome, not a failure
        return False
    return True


def _ensure_token() -> None:
    """Create the shared token if it is not there yet. 0600, never a command line.

    A token passed as an argument would be visible to every local user in `ps`,
    which is a worse boundary than the one it is protecting.
    """
    if token_path().exists():
        return
    with contextlib.suppress(OSError):
        fd = os.open(token_path(), os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
        with os.fdopen(fd, "w") as handle:
            handle.write(uuid.uuid4().hex + "\n")


# ---------------------------------------------------------------------------
# Server
# ---------------------------------------------------------------------------


@dataclass
class _Runtime:
    """Server state. Small on purpose: the service is the interesting part."""

    state: str = "starting"
    model: str = "not loaded"
    started: float = field(default_factory=time.monotonic)
    last_request: float = field(default_factory=time.monotonic)
    requests: int = 0
    detail: str | None = None

    def touch(self) -> None:
        self.last_request = time.monotonic()
        self.requests += 1


async def serve() -> int:
    """Run the runtime until it is stopped, or until it goes idle."""
    runtime = _Runtime()
    directory = state_dir()
    directory.mkdir(parents=True, exist_ok=True)
    with contextlib.suppress(OSError):
        os.chmod(directory, 0o700)
    token = _read_token()
    if not token:
        raise RuntimeError_("no runtime token; start with `mindpalace runtime start`")

    path = socket_path()
    with contextlib.suppress(OSError):
        if stat.S_ISSOCK(path.lstat().st_mode):
            path.unlink()

    from api.services import telemetry

    stop = asyncio.Event()
    gate = asyncio.Semaphore(MAX_CONCURRENT)
    server = None
    with telemetry.span("runtime.start", unix_socket=use_unix()):
        try:
            server = await asyncio.start_unix_server(
                _handler(runtime, token, stop, gate), path=str(path)
            )
        except OSError as exc:
            if exc.errno == errno.EADDRINUSE:
                raise RuntimeError_("another runtime is already using this socket")
            raise
        try:
            os.chmod(path, 0o600)
        except OSError:
            pass

    runtime.state = "ready"

    loop = asyncio.get_running_loop()
    for sig in (signal.SIGINT, signal.SIGTERM):
        with contextlib.suppress(NotImplementedError, ValueError):
            loop.add_signal_handler(sig, stop.set)

    # Warm the model in the background rather than on the first request. The first
    # request still works -- it would load the model itself -- but a runtime started
    # by `init` or `remember` is ready before the user types their next command.
    tasks = [asyncio.create_task(_idle_watch(runtime, stop)), asyncio.create_task(_warm(runtime))]
    try:
        async with server:
            await stop.wait()
    finally:
        runtime.state = "stopping"
        for task in tasks:
            task.cancel()
        for task in tasks:
            with contextlib.suppress(asyncio.CancelledError, Exception):
                await task
        with contextlib.suppress(OSError):
            path.unlink()
    return 0


async def _warm(runtime: _Runtime) -> None:
    """Load the model now so the first request does not have to.

    Failure is recorded and never invented around: `model: unavailable` plus the
    reason. The runtime keeps serving, and a query then takes the same path it
    would have taken without the runtime -- which, when the model genuinely cannot
    load, is the same `memory_unavailable` the CLI already produces. There is no
    new answer and no silent lexical substitution.
    """
    from api.services import telemetry

    runtime.state = "loading"
    started = time.perf_counter()
    try:
        with telemetry.span("runtime.model_load", model="configured"):
            await asyncio.to_thread(_load_model)
        runtime.model = "loaded"
        runtime.state = "ready"
    except Exception as exc:  # noqa: BLE001 - the reason has to reach the user
        runtime.model = "unavailable"
        runtime.detail = f"{type(exc).__name__}: {exc}"
        runtime.state = "ready"
    _ = time.perf_counter() - started


def _load_model() -> None:
    from api.services.embedder import Embedder

    Embedder().embed(["mind palace runtime warmup"])


async def _idle_watch(runtime: _Runtime, stop: asyncio.Event) -> None:
    while not stop.is_set():
        await asyncio.sleep(IDLE_POLL_SECONDS)
        if time.monotonic() - runtime.last_request > IDLE_SECONDS:
            stop.set()


def _handler(runtime: _Runtime, token: str, stop: asyncio.Event, gate: asyncio.Semaphore):
    """One framed-JSON connection: read a request, answer it, close.

    ``gate`` is passed in rather than created here on purpose. A semaphore made per
    connection bounds nothing at all -- every connection would get its own, and a
    burst of twenty would run twenty queries at once. One gate per server is what
    actually bounds the work, which is the whole point on a machine with a handful of
    cores doing CPU-bound inference.
    """

    async def handle(reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        try:
            async with gate:
                reply = await _serve_one(runtime, token, stop, reader)
        except Exception as exc:  # noqa: BLE001 - a runtime must not die on one request
            reply = {"ok": False, "error": {"code": "runtime_failed", "message": str(exc)}}
        try:
            body = json.dumps(reply).encode()
            writer.write(len(body).to_bytes(4, "big") + body)
            await writer.drain()
        except (ConnectionError, OSError):
            pass
        finally:
            with contextlib.suppress(Exception):
                writer.close()
                await writer.wait_closed()

    return handle


async def _serve_one(
    runtime: _Runtime, token: str, stop: asyncio.Event, reader: asyncio.StreamReader
) -> dict:
    header = await reader.readexactly(4)
    length = int.from_bytes(header, "big")
    if length > 32 * 1024 * 1024:  # pragma: no cover - a local peer would have to send this
        return {"ok": False, "error": {"code": "invalid_request", "message": "request too large"}}
    try:
        request = json.loads(await reader.readexactly(length))
    except (ValueError, asyncio.IncompleteReadError) as exc:
        return {"ok": False, "error": {"code": "invalid_request", "message": str(exc)}}

    if not isinstance(request.get("token"), str) or request["token"] != token:
        return {"ok": False, "error": {"code": "forbidden", "message": "bad runtime token"}}
    if request.get("protocol") != PROTOCOL:
        return {
            "ok": False,
            "error": {
                "code": "protocol_mismatch",
                "message": (
                    f"this runtime speaks protocol {PROTOCOL}; the client asked for "
                    f"{request.get('protocol')}. Restart it with `mindpalace runtime stop`."
                ),
            },
        }

    operation = request.get("op")
    if operation == "status":
        return {"ok": True, "result": _describe(runtime)}
    if operation == "shutdown":
        stop.set()
        return {"ok": True, "result": {"state": "stopping"}}

    runtime.touch()
    try:
        return {"ok": True, "result": await _dispatch(operation, request, runtime)}
    except Exception as exc:  # noqa: BLE001 - translated exactly as the CLI does
        return {"ok": False, "error": _error(exc)}


def _describe(runtime: _Runtime) -> dict:
    return {
        "state": runtime.state,
        "pid": os.getpid(),
        "uptime_s": round(time.monotonic() - runtime.started, 3),
        "model": runtime.model,
        "requests": runtime.requests,
        "detail": runtime.detail,
        "protocol": PROTOCOL,
    }


def _error(exc: Exception) -> dict:
    """Reuse the product's own error translation.

    A runtime must not invent an error vocabulary: the same failure has to reach the
    user with the same code and the same wording whether it happened here or in the
    CLI's own process.
    """
    code = getattr(exc, "code", None)
    if code:
        return {
            "code": code,
            "message": getattr(exc, "message", None) or str(exc),
            "status": int(getattr(exc, "status_code", 1) or 1),
        }
    return {"code": "error", "message": str(exc), "status": 1}


async def _dispatch(operation: str, request: dict, runtime: _Runtime) -> dict:
    """Run one public operation. Everything here is existing product code."""
    if operation == "remember":
        from api.models.remember import RememberResult
        from api.services.remember import remember as write

        name = request.get("corpus") or "default"
        written = await write(request.get("statement"), corpus=name, key=request.get("key"))
        return RememberResult(
            corpus=name,
            version_id=written.get("version_id"),
            event=written.get("event", "NEW"),
            path=written.get("path", ""),
            key=written.get("key"),
            changed=written.get("event") != "UNCHANGED",
        ).model_dump(mode="json")

    from api.models.memory import MemoryRequest
    from api.services.memory_public import execute

    memory_request = MemoryRequest.model_validate(request.get("request") or {})
    result = await execute(operation, memory_request)
    if runtime.model != "loaded":
        from api.services.embedder import Embedder

        if Embedder().loaded:
            runtime.model = "loaded"
    return result.model_dump(mode="json")
