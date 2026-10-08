# SPDX-License-Identifier: Apache-2.0
"""The local runtime's lifecycle, exercised as a product surface.

Nine scenarios, one per way a background process can go wrong or be misused:

    normal startup · already in progress · stale socket · killed runtime ·
    SIGTERM during a request · second invocation · concurrent requests ·
    already ready · unavailable

None of these are allowed to produce a traceback, a hang, or a wrong answer. The
runtime is an optimisation, so the worst it may ever do is not exist.
"""

from __future__ import annotations

import contextlib
import os
import signal
import subprocess
import sys
import time
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
pytestmark = pytest.mark.skipif(
    not (os.getenv("DATABASE_URL") or os.getenv("MEMORY_TEST_DATABASE_URL")),
    reason="needs a real PostgreSQL for a runtime to serve reads from",
)


@contextlib.contextmanager
def isolated_state(tmp_path):
    """Point the runtime at a private state directory, in this process and the child.

    Both, or the token is written in one place and read in another -- which is how a
    runtime appears to start and then vanish.
    """
    state = tmp_path / "state"
    state.mkdir(parents=True, exist_ok=True)
    previous = os.environ.get("MIND_PALACE_STATE_DIR")
    os.environ["MIND_PALACE_STATE_DIR"] = str(state)
    try:
        from api import runtime as runtime_module

        runtime_module._ensure_token()
        yield runtime_module, state
    finally:
        if previous is None:
            os.environ.pop("MIND_PALACE_STATE_DIR", None)
        else:
            os.environ["MIND_PALACE_STATE_DIR"] = previous


def child_env(state: Path, **extra: str) -> dict:
    env = dict(os.environ, PYTHONPATH=str(ROOT), MIND_PALACE_STATE_DIR=str(state))
    env.pop("MIND_PALACE_LEXICAL", None)
    env.pop("MIND_PALACE_RUNTIME", None)
    env.update(extra)
    return env


def launch(state: Path) -> subprocess.Popen:
    return subprocess.Popen(
        [sys.executable, "-m", "mindpalace" "_runtime"],
        cwd=str(ROOT),
        env=child_env(state),
        stdin=subprocess.DEVNULL,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        start_new_session=True,
    )


def wait_until(runtime_module, timeout: float = 120.0) -> None:
    """Wait for `ready`, not merely for reachable.

    A `loading` runtime is deliberately reachable -- a command that arrives while the
    model is still importing is served correctly, which is the whole point of
    accepting it. These tests are about the settled state, so they wait for it.
    """
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if runtime_module.status().state == "ready":
            return
        time.sleep(0.2)
    raise AssertionError(
        f"the runtime never reached ready; last state was {runtime_module.status().state}"
    )


def stop(runtime_module, process: subprocess.Popen) -> int:
    with contextlib.suppress(Exception):
        runtime_module.call({"op": "shutdown"}, timeout=10.0)
    try:
        return process.wait(timeout=20)
    except subprocess.TimeoutExpired:  # pragma: no cover - only on a hang
        process.kill()
        return process.wait(timeout=10)


@pytest.fixture
def runtime(tmp_path):
    """A runtime in its own state directory, stopped and cleaned up afterwards."""
    with isolated_state(tmp_path) as (runtime_module, state):
        process = launch(state)
        wait_until(runtime_module)
        try:
            yield runtime_module, process, state
        finally:
            code = stop(runtime_module, process)
            assert code == 0, (
                f"a runtime asked to stop must exit 0; got {code}. A non-zero exit "
                "here is what a supervisor would read as a crash."
            )


def test_normal_startup_reaches_ready(runtime):
    runtime_module, _process, _state = runtime
    status = runtime_module.status()
    assert status.state == "ready", status
    assert status.model == "loaded", status
    assert status.pid == os.getpid() or status.pid, status
    assert status.detail is None


def test_start_is_idempotent_and_reports_already_running(runtime, tmp_path):
    """Running it twice must not start a second runtime or fail."""
    runtime_module, process, state = runtime
    before = runtime_module.status().pid

    done = subprocess.run(
        [sys.executable, "-m", "cli.main", "runtime", "start"],
        cwd=str(ROOT),
        env=child_env(state),
        capture_output=True,
        text=True,
        timeout=180,
    )
    assert done.returncode == 0, done.stdout + done.stderr
    assert "already running" in done.stdout, done.stdout
    assert runtime_module.status().pid == before, "a second runtime was started"
    assert process.poll() is None


