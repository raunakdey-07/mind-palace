# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 Raunak Dey

"""Local PostgreSQL (podman/docker) recovery helpers for the CLI."""

from __future__ import annotations

import json
import os
import shutil
import socket
import subprocess
import time
from urllib.parse import urlparse

#: How long to wait for a (re)started container to accept TCP connections.
READY_TIMEOUT_SECONDS = 5.0

#: The container name the compose file / init creates.
DEFAULT_CONTAINER_NAME = "mindpalace_postgresql_1"

#: The image used when provisioning a fresh local container.
DEFAULT_IMAGE = "ankane/pgvector"

#: The port the local development database lives on.
#:
#: Provisioning is limited to this one. Creating a container on whatever port
#: `DATABASE_URL` happens to name is a surprise with a lasting consequence --
#: a container nobody asked for, on a port that was chosen for something else
#: -- and it is not what the local setup documents. A database on any other
#: port is one the operator runs, and the CLI leaves it alone.
DEFAULT_POSTGRES_PORT = 5432


def database_url() -> str:
    return os.getenv("DATABASE_URL", "postgresql://mpadmin:secret@localhost:5432/mindpalace")


def db_host_port(url: str | None = None) -> tuple[str, int]:
    parsed = urlparse(url or database_url())
    host = parsed.hostname or "localhost"
    port = parsed.port or 5432
    return host, port


def db_ready(host: str, port: int, timeout: float = 2.0) -> bool:
    """TCP plus an actual SELECT 1, so we don't race Postgres startup.

    Postgres accepts TCP connections before it will answer a query, so a TCP
    check alone reports a container as ready while it is still replaying its
    WAL. The first command after an auto-start would then fail with "the
    database system is starting up".

    The probe uses its own engine rather than the application's shared one:
    this runs before any client exists, and an engine bound to a different
    event loop is a class of failure this code should not be able to cause.
    """
    if not tcp_open(host, port, timeout=0.5):
        return False
    try:
        from sqlalchemy import text
        from sqlalchemy.ext.asyncio import create_async_engine
        from sqlalchemy.pool import NullPool

        url = database_url()
        parsed = urlparse(url)
        # Probe the host and port asked for, not whatever the URL happens to
        # name: the caller may have been told to look somewhere else.
        userinfo = ""
        if "@" in parsed.netloc:
            userinfo = parsed.netloc.rsplit("@", 1)[0] + "@"
        host_for_url = f"[{host}]" if ":" in host else host
        probe_url = parsed._replace(netloc=f"{userinfo}{host_for_url}:{port}").geturl()
        engine = create_async_engine(
            probe_url.replace("postgresql://", "postgresql+asyncpg://"),
            poolclass=NullPool,
        )

        async def _ping():
            async with engine.connect() as conn:
                await conn.execute(text("SELECT 1"))

        try:
            _run_blocking(_ping(), timeout=timeout)
        finally:
            _dispose(engine)
        return True
    except Exception:  # noqa: BLE001 - readiness is a question, not a verdict
        return False


def _run_blocking(coro, timeout: float) -> None:
    """Run a coroutine to completion, from either kind of caller.

    `asyncio.run` refuses to nest, so a command driven from inside a running
    loop -- an embedding application, an in-process test -- has to do the work
    on a worker thread with a loop of its own. Getting that wrong would make
    the probe always report "not ready", and a false negative here is what
    makes the CLI restart a database that is already running.
    """
    import asyncio

    try:
        asyncio.get_running_loop()
    except RuntimeError:
        asyncio.run(asyncio.wait_for(coro, timeout=timeout))
        return

    import threading

    outcome: list = []

    def worker():
        try:
            asyncio.run(asyncio.wait_for(coro, timeout=timeout))
        except BaseException as exc:  # noqa: BLE001 - re-raised on the caller's thread
            outcome.append(exc)

    thread = threading.Thread(target=worker)
    thread.start()
    thread.join()
    if outcome:
        raise outcome[0]


def _dispose(engine) -> None:
    """Dispose an engine, wherever it was created."""

    async def _close():
        await engine.dispose()

    try:
        _run_blocking(_close(), timeout=5.0)
    except Exception:  # noqa: BLE001 - disposal is best-effort
        pass


def is_local_host(host: str) -> bool:
    return host in {"localhost", "127.0.0.1", "::1", "0.0.0.0"}


def tcp_open(host: str, port: int, timeout: float = 1.0) -> bool:
    try:
        with socket.create_connection((host, port), timeout=timeout):
            return True
    except OSError:
        return False


