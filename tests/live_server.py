"""Shared live HTTP server fixture for the brand-kit integration scenario.

Starts a real uvicorn server against the session database as the least-privileged
runtime role, with scope-token enforcement ON, and yields an object carrying the
server URL and one read_write credential per seeded corpus.
"""

from __future__ import annotations

import asyncio
import os
import socket
import subprocess
import sys
import time
import uuid

import httpx
import pytest
import sqlalchemy as sa

from tenant_harness import (
    _restore_secret_and_disable,
    _rotate_and_enable,
    apply_provision_sql,
    runtime_url,
    sync_url,
)

CORPORA = ("acme", "globex")


def _issue_cred(label: str, corpus: str, role: str = "read_write") -> str:
    """Mirror of scripts/probe_e.py's issue_cred: admin session, ADMIN principal,
    issue one credential, return its secret."""

    async def _issue() -> str:
        from api.services import authz
        from api.services.db import admin_session_scope, principal_scope
        from api.services.authz import ADMIN_PRINCIPAL

        async with admin_session_scope(url=os.environ["DATABASE_URL"]) as db:
            with principal_scope(ADMIN_PRINCIPAL):
                cred = await authz.issue_credential(db, label, [corpus], role)
                return cred["secret"]

    return asyncio.run(_issue())


def _find_free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def _insert_corpora(names: dict[str, str]) -> None:
    """Insert corpora as the maintenance role (sync_url), mirroring the
    world fixture in tests/test_enforcement_e2e.py."""
    engine = sa.create_engine(sync_url())
    try:
        with engine.begin() as conn:
            for short_name, full_name in names.items():
                conn.execute(
                    sa.text(
                        "INSERT INTO corpora (id, name, description) VALUES "
                        "(encode(sha256(convert_to('corpus:' || :n, 'UTF8')), 'hex'), "
                        ":n, '') ON CONFLICT DO NOTHING"
                    ),
                    {"n": full_name},
                )
    finally:
        engine.dispose()


@pytest.fixture(scope="module")
def live_server(tmp_path_factory):
    """Module-scoped live HTTP server with enforcement ON.

    Yields an object with `.url`, `.keys = {"acme": "...", "globex": "..."}` and
    `.stderr_path`.
    """
    with pytest.MonkeyPatch.context():
        # 1. Provision the runtime role, rotate the scope secret, enable enforcement.
        apply_provision_sql()
        secret, original = _rotate_and_enable(os.environ["DATABASE_URL"])

        # 2. Seed corpora as the maintenance role, with unique names per fixture
        #    instance so parallel suites do not collide.
        names = {name: f"{name}_{uuid4().hex[:8]}" for name in CORPORA}
        _insert_corpora(names)

        # 3. Issue one read_write credential per corpus.
        keys = {name: _issue_cred(f"live-{name}-rw", corp_name, "read_write") for name, corp_name in names.items()}

        # 4. Start the server as a subprocess.
        port = _find_free_port()
        tmp_dir = tmp_path_factory.mktemp("live_server")
        ingest_root = str(tmp_dir / "ingest")
        os.makedirs(ingest_root, exist_ok=True)

        stderr_path = str(tmp_dir / "server_stderr.txt")
        stdout_path = str(tmp_dir / "server_stdout.txt")

        env = dict(os.environ)
        env["DATABASE_URL"] = runtime_url()
        env["ADMIN_DATABASE_URL"] = os.environ["DATABASE_URL"]
        env["MIND_PALACE_SCOPE_SECRET"] = secret
        env["MIND_PALACE_ENFORCE_SCOPE_TOKEN"] = "on"
        env["MIND_PALACE_AUTH_MODE"] = "required"
        env["MIND_PALACE_DEPLOYMENT"] = "server"
        env["MIND_PALACE_RUNTIME"] = "0"
        env["MIND_PALACE_INGEST_ROOTS"] = ingest_root

        stderr_file = open(stderr_path, "w")
        stdout_file = open(stdout_path, "w")

        proc = subprocess.Popen(
            [
                sys.executable,
                "-m",
                "uvicorn",
                "api.main:app",
                "--host",
                "127.0.0.1",
                "--port",
                str(port),
                "--log-level",
                "warning",
            ],
            env=env,
            stdout=stdout_file,
            stderr=stderr_file,
        )

        base_url = f"http://127.0.0.1:{port}"

        # Poll /health/live until 200 (60 s deadline).
        deadline = time.monotonic() + 60
        healthy = False
        while time.monotonic() < deadline:
            if proc.poll() is not None:
                stderr_file.close()
                stdout_file.close()
                try:
                    with open(stderr_path) as f:
                        err_text = f.read()
                except OSError:
                    err_text = "(unreadable)"
                pytest.fail(f"server exited with {proc.returncode}; stderr:\n{err_text}")
            try:
                if httpx.get(f"{base_url}/health/live", timeout=2).status_code == 200:
                    healthy = True
                    break
            except Exception:
                pass
            time.sleep(0.5)

        if not healthy:
            proc.terminate()
            try:
                proc.wait(timeout=10)
            except subprocess.TimeoutExpired:
                proc.kill()
            stderr_file.close()
            stdout_file.close()
            try:
                with open(stderr_path) as f:
                    err_text = f.read()
            except OSError:
                err_text = "(unreadable)"
            pytest.fail(f"server did not become healthy; stderr:\n{err_text}")

        class _Server:
            pass

        srv = _Server()
        srv.url = base_url
        srv.keys = keys
        srv.corpora = names
        srv.stderr_path = stderr_path

        yield srv

        # 6. Teardown: terminate (kill after 10 s), then restore the secret.
        proc.terminate()
        try:
            proc.wait(timeout=10)
        except subprocess.TimeoutExpired:
            proc.kill()
        stderr_file.close()
        stdout_file.close()
        _restore_secret_and_disable(os.environ["DATABASE_URL"], original)