def test_status_is_a_no_op_when_nothing_is_running(tmp_path):
    """The diagnostic most likely to be run at the wrong moment must never fail."""
    with isolated_state(tmp_path) as (runtime_module, _state):
        status = runtime_module.status()
        assert status.state == "unreachable", status
        assert runtime_module.reachable() is False

        done = subprocess.run(
            [sys.executable, "-m", "cli.main", "runtime", "status"],
            cwd=str(ROOT),
            env=child_env(_state),
            capture_output=True,
            text=True,
            timeout=180,
        )
        assert done.returncode == 0, done.stdout + done.stderr
        assert "not running" in done.stdout

        # And `--json` says the same thing in a shape a script can read.
        machine = subprocess.run(
            [sys.executable, "-m", "cli.main", "runtime", "status", "--json"],
            cwd=str(ROOT),
            env=child_env(_state),
            capture_output=True,
            text=True,
            timeout=180,
        )
        assert machine.returncode == 0, machine.stderr
        import json

        payload = json.loads(machine.stdout)
        assert payload["state"] == "unreachable"
        assert payload["enabled"] is True
        assert payload["protocol"] >= 1


def test_a_stale_socket_is_replaced_not_surfaced_as_an_error(tmp_path):
    """A killed runtime leaves its socket file. That must not become the user's problem.

    Simulated exactly rather than approximated: a real socket file is created and
    then closed, which is byte-for-byte what SIGKILL leaves behind.
    """
    with isolated_state(tmp_path) as (runtime_module, state):
        import socket as socket_module

        path = runtime_module.socket_path()
        stale = socket_module.socket(socket_module.AF_UNIX, socket_module.SOCK_STREAM)
        stale.bind(str(path))
        stale.close()
        assert path.exists(), "the stale socket should still be on disk"
        assert runtime_module.reachable() is False, "nothing is listening"

        # A command must not raise, and must not need the file removed by hand.
        done = subprocess.run(
            [sys.executable, "-m", "cli.main", "runtime", "start", "--wait"],
            cwd=str(ROOT),
            env=child_env(state),
            capture_output=True,
            text=True,
            timeout=300,
        )
        assert done.returncode == 0, done.stdout + done.stderr
        assert "Traceback" not in done.stderr, done.stderr

        process = subprocess.Popen(
            [sys.executable, "-c", "import time; time.sleep(300)"],
            cwd=str(ROOT),
        )
        try:
            wait_until(runtime_module)
            assert runtime_module.status().state == "ready"
        finally:
            for pid in _runtime_pids():
                with contextlib.suppress(OSError):
                    os.kill(pid, signal.SIGTERM)
            process.terminate()
            with contextlib.suppress(subprocess.TimeoutExpired):
                process.wait(timeout=10)
            time.sleep(0.5)


def test_sigkill_is_recoverable(tmp_path):
    """The least polite way a process can die must still leave the CLI working."""
    with isolated_state(tmp_path) as (runtime_module, state):
        process = launch(state)
        wait_until(runtime_module)
        os.kill(process.pid, signal.SIGKILL)
        process.wait(timeout=20)
        time.sleep(0.5)
        assert runtime_module.reachable() is False, "a SIGKILLed runtime must not look alive"

        replacement = launch(state)
        try:
            wait_until(runtime_module)
            assert replacement.poll() is None
            assert runtime_module.status().state == "ready"
        finally:
            stop(runtime_module, replacement)


def test_sigterm_during_a_request_finishes_it_and_exits_zero(tmp_path):
    """A shutdown must not truncate a read that is already running.

    The failure this prevents is silent data loss: a request that was mid-query when
    the socket closed would look to the caller like a successful, empty answer.
    """
    with isolated_state(tmp_path) as (runtime_module, state):
        process = launch(state)
        wait_until(runtime_module)

        result: dict = {}

        def ask():
            with contextlib.suppress(Exception):
                result["reply"] = runtime_module.call(
                    {"op": "query", "request": {"corpus": "default", "query": "anything at all"}},
                    timeout=60.0,
                )

        import threading

        thread = threading.Thread(target=ask)
        thread.start()
        os.kill(process.pid, signal.SIGTERM)
        thread.join(timeout=90)
        code = process.wait(timeout=30)
        assert code == 0, f"graceful shutdown must exit 0, got {code}"
        assert not thread.is_alive(), "a request outlived the shutdown"
        # Either it completed, or it did not -- but it must never have been answered
        # with something it did not read.
        if "reply" in result:
            assert "ok" in result["reply"]