def _container_clis() -> list[str]:
    found = []
    for name in ("podman", "docker"):
        if shutil.which(name):
            found.append(name)
    # Prefer podman on Fedora-style setups, but docker is fine.
    return found


def _run_cli(cli: str, args: list[str], timeout: float = 30.0) -> subprocess.CompletedProcess:
    return subprocess.run(
        [cli, *args],
        capture_output=True,
        text=True,
        timeout=timeout,
    )


def _normalize_container(raw: dict, cli: str) -> dict:
    names = raw.get("Names") or raw.get("Name") or raw.get("names") or raw.get("name")
    if isinstance(names, list):
        names = names[0] if names else ""
    state = raw.get("State") or raw.get("state") or ""
    status = raw.get("Status") or raw.get("status") or ""
    image = raw.get("Image") or raw.get("image") or ""
    ports = raw.get("Ports") or raw.get("ports") or raw.get("ExposedPorts") or []
    return {
        "cli": cli,
        "name": str(names).strip().strip("[]'\""),
        "state": str(state).strip().lower(),
        "status": str(status).strip(),
        "image": str(image).strip().lower(),
        "ports": ports,
        "raw": raw,
    }


def _publishes_port(container: dict, port: int) -> bool:
    """Does this container publish ``port`` on the host?

    The *host* port is what matters, because that is the port a client
    connects to. Matched exactly rather than by substring: "5432" is a
    substring of "15432", and a container publishing 15432 is not the one
    publishing 5432. Starting it because its port number happens to contain
    the right digits would be a worse failure than not starting it at all.
    """
    ports = container.get("ports")
    if isinstance(ports, list):
        for entry in ports:
            if isinstance(entry, dict):
                host_port = entry.get("host_port", entry.get("HostPort", entry.get("hostPort")))
                if host_port is None:
                    continue
                if int(host_port) == port:
                    return True
                # A published range starts at `host_port` and spans `range`
                # ports, so 5432 is inside a range that starts at 5430.
                span = entry.get("range")
                if span and int(host_port) <= port < int(host_port) + int(span):
                    return True
            elif isinstance(entry, int) and entry == port:
                return True
            elif isinstance(entry, str) and _host_port_in(entry, port):
                return True
    if isinstance(ports, dict):
        for key, value in ports.items():
            if str(key) == str(port):
                return True
            if _host_port_in(str(value), port):
                return True
    if isinstance(ports, str):
        return _host_port_in(ports, port)
    return False


def _host_port_in(rendered: str, port: int) -> bool:
    """Is ``port`` the host port in a rendered ports field?

    The rendered form is "0.0.0.0:5432->5432/tcp": the host port is the one
    before the arrow, and the one after it is the container's own port. A
    client connects to the host port, so the container port is not a match --
    a container publishing 15432 is not the one publishing 5432.
    """
    import re

    for chunk in rendered.split(","):
        host_part = chunk.split("->")[0]
        numbers = re.findall(r"\d+", host_part)
        if numbers and int(numbers[-1]) == port:
            return True
    return False


def _is_postgres(container: dict) -> bool:
    blob = " ".join(
        str(x)
        for x in (
            container.get("name", ""),
            container.get("image", ""),
            container.get("status", ""),
        )
    ).lower()
    return ("postgres" in blob) or ("pgvector" in blob) or ("mindpalace" in blob)


def _is_stopped(container: dict) -> bool:
    state = (container.get("state") or "").lower()
    status = (container.get("status") or "").lower()
    if state in {"exited", "created", "dead", "stopped", "paused"}:
        return True
    if status.startswith("exited") or status.startswith("created") or status.startswith("dead"):
        return True
    return False


def list_containers() -> list[dict]:
    """Return all containers visible to any available container CLI."""
    result = []
    for cli in _container_clis():
        # Prefer JSON output.
        try:
            proc = _run_cli(cli, ["ps", "-a", "--format", "json"])
            text = (proc.stdout or "").strip()
            if text:
                parsed = None
                try:
                    parsed = json.loads(text)
                except json.JSONDecodeError:
                    # Some CLIs emit one JSON object per line.
                    objs = []
                    for line in text.splitlines():
                        line = line.strip()
                        if not line:
                            continue
                        try:
                            objs.append(json.loads(line))
                        except json.JSONDecodeError:
                            objs = None
                            break
                    if objs is not None:
                        parsed = objs
                if isinstance(parsed, list):
                    for raw in parsed:
                        if isinstance(raw, dict):
                            result.append(_normalize_container(raw, cli))
                    if result:
                        continue
        except (OSError, subprocess.SubprocessError, ValueError):
            pass

        # Fallback: tab-separated format.
        try:
            proc = _run_cli(
                cli,
                [
                    "ps",
                    "-a",
                    "--format",
                    "{{.Names}}\t{{.State}}\t{{.Status}}\t{{.Ports}}\t{{.Image}}",
                ],
            )
            for line in (proc.stdout or "").splitlines():
                parts = line.split("\t")
                if not parts or not parts[0].strip():
                    continue
                raw = {
                    "Names": parts[0].strip(),
                    "State": parts[1].strip() if len(parts) > 1 else "",
                    "Status": parts[2].strip() if len(parts) > 2 else "",
                    "Ports": parts[3].strip() if len(parts) > 3 else "",
                    "Image": parts[4].strip() if len(parts) > 4 else "",
                }
                result.append(_normalize_container(raw, cli))
        except (OSError, subprocess.SubprocessError):
            pass
    return result