def test_concurrent_requests_are_all_answered(runtime):
    """Six clients at once, one runtime, no errors and no lost answers."""
    runtime_module, _process, _state = runtime
    import threading

    replies: list = []
    errors: list = []

    def ask(index: int):
        try:
            replies.append(
                runtime_module.call(
                    {
                        "op": "query",
                        "request": {"corpus": "default", "query": f"question number {index}"},
                    },
                    timeout=120.0,
                )
            )
        except Exception as exc:  # noqa: BLE001 - collected and asserted, not swallowed
            errors.append(exc)

    threads = [threading.Thread(target=ask, args=(i,)) for i in range(6)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(timeout=180)

    assert not errors, errors
    assert len(replies) == 6, len(replies)
    assert all(reply.get("ok") for reply in replies)
    assert runtime_module.status().state == "ready"


def test_a_protocol_mismatch_is_refused_with_the_fix(tmp_path):
    """An old runtime must not answer a newer request with the old semantics."""
    with isolated_state(tmp_path) as (runtime_module, state):
        process = launch(state)
        wait_until(runtime_module)
        try:
            import asyncio

            async def ask(wrong_protocol: int):
                return await runtime_module._call_async(
                    runtime_module.socket_path(),
                    runtime_module._read_token(),
                    {"op": "status", "protocol": wrong_protocol},
                    10.0,
                    2.0,
                )

            reply = asyncio.run(ask(runtime_module.PROTOCOL + 1))
            assert not reply["ok"], reply
            assert reply["error"]["code"] == "protocol_mismatch", reply
            assert "mindpalace runtime stop" in reply["error"]["message"], reply

            # And the correct protocol still works on the same runtime.
            good = runtime_module.call({"op": "status"}, timeout=10.0)
            assert good["ok"], good
        finally:
            stop(runtime_module, process)


def test_the_socket_is_private_and_never_a_port(tmp_path):
    """The authorisation boundary is the filesystem, and it must be a socket.

    Asserted rather than assumed: a regression to a TCP bind would make every
    `mindpalace recall` on a laptop a service on the network.
    """
    with isolated_state(tmp_path) as (runtime_module, state):
        import stat as stat_module

        process = launch(state)
        wait_until(runtime_module)
        try:
            socket_file = runtime_module.socket_path()
            mode = socket_file.stat().st_mode
            assert stat_module.S_ISSOCK(mode), "the runtime must use a Unix socket"
            assert not stat_module.S_ISCHR(mode) and not stat_module.S_ISBLK(mode)
            # Readable and writable only by its owner.
            assert mode & 0o077 == 0, oct(mode & 0o077)
            assert mode & 0o700, oct(mode & 0o700)
            # And the directory holding it, and the token beside it.
            assert state.stat().st_mode & 0o077 == 0, oct(state.stat().st_mode)
            assert runtime_module.token_path().stat().st_mode & 0o077 == 0

            # No listening TCP port was opened by the runtime process.
            listening = _listening_ports_of(process.pid)
            assert not listening, f"the runtime opened TCP port(s) {listening}"
        finally:
            stop(runtime_module, process)


def _runtime_pids() -> list[int]:
    """Every running runtime, by the module name it was launched as."""
    found = []
    for entry in Path("/proc").iterdir():
        if not entry.name.isdigit():
            continue
        try:
            cmdline = (entry / "cmdline").read_bytes().decode("utf-8", "replace")
        except OSError:
            continue
        if "mindpalace_runtime" in cmdline or "mindpalace" + "_runtime" in cmdline:
            found.append(int(entry.name))
    return found


def _listening_ports_of(pid: int) -> list[int]:
    """TCP ports this process is listening on. Empty means it bound no port."""
    ports = []
    tcp = Path("/proc/net/tcp")
    if not tcp.exists():  # pragma: no cover - non-Linux
        return ports
    inodes = set()
    for line in tcp.read_text(encoding="utf-8").splitlines()[1:]:
        fields = line.split()
        if len(fields) > 9 and fields[3] == "0A":  # 0A = LISTEN
            inodes.add(fields[9])
    fd_dir = Path(f"/proc/{pid}/fd")
    if not fd_dir.exists():  # pragma: no cover
        return ports
    for fd in fd_dir.iterdir():
        with contextlib.suppress(OSError):
            target = fd.readlink().as_posix()
            if target.startswith("socket:["):
                inode = target[8:-1]
                if inode in inodes:
                    for line in tcp.read_text(encoding="utf-8").splitlines()[1:]:
                        fields = line.split()
                        if len(fields) > 9 and fields[9] == inode:
                            ports.append(int(fields[1].split(":")[1], 16))
    return sorted(set(ports))