def find_stopped_postgres_container(port: int = 5432) -> dict | None:
    """Find a stopped local Postgres/pgvector container publishing ``port``."""
    candidates = []
    for c in list_containers():
        if not _is_postgres(c):
            continue
        if not _publishes_port(c, port):
            continue
        if not _is_stopped(c):
            continue
        candidates.append(c)
    if not candidates:
        return None
    # Prefer mindpalace-named containers.
    for c in candidates:
        if "mindpalace" in (c.get("name") or "").lower():
            return c
    return candidates[0]


def start_container(container: dict, timeout: float = READY_TIMEOUT_SECONDS) -> bool:
    cli = container["cli"]
    name = container["name"]
    try:
        proc = _run_cli(cli, ["start", name], timeout=30.0)
    except (OSError, subprocess.SubprocessError):
        return False
    if proc.returncode != 0:
        return False
    host, port = db_host_port()
    # Wait up to timeout for readiness.
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if db_ready(host, port, timeout=1.0):
            return True
        time.sleep(0.25)
    return db_ready(host, port, timeout=1.0)


def provision_local_postgres(timeout: float = READY_TIMEOUT_SECONDS) -> bool:
    """Provision and start a fresh local pgvector container.

    Only for the standard local port. A database on any other port is one the
    operator runs, and creating a container for it is a surprise with a
    lasting consequence.
    """
    clis = _container_clis()
    if not clis:
        return False
    cli = clis[0]
    host, port = db_host_port()
    if port != DEFAULT_POSTGRES_PORT:
        return False
    # If something is already listening, nothing to do.
    if db_ready(host, port, timeout=1.0):
        return True
    name = DEFAULT_CONTAINER_NAME
    cmd = [
        "run",
        "-d",
        "--name",
        name,
        "-e",
        "POSTGRES_DB=mindpalace",
        "-e",
        "POSTGRES_USER=mpadmin",
        "-e",
        "POSTGRES_PASSWORD=secret",
        "-p",
        f"{port}:5432",
        "--restart",
        "unless-stopped",
        DEFAULT_IMAGE,
        "postgres",
        "-c",
        "log_error_verbosity=terse",
    ]
    try:
        proc = _run_cli(cli, cmd, timeout=120.0)
    except (OSError, subprocess.SubprocessError):
        return False
    if proc.returncode != 0:
        # Maybe name already exists; try starting it.
        try:
            _run_cli(cli, ["start", name], timeout=30.0)
        except (OSError, subprocess.SubprocessError):
            pass
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if db_ready(host, port, timeout=1.0):
            return True
        time.sleep(0.25)
    return db_ready(host, port, timeout=1.0)


def ensure_local_postgres(
    *,
    auto_provision: bool = False,
    timeout: float = READY_TIMEOUT_SECONDS,
) -> bool:
    """Try to make a local Postgres reachable.

    Returns True if the port is open afterwards. Only acts for localhost
    targets -- a deployment whose database is elsewhere must not have a local
    container started for it.

    The healthy path costs one TCP connect and nothing else. A query is only
    sent after something has actually been started, because that is the only
    time the answer is in doubt: Postgres accepts a connection before it will
    serve one, so a container that was just started has to be asked a
    question, not merely a socket.
    """
    host, port = db_host_port()
    if not is_local_host(host):
        return tcp_open(host, port, timeout=0.5)
    if tcp_open(host, port, timeout=0.5):
        return True
    # Try to start a stopped container.
    container = find_stopped_postgres_container(port)
    if container is not None:
        if start_container(container, timeout=timeout):
            return True
    if auto_provision:
        if provision_local_postgres(timeout=timeout):
            return True
    return tcp_open(host, port, timeout=0.5)
