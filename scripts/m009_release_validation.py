#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 Raunak Dey

"""Run focused M009 release validation against a disposable PostgreSQL database.

The runner deliberately seeds only the relational objects needed by the durable
memory feed.  It never connects the application to the configured archive for
writes: the application, SDK, CLI, fixtures, and SQL timing all use a newly
created database which is dropped in the outer ``finally`` block.
"""

from __future__ import annotations

import argparse
import asyncio
import hashlib
import importlib.metadata
import json
import os
import platform
import re
import secrets
import socket
import subprocess
import sys
import time
from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

import httpx
import sqlalchemy as sa
from sqlalchemy.engine import URL, make_url
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.pool import NullPool

ROOT = Path(__file__).resolve().parents[1]
OUTPUT_DIR = ROOT / "docs" / "m009"
DEFAULT_DATABASE_URL = "postgresql://mpadmin:secret@localhost:5432/mindpalace"
LIVE_CORPUS = "m009-live"
LIVE_OTHER_CORPUS = "m009-live-other"
CONCURRENCY_CORPUS = "m009-concurrency"
CONCURRENCY_OTHER_CORPUS = "m009-concurrency-other"
BENCHMARK_CORPUSES = {
    100: "m009-benchmark-100",
    1000: "m009-benchmark-1000",
    10000: "m009-benchmark-10000",
}
PAGE_SIZE_LIVE = 2
PAGE_SIZE_CONCURRENCY = 5
PAGE_SIZE_BENCHMARK = 50
BENCHMARK_ITERATIONS = 20
BENCHMARK_WARMUPS = 2
M009_MIGRATION_FILE = ROOT / "migrations" / "alembic.ini"
HEX_ID_RE = re.compile(r"\b[0-9a-f]{64}\b")
TIMESTAMP_LITERAL_RE = re.compile(r"'[^']+'::timestamp with time zone")


class ValidationFailure(RuntimeError):
    """A release-validation assertion or environment failure."""


def require(condition: bool, message: str) -> None:
    if not condition:
        raise ValidationFailure(message)


def require_cursor(value: str | None, message: str) -> str:
    if not isinstance(value, str) or not value:
        raise ValidationFailure(message)
    return value


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="microseconds").replace("+00:00", "Z")


def sync_url(value: str | URL) -> str:
    """Return a URL usable by the installed psycopg2 driver."""
    raw = value.render_as_string(hide_password=False) if isinstance(value, URL) else str(value)
    return (
        raw.replace("postgresql+asyncpg://", "postgresql+psycopg2://")
        .replace("postgresql+psycopg://", "postgresql+psycopg2://")
        .replace("postgresql://", "postgresql+psycopg2://")
    )


def async_url(value: str | URL) -> str:
    """Return a URL usable by the installed asyncpg driver."""
    raw = value.render_as_string(hide_password=False) if isinstance(value, URL) else str(value)
    return (
        raw.replace("postgresql+psycopg2://", "postgresql+asyncpg://")
        .replace("postgresql+psycopg://", "postgresql+asyncpg://")
        .replace("postgresql://", "postgresql+asyncpg://")
    )


def app_url(value: str | URL) -> str:
    """Return the URL shape expected by the application and Alembic environment."""
    return sync_url(value).replace("postgresql+psycopg2://", "postgresql://")


def database_url_with_name(value: str, database_name: str) -> str:
    parsed = make_url(sync_url(value)).set(database=database_name)
    return app_url(parsed)


def source_database_url() -> str:
    return os.getenv("M009_ADMIN_DATABASE_URL") or os.getenv("DATABASE_URL") or DEFAULT_DATABASE_URL


def make_disposable_database_name() -> str:
    return "m009_validation_" + secrets.token_hex(7)


def quote_identifier(value: str) -> str:
    require(bool(re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", value)), "unsafe database identifier")
    return '"' + value + '"'


class DisposableDatabase:
    """Create one uniquely named database and remove it after validation."""

    def __init__(self, source_url: str) -> None:
        self.source_url = sync_url(source_url)
        self.name = make_disposable_database_name()
        self.url = database_url_with_name(self.source_url, self.name)
        self._admin_url = self.source_url
        self.created = False

    def _admin_candidates(self) -> list[str]:
        parsed = make_url(self.source_url)
        candidates = [self.source_url]
        if parsed.database and parsed.database != "postgres":
            candidates.append(
                str(parsed.set(database="postgres").render_as_string(hide_password=False))
            )
        return candidates

    def create(self) -> None:
        errors: list[str] = []
        identifier = quote_identifier(self.name)
        for candidate in self._admin_candidates():
            engine = sa.create_engine(candidate, isolation_level="AUTOCOMMIT", poolclass=NullPool)
            try:
                with engine.connect() as connection:
                    connection.execute(sa.text(f"CREATE DATABASE {identifier}"))
                self._admin_url = candidate
                self.created = True
                return
            except SQLAlchemyError as exc:
                errors.append(type(exc).__name__)
            finally:
                engine.dispose()
        detail = ", ".join(errors) if errors else "no PostgreSQL candidate URL"
        raise ValidationFailure(f"could not create disposable PostgreSQL database ({detail})")

    def drop(self) -> None:
        if not self.created:
            return
        identifier = quote_identifier(self.name)
        last_error: Exception | None = None
        for attempt in range(3):
            engine = sa.create_engine(
                self._admin_url, isolation_level="AUTOCOMMIT", poolclass=NullPool
            )
            try:
                with engine.connect() as connection:
                    connection.execute(
                        sa.text(
                            "SELECT pg_terminate_backend(pid) FROM pg_stat_activity "
                            "WHERE datname = :name AND pid <> pg_backend_pid()"
                        ),
                        {"name": self.name},
                    )
                    connection.execute(sa.text(f"DROP DATABASE IF EXISTS {identifier}"))
                self.created = False
                return
            except SQLAlchemyError as exc:
                last_error = exc
            finally:
                engine.dispose()
            if attempt < 2:
                time.sleep(0.25 * (attempt + 1))
        raise ValidationFailure(
            "disposable PostgreSQL database cleanup failed"
            + (f" ({type(last_error).__name__})" if last_error else "")
        )


def project_python() -> str:
    candidate = ROOT / "venvmp" / "bin" / "python"
    return str(candidate) if candidate.is_file() else sys.executable


def ensure_project_runtime(argv: Sequence[str] | None = None) -> None:
    candidate = Path(project_python())
    if candidate.is_file() and Path(sys.executable).absolute() != candidate.absolute():
        arguments = list(sys.argv[1:] if argv is None else argv)
        os.execv(
            str(candidate),
            [str(candidate), str(Path(__file__).resolve()), *arguments],
        )


def child_environment(database_url: str, cursor_secret: str) -> dict[str, str]:
    env = os.environ.copy()
    env["DATABASE_URL"] = database_url
    env["MIND_PALACE_CURSOR_SECRET"] = cursor_secret
    env["PYTHONUNBUFFERED"] = "1"
    pythonpath = env.get("PYTHONPATH", "")
    env["PYTHONPATH"] = str(ROOT) + (os.pathsep + pythonpath if pythonpath else "")
    return env


def redact_text(value: Any, sensitive: Sequence[str]) -> str:
    text = str(value)
    for item in sensitive:
        if item:
            text = text.replace(item, "<redacted>")
    text = HEX_ID_RE.sub("<redacted-id>", text)
    return text[-4000:]


def run_checked(
    command: Sequence[str], env: dict[str, str], timeout: float, sensitive: Sequence[str]
) -> subprocess.CompletedProcess[str]:
    try:
        result = subprocess.run(
            list(command),
            cwd=ROOT,
            env=env,
            text=True,
            capture_output=True,
            timeout=timeout,
            check=False,
        )
    except subprocess.TimeoutExpired as exc:
        raise ValidationFailure(f"command timed out: {redact_text(command, sensitive)}") from exc
    if result.returncode != 0:
        output = f"{result.stdout}\n{result.stderr}"
        raise ValidationFailure(
            f"command failed ({result.returncode}): {redact_text(command, sensitive)}\n"
            f"{redact_text(output, sensitive)}"
        )
    return result


def run_migrations(database_url: str, cursor_secret: str) -> str:
    env = child_environment(database_url, cursor_secret)
    command = [
        project_python(),
        "-m",
        "alembic",
        "-c",
        str(M009_MIGRATION_FILE),
        "upgrade",
        "head",
    ]
    run_checked(command, env, timeout=180, sensitive=[database_url, cursor_secret])
    from alembic.config import Config
    from alembic.script import ScriptDirectory

    config = Config(str(M009_MIGRATION_FILE))
    config.set_main_option("script_location", str(ROOT / "migrations"))
    head = ScriptDirectory.from_config(config).get_current_head()
    require(head is not None, "Alembic has no migration head")
    migration_head = str(head)
    engine = sa.create_engine(sync_url(database_url), poolclass=NullPool)
    try:
        with engine.connect() as connection:
            current = connection.execute(
                sa.text("SELECT version_num FROM alembic_version")
            ).scalar_one()
    finally:
        engine.dispose()
    require(current == head, f"Alembic version {current!r} does not match head {head!r}")
    return migration_head


@dataclass(frozen=True)
class VersionSpec:
    path: str
    version_id: str
    document_id: str
    observed_at: datetime
    event: str = "NEW"
    version_number: int = 1
    predecessor_id: str | None = None


@dataclass(frozen=True)
class Fixture:
    corpus: str
    corpus_id: str
    specs: tuple[VersionSpec, ...]


def corpus_id_for_name(name: str) -> str:
    return hashlib.sha256(f"corpus:{name}".encode()).hexdigest()


def memory_document_id(corpus_id: str, path: str) -> str:
    return hashlib.sha256(f"{corpus_id}:{path}".encode()).hexdigest()


def version_id_for(corpus: str, path: str, ordinal: int) -> str:
    return hashlib.sha256(f"m009:{corpus}:{path}:{ordinal}".encode()).hexdigest()


def make_specs(
    corpus: str,
    count: int,
    base: datetime,
    *,
    equal_groups: bool = False,
) -> tuple[VersionSpec, ...]:
    specs: list[VersionSpec] = []
    for ordinal in range(count):
        path = f"m009/{corpus}/item-{ordinal:05d}.md"
        if equal_groups and ordinal in {3, 4, 5}:
            observed_at = base + timedelta(seconds=3)
        else:
            observed_at = base + timedelta(seconds=ordinal)
        specs.append(
            VersionSpec(
                path=path,
                version_id=version_id_for(corpus, path, ordinal),
                document_id=f"m009-document-{ordinal:05d}",
                observed_at=observed_at,
            )
        )
    return tuple(specs)


def late_spec(
    corpus: str,
    label: str,
    observed_at: datetime,
    version_id: str,
    document_id: str,
) -> VersionSpec:
    return VersionSpec(
        path=f"m009/{corpus}/late-{label}.md",
        version_id=version_id,
        document_id=document_id,
        observed_at=observed_at,
    )


def ensure_corpus(connection, name: str) -> str:
    corpus_id = corpus_id_for_name(name)
    existing = connection.execute(
        sa.text("SELECT id FROM corpora WHERE name = :name"), {"name": name}
    ).scalar_one_or_none()
    if existing is not None:
        require(str(existing).strip() == corpus_id, f"corpus identity collision for {name}")
        return corpus_id
    connection.execute(
        sa.text("INSERT INTO corpora (id, name, description) VALUES (:id, :name, :description)"),
        {"id": corpus_id, "name": name, "description": "M009 disposable validation fixture"},
    )
    return corpus_id


def insert_specs(connection, corpus_id: str, specs: Iterable[VersionSpec]) -> None:
    documents = []
    versions = []
    for spec in specs:
        identity = memory_document_id(corpus_id, spec.path)
        content = "M009 release validation fixture"
        documents.append({"corpus_id": corpus_id, "id": identity, "path": spec.path})
        versions.append(
            {
                "corpus_id": corpus_id,
                "memory_document_id": identity,
                "id": spec.version_id,
                "predecessor_id": spec.predecessor_id,
                "version_number": spec.version_number,
                "document_id": spec.document_id,
                "event": spec.event,
                "content": content,
                "metadata": "{}",
                "fingerprint": hashlib.sha256(content.encode()).hexdigest(),
                "observed_at": spec.observed_at,
            }
        )
    if documents:
        connection.execute(
            sa.text(
                "INSERT INTO memory_documents (corpus_id, id, path) VALUES (:corpus_id, :id, :path)"
            ),
            documents,
        )
        connection.execute(
            sa.text(
                "INSERT INTO memory_versions ("
                "corpus_id, memory_document_id, id, predecessor_id, version_number, "
                "document_id, event, content, metadata, fingerprint, observed_at"
                ") VALUES ("
                ":corpus_id, :memory_document_id, :id, :predecessor_id, :version_number, "
                ":document_id, :event, :content, CAST(:metadata AS jsonb), :fingerprint, "
                ":observed_at"
                ")"
            ),
            versions,
        )


def seed_fixture(
    engine,
    corpus: str,
    specs: Sequence[VersionSpec],
) -> Fixture:
    with engine.begin() as connection:
        corpus_id = ensure_corpus(connection, corpus)
        insert_specs(connection, corpus_id, specs)
    return Fixture(corpus=corpus, corpus_id=corpus_id, specs=tuple(specs))


def insert_late_rows(
    engine,
    target: Fixture,
    target_specs: Sequence[VersionSpec],
    other_corpus: str,
    other_specs: Sequence[VersionSpec],
) -> Fixture:
    with engine.begin() as connection:
        insert_specs(connection, target.corpus_id, target_specs)
        other_id = ensure_corpus(connection, other_corpus)
        insert_specs(connection, other_id, other_specs)
    return Fixture(
        corpus=other_corpus,
        corpus_id=other_id,
        specs=tuple(other_specs),
    )


def fetch_rows(engine, corpus: str) -> list[dict[str, Any]]:
    with engine.connect() as connection:
        rows = connection.execute(
            sa.text(
                "SELECT v.id, v.observed_at, v.predecessor_id, v.version_number, "
                "v.document_id, v.event, d.path "
                "FROM memory_versions v JOIN memory_documents d "
                "ON d.corpus_id = v.corpus_id AND d.id = v.memory_document_id "
                "JOIN corpora c ON c.id = v.corpus_id "
                "WHERE c.name = :corpus ORDER BY v.observed_at ASC, v.id ASC"
            ),
            {"corpus": corpus},
        ).mappings()
        return [dict(row) for row in rows]


def archive_fingerprint(engine, corpora: Sequence[str]) -> str:
    digest = hashlib.sha256()
    for corpus in sorted(corpora):
        digest.update(corpus.encode("utf-8"))
        digest.update(b"\0")
        for row in fetch_rows(engine, corpus):
            value = {
                "corpus": corpus,
                "version_id": str(row["id"]).strip(),
                "observed_at": row["observed_at"].astimezone(timezone.utc).isoformat(),
                "predecessor_id": (
                    str(row["predecessor_id"]).strip()
                    if row["predecessor_id"] is not None
                    else None
                ),
                "version_number": row["version_number"],
                "document_id": row["document_id"],
                "event": row["event"],
                "path": row["path"],
            }
            digest.update(json.dumps(value, sort_keys=True, separators=(",", ":")).encode("utf-8"))
            digest.update(b"\0")
    return digest.hexdigest()


def timestamp(value: Any) -> datetime:
    if isinstance(value, datetime):
        result = value
    else:
        text_value = str(value).replace("Z", "+00:00")
        result = datetime.fromisoformat(text_value)
    require(result.tzinfo is not None and result.utcoffset() is not None, "naive timestamp")
    return result.astimezone(timezone.utc)


def logical_item(item: Any) -> dict[str, Any]:
    value = item.model_dump(mode="json") if hasattr(item, "model_dump") else dict(item)
    return {
        "version_id": str(value["version_id"]),
        "document_id": str(value["document_id"]),
        "corpus": str(value["corpus"]),
        "path": str(value["path"]),
        "version_number": int(value["version_number"]),
        "status": str(value["status"]),
        "observed_at": timestamp(value["observed_at"])
        .isoformat(timespec="microseconds")
        .replace("+00:00", "Z"),
        "predecessor_id": (
            str(value["predecessor_id"]) if value.get("predecessor_id") is not None else None
        ),
    }


def logical_key(item: dict[str, Any]) -> tuple[datetime, str]:
    return timestamp(item["observed_at"]), item["version_id"]


def sequence_digest(items: Sequence[dict[str, Any]]) -> str:
    encoded = json.dumps(items, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    return hashlib.sha256(encoded.encode("utf-8")).hexdigest()


def assert_ordered_unique(items: Sequence[dict[str, Any]], corpus: str) -> None:
    require(bool(items), f"feed returned no items for {corpus}")
    ids = [item["version_id"] for item in items]
    require(len(ids) == len(set(ids)), "feed returned duplicate version IDs")
    require(all(item["corpus"] == corpus for item in items), "feed crossed corpus boundary")
    keys = [logical_key(item) for item in items]
    require(keys == sorted(keys), "feed items are not in keyset order")


def percentile(values: Sequence[float], fraction: float) -> float:
    require(bool(values), "cannot calculate a percentile of no samples")
    ordered = sorted(values)
    if len(ordered) == 1:
        return ordered[0]
    position = (len(ordered) - 1) * fraction
    lower = int(position)
    upper = min(lower + 1, len(ordered) - 1)
    weight = position - lower
    return ordered[lower] + (ordered[upper] - ordered[lower]) * weight


def timing_summary(samples: Sequence[float]) -> dict[str, Any]:
    require(len(samples) >= BENCHMARK_ITERATIONS, "benchmark did not collect enough samples")
    return {
        "p50_ms": round(percentile(samples, 0.50), 6),
        "p95_ms": round(percentile(samples, 0.95), 6),
        "samples_ms": [round(value, 6) for value in samples],
    }


def cursor_length_summary(cursors: Sequence[str]) -> dict[str, Any]:
    if not cursors:
        return {"count": 0, "lengths": [], "sha256": None}
    joined = "\n".join(cursors).encode("utf-8")
    return {
        "count": len(cursors),
        "lengths": sorted({len(value) for value in cursors}),
        "sha256": hashlib.sha256(joined).hexdigest(),
    }


def interface_summary(
    items: Sequence[dict[str, Any]], page_sizes: Sequence[int], cursors: Sequence[str]
) -> dict[str, Any]:
    return {
        "status": "passed",
        "item_count": len(items),
        "page_count": len(page_sizes),
        "pages": list(page_sizes),
        "items": list(items),
        "continuation_count": max(0, len(page_sizes) - 1),
        "logical_sequence_sha256": sequence_digest(items),
        "first_item_sha256": hashlib.sha256(
            json.dumps(items[0], sort_keys=True, separators=(",", ":")).encode()
        ).hexdigest(),
        "last_item_sha256": hashlib.sha256(
            json.dumps(items[-1], sort_keys=True, separators=(",", ":")).encode()
        ).hexdigest(),
        "cursor_redaction": cursor_length_summary(cursors),
    }


def assert_artifact_safe(value: Any, sensitive: Sequence[str]) -> None:
    encoded = json.dumps(value, sort_keys=True, ensure_ascii=False)
    for item in sensitive:
        if item:
            require(item not in encoded, "redaction check found sensitive data")


def write_json(path: Path, value: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(path.name + ".tmp")
    temporary.write_text(
        json.dumps(value, indent=2, sort_keys=True, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )
    temporary.replace(path)


def dependency_versions() -> dict[str, str]:
    names = ("alembic", "asyncpg", "fastapi", "httpx", "psycopg2-binary", "sqlalchemy", "uvicorn")
    versions: dict[str, str] = {}
    for name in names:
        try:
            versions[name] = importlib.metadata.version(name)
        except importlib.metadata.PackageNotFoundError:
            versions[name] = "not-installed"
    return versions


def reproducibility(
    database_url: str,
    migration_head: str,
    page_size: int,
    fixture: dict[str, Any],
    *,
    server_port: int | None = None,
) -> dict[str, Any]:
    result: dict[str, Any] = {
        "python": platform.python_version(),
        "platform": platform.platform(),
        "dependencies": dependency_versions(),
        "database": {
            "backend": "postgresql",
            "disposable": True,
            "name": "<created-and-dropped>",
            "credentials": "redacted",
        },
        "migrations": {
            "command": [
                "python",
                "-m",
                "alembic",
                "-c",
                "migrations/alembic.ini",
                "upgrade",
                "head",
            ],
            "head": migration_head,
        },
        "fixture": fixture,
        "page_size": page_size,
        "seed_tables": ["corpora", "memory_documents", "memory_versions"],
        "excluded_live_tables": [
            "documents",
            "chunks",
            "ingestion_manifest",
            "memory_chunks",
            "memory_claims",
            "memory_evidence",
            "memory_snapshots",
            "memory_snapshot_versions",
        ],
    }
    if server_port is not None:
        result["server"] = {
            "command": ["uvicorn", "api.main:app", "--host", "127.0.0.1", "--port", "<free-port>"],
            "host": "127.0.0.1",
            "port": server_port,
        }
    return result


def seed_live_fixture(engine) -> tuple[Fixture, Fixture]:
    base = datetime(2026, 1, 1, tzinfo=timezone.utc)
    live = seed_fixture(engine, LIVE_CORPUS, make_specs(LIVE_CORPUS, 12, base))
    other = seed_fixture(
        engine,
        LIVE_OTHER_CORPUS,
        make_specs(LIVE_OTHER_CORPUS, 3, base + timedelta(days=1)),
    )
    return live, other


def free_localhost_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


class UvicornServer:
    """Run the actual application entry point in a child process."""

    def __init__(self, database_url: str, cursor_secret: str) -> None:
        self.database_url = database_url
        self.cursor_secret = cursor_secret
        self.port: int | None = None
        self.process: subprocess.Popen[str] | None = None
        self.output = ""

    def __enter__(self) -> "UvicornServer":
        self.port = free_localhost_port()
        executable = ROOT / "venvmp" / "bin" / "python"
        if not executable.is_file():
            executable = Path(sys.executable)
        require(executable.is_file(), "the project Python interpreter is unavailable")
        env = child_environment(self.database_url, self.cursor_secret)
        command = [
            str(executable),
            "-m",
            "uvicorn",
            "api.main:app",
            "--host",
            "127.0.0.1",
            "--port",
            str(self.port),
            "--log-level",
            "warning",
        ]
        self.process = subprocess.Popen(
            command,
            cwd=ROOT,
            env=env,
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
        )
        try:
            self._wait_ready()
        except Exception:
            self._stop()
            raise
        return self

    @property
    def base_url(self) -> str:
        require(self.port is not None, "Uvicorn server has no port")
        return f"http://127.0.0.1:{self.port}"

    def _wait_ready(self) -> None:
        process = self.process
        if process is None or self.port is None:
            raise ValidationFailure("Uvicorn process did not start")
        deadline = time.monotonic() + 30
        last_error = ""
        while time.monotonic() < deadline:
            if process.poll() is not None:
                self._collect_output()
                raise ValidationFailure(
                    "uvicorn exited before readiness: "
                    + redact_text(self.output, [self.database_url, self.cursor_secret])
                )
            try:
                with httpx.Client(timeout=1.0) as client:
                    live = client.get(f"{self.base_url}/health/live")
                    ready = client.get(f"{self.base_url}/health/ready")
                if live.status_code == 200 and ready.status_code == 200:
                    return
                last_error = f"live={live.status_code}, ready={ready.status_code}"
            except httpx.HTTPError as exc:
                last_error = type(exc).__name__
            time.sleep(0.2)
        raise ValidationFailure(f"uvicorn did not become ready ({last_error or 'timeout'})")

    def _collect_output(self) -> None:
        if self.process is None or self.process.stdout is None:
            return
        if self.process.poll() is not None:
            self.output = self.process.stdout.read() or self.output

    def _stop(self) -> None:
        if self.process is None:
            return
        if self.process.poll() is None:
            self.process.terminate()
            try:
                self.process.wait(timeout=10)
            except subprocess.TimeoutExpired:
                self.process.kill()
                self.process.wait(timeout=5)
        self._collect_output()

    def __exit__(self, _exc_type, _exc_value, _traceback) -> None:
        self._stop()


def rest_error(client: httpx.Client, corpus: str, cursor: str) -> dict[str, Any]:
    response = client.get(
        "/api/memory/feed",
        params={"corpus": corpus, "page_size": PAGE_SIZE_LIVE, "cursor": cursor},
    )
    body = response.json()
    detail = body.get("detail", {}) if isinstance(body, dict) else {}
    code = detail.get("code") if isinstance(detail, dict) else None
    return {"status_code": response.status_code, "code": code}


def rest_traversal(base_url: str, corpus: str) -> tuple[list[dict[str, Any]], list[int], list[str]]:
    items: list[dict[str, Any]] = []
    page_sizes: list[int] = []
    cursors: list[str] = []
    cursor: str | None = None
    with httpx.Client(base_url=base_url, timeout=15.0) as client:
        for _ in range(100):
            params: dict[str, Any] = {"corpus": corpus, "page_size": PAGE_SIZE_LIVE}
            if cursor is not None:
                params["cursor"] = cursor
            response = client.get("/api/memory/feed", params=params)
            require(response.status_code == 200, f"REST feed returned HTTP {response.status_code}")
            body = response.json()
            require(body.get("corpus") == corpus, "REST response corpus mismatch")
            page_items = [logical_item(item) for item in body.get("items", [])]
            items.extend(page_items)
            page_sizes.append(len(page_items))
            if not body.get("has_more"):
                require(body.get("next_cursor") is None, "final REST page exposed a cursor")
                break
            cursor = require_cursor(body.get("next_cursor"), "REST continuation cursor missing")
            cursors.append(cursor)
        else:
            raise ValidationFailure("REST feed exceeded the page guard")
    return items, page_sizes, cursors


def sdk_error(base_url: str, corpus: str, cursor: str) -> str:
    from mindpalace_sdk import MemoryClientError, MindPalace

    client = MindPalace(base_url=base_url, timeout=15.0)
    try:
        client.memory.feed(corpus=corpus, page_size=PAGE_SIZE_LIVE, cursor=cursor)
    except MemoryClientError as exc:
        return exc.code
    raise ValidationFailure("SDK accepted an invalid cursor")


def sdk_traversal(base_url: str, corpus: str) -> tuple[list[dict[str, Any]], list[int], list[str]]:
    from mindpalace_sdk import MindPalace

    client = MindPalace(base_url=base_url, timeout=15.0)
    items: list[dict[str, Any]] = []
    page_sizes: list[int] = []
    cursors: list[str] = []
    cursor: str | None = None
    for _ in range(100):
        page = client.memory.feed(corpus=corpus, page_size=PAGE_SIZE_LIVE, cursor=cursor)
        page_items = [logical_item(item) for item in page.items]
        items.extend(page_items)
        page_sizes.append(len(page_items))
        if not page.has_more:
            require(page.next_cursor is None, "final SDK page exposed a cursor")
            break
        cursor = require_cursor(page.next_cursor, "SDK continuation cursor missing")
        cursors.append(cursor)
    else:
        raise ValidationFailure("SDK feed exceeded the page guard")
    return items, page_sizes, cursors


def cli_page(
    executable: Path,
    base_url: str,
    corpus: str,
    cursor: str | None,
    env: dict[str, str],
) -> dict[str, Any]:
    command = [
        str(executable),
        "memory",
        "feed",
        "--corpus",
        corpus,
        "--page-size",
        str(PAGE_SIZE_LIVE),
        "--base-url",
        base_url,
    ]
    if cursor is not None:
        command.extend(["--cursor", cursor])
    result = run_checked(
        command,
        env,
        timeout=45,
        sensitive=[
            env["DATABASE_URL"],
            env["MIND_PALACE_CURSOR_SECRET"],
            cursor or "",
        ],
    )
    lines = [line for line in result.stdout.splitlines() if line.strip()]
    require(bool(lines), "memory feed CLI returned no JSON")
    try:
        body = json.loads(lines[-1])
    except json.JSONDecodeError as exc:
        raise ValidationFailure("memory feed CLI returned invalid JSON") from exc
    require(isinstance(body, dict), "memory feed CLI returned a non-object")
    return body


def cli_traversal(
    executable: Path, base_url: str, corpus: str, env: dict[str, str]
) -> tuple[list[dict[str, Any]], list[int], list[str]]:
    items: list[dict[str, Any]] = []
    page_sizes: list[int] = []
    cursors: list[str] = []
    cursor: str | None = None
    for _ in range(100):
        body = cli_page(executable, base_url, corpus, cursor, env)
        page_items = [logical_item(item) for item in body.get("items", [])]
        items.extend(page_items)
        page_sizes.append(len(page_items))
        if not body.get("has_more"):
            require(body.get("next_cursor") is None, "final CLI page exposed a cursor")
            break
        cursor = require_cursor(body.get("next_cursor"), "CLI continuation cursor missing")
        cursors.append(cursor)
    else:
        raise ValidationFailure("CLI feed exceeded the page guard")
    return items, page_sizes, cursors


def run_live(database_url: str, cursor_secret: str, migration_head: str) -> None:
    sync_engine = sa.create_engine(sync_url(database_url), poolclass=NullPool)
    live_fixture: Fixture | None = None
    other_fixture: Fixture | None = None
    try:
        live_fixture, other_fixture = seed_live_fixture(sync_engine)
        fingerprint = archive_fingerprint(sync_engine, [LIVE_CORPUS])
        env = child_environment(database_url, cursor_secret)
        cli_executable = ROOT / "venvmp" / "bin" / "mindpalace"
        require(cli_executable.is_file(), "venvmp/bin/mindpalace is not installed")
        with UvicornServer(database_url, cursor_secret) as server:
            base_url = server.base_url
            rest_items, rest_pages, rest_cursors = rest_traversal(base_url, LIVE_CORPUS)
            sdk_items, sdk_pages, sdk_cursors = sdk_traversal(base_url, LIVE_CORPUS)
            cli_items, cli_pages, cli_cursors = cli_traversal(
                cli_executable, base_url, LIVE_CORPUS, env
            )
            with httpx.Client(base_url=base_url, timeout=15.0) as client:
                invalid = rest_error(client, LIVE_CORPUS, "not-a-valid-cursor")
                require(
                    invalid == {"status_code": 422, "code": "invalid_cursor"},
                    "invalid cursor was not rejected",
                )
                wrong = rest_error(client, LIVE_OTHER_CORPUS, rest_cursors[0])
                require(
                    wrong == {"status_code": 422, "code": "cursor_corpus_mismatch"},
                    "wrong-corpus cursor was not rejected",
                )
            sdk_invalid = sdk_error(base_url, LIVE_CORPUS, "not-a-valid-cursor")
            sdk_wrong = sdk_error(base_url, LIVE_OTHER_CORPUS, rest_cursors[0])
            require(sdk_invalid == "invalid_cursor", "SDK invalid cursor code mismatch")
            require(sdk_wrong == "cursor_corpus_mismatch", "SDK wrong-corpus cursor code mismatch")
            require(rest_items == sdk_items == cli_items, "REST, SDK, and CLI sequences differ")
            assert_ordered_unique(rest_items, LIVE_CORPUS)
            artifact = {
                "schema_version": 1,
                "mode": "live",
                "rest": interface_summary(rest_items, rest_pages, rest_cursors),
                "sdk": interface_summary(sdk_items, sdk_pages, sdk_cursors),
                "cli": interface_summary(cli_items, cli_pages, cli_cursors),
                "equivalent": True,
                "validated_at": utc_now(),
                "archive_fingerprint": fingerprint,
                "corpus": LIVE_CORPUS,
                "cursor_validation": {
                    "rest_invalid": invalid,
                    "rest_wrong_corpus": wrong,
                    "sdk_invalid_code": sdk_invalid,
                    "sdk_wrong_corpus_code": sdk_wrong,
                },
                "reproducibility": reproducibility(
                    database_url,
                    migration_head,
                    PAGE_SIZE_LIVE,
                    {
                        "live_versions": len(live_fixture.specs),
                        "wrong_corpus_versions": len(other_fixture.specs),
                        "all_versions_event": "NEW",
                    },
                    server_port=server.port,
                ),
            }
            sensitive = [database_url, cursor_secret, *rest_cursors, *sdk_cursors, *cli_cursors]
            assert_artifact_safe(artifact, sensitive)
            write_json(OUTPUT_DIR / "live-interface-validation.json", artifact)
    finally:
        sync_engine.dispose()


class SQLTimer:
    def __init__(self) -> None:
        self.started: float | None = None
        self.samples_ms: list[float] = []

    def reset(self) -> None:
        self.started = None
        self.samples_ms = []

    def before_cursor_execute(
        self, _conn, _cursor, _statement, _parameters, _context, _executemany
    ) -> None:
        self.started = time.perf_counter()

    def after_cursor_execute(
        self, _conn, _cursor, _statement, _parameters, _context, _executemany
    ) -> None:
        if self.started is not None:
            self.samples_ms.append((time.perf_counter() - self.started) * 1000)
        self.started = None

    def take(self) -> list[float]:
        result = list(self.samples_ms)
        self.samples_ms = []
        self.started = None
        return result


class AsyncFeedRunner:
    def __init__(self, database_url: str) -> None:
        from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

        self.engine = create_async_engine(async_url(database_url), poolclass=NullPool)
        self.sessions = async_sessionmaker(self.engine, expire_on_commit=False)
        self.timer = SQLTimer()
        from sqlalchemy import event

        event.listen(
            self.engine.sync_engine, "before_cursor_execute", self.timer.before_cursor_execute
        )
        event.listen(
            self.engine.sync_engine, "after_cursor_execute", self.timer.after_cursor_execute
        )
        from api.services.memory_feed import feed

        self.feed = feed

    async def close(self) -> None:
        from sqlalchemy import event

        event.remove(
            self.engine.sync_engine, "before_cursor_execute", self.timer.before_cursor_execute
        )
        event.remove(
            self.engine.sync_engine, "after_cursor_execute", self.timer.after_cursor_execute
        )
        await self.engine.dispose()

    async def page(self, corpus: str, cursor: str | None, page_size: int):
        self.timer.reset()
        started = time.perf_counter()
        async with self.sessions() as session:
            result = await self.feed(session, corpus, cursor, page_size)
        wall_ms = (time.perf_counter() - started) * 1000
        sql_samples = self.timer.take()
        return result, {
            "wall_ms": wall_ms,
            "sql_samples_ms": sql_samples,
            "sql_ms": sum(sql_samples),
            "sql_statements": len(sql_samples),
        }

    async def traverse(
        self, corpus: str, cursor: str | None, page_size: int
    ) -> tuple[list[dict[str, Any]], dict[str, Any]]:
        self.timer.reset()
        started = time.perf_counter()
        items: list[dict[str, Any]] = []
        page_sizes: list[int] = []
        seen_cursors: set[str] = set()
        async with self.sessions() as session:
            for _ in range(10000):
                page = await self.feed(session, corpus, cursor, page_size)
                page_items = [logical_item(item) for item in page.items]
                items.extend(page_items)
                page_sizes.append(len(page_items))
                if not page.has_more:
                    break
                cursor = require_cursor(page.next_cursor, "async feed continuation cursor missing")
                require(cursor not in seen_cursors, "async feed repeated a continuation cursor")
                seen_cursors.add(cursor)
            else:
                raise ValidationFailure("async feed exceeded the page guard")
        wall_ms = (time.perf_counter() - started) * 1000
        sql_samples = self.timer.take()
        return items, {
            "wall_ms": wall_ms,
            "sql_samples_ms": sql_samples,
            "sql_ms": sum(sql_samples),
            "sql_statements": len(sql_samples),
            "page_sizes": page_sizes,
        }


def run_concurrency(database_url: str, cursor_secret: str, migration_head: str) -> None:
    sync_engine = sa.create_engine(sync_url(database_url), poolclass=NullPool)
    try:
        base = datetime(2026, 2, 1, tzinfo=timezone.utc)
        initial_specs = make_specs(CONCURRENCY_CORPUS, 30, base, equal_groups=True)
        target = seed_fixture(sync_engine, CONCURRENCY_CORPUS, initial_specs)

        async def exercise() -> dict[str, Any]:
            runner = AsyncFeedRunner(database_url)
            try:
                first, first_timing = await runner.page(
                    CONCURRENCY_CORPUS, None, PAGE_SIZE_CONCURRENCY
                )
                require(first.has_more, "concurrency fixture did not produce a continuation")
                first_items = [logical_item(item) for item in first.items]
                require(
                    len(first_items) == PAGE_SIZE_CONCURRENCY, "first concurrency page is not full"
                )
                boundary_item = first_items[-1]
                boundary_time = timestamp(boundary_item["observed_at"])
                boundary_id = boundary_item["version_id"]
                equal_id_one = f"{int(boundary_id, 16) + 1:064x}"
                equal_id_two = f"{int(boundary_id, 16) + 2:064x}"
                target_late = [
                    late_spec(
                        CONCURRENCY_CORPUS,
                        "equal-one",
                        boundary_time,
                        equal_id_one,
                        "m009-late-equal-one",
                    ),
                    late_spec(
                        CONCURRENCY_CORPUS,
                        "equal-two",
                        boundary_time,
                        equal_id_two,
                        "m009-late-equal-two",
                    ),
                    late_spec(
                        CONCURRENCY_CORPUS,
                        "later",
                        boundary_time + timedelta(seconds=10),
                        version_id_for(CONCURRENCY_CORPUS, "later", 1),
                        "m009-late-later",
                    ),
                    late_spec(
                        CONCURRENCY_CORPUS,
                        "before-cursor",
                        boundary_time - timedelta(microseconds=1),
                        version_id_for(CONCURRENCY_CORPUS, "before-cursor", 1),
                        "m009-late-before",
                    ),
                ]
                other_specs = make_specs(
                    CONCURRENCY_OTHER_CORPUS,
                    4,
                    boundary_time + timedelta(seconds=20),
                )
                other = insert_late_rows(
                    sync_engine, target, target_late, CONCURRENCY_OTHER_CORPUS, other_specs
                )
                continuation, continuation_timing = await runner.traverse(
                    CONCURRENCY_CORPUS, first.next_cursor, PAGE_SIZE_CONCURRENCY
                )
                fresh, fresh_timing = await runner.traverse(
                    CONCURRENCY_CORPUS, None, PAGE_SIZE_CONCURRENCY
                )
                other_items, other_timing = await runner.traverse(
                    CONCURRENCY_OTHER_CORPUS, None, PAGE_SIZE_CONCURRENCY
                )
                return {
                    "first_items": first_items,
                    "continuation": continuation,
                    "fresh": fresh,
                    "other": other_items,
                    "boundary_time": boundary_time,
                    "boundary_id": boundary_id,
                    "target_late": target_late,
                    "other_fixture": other,
                    "timings": {
                        "first": first_timing,
                        "continuation": continuation_timing,
                        "fresh": fresh_timing,
                        "other": other_timing,
                    },
                }
            finally:
                await runner.close()

        result = asyncio.run(exercise())
        target_rows = fetch_rows(sync_engine, CONCURRENCY_CORPUS)
        all_target_items = [
            logical_item(
                {
                    "version_id": str(row["id"]).strip(),
                    "document_id": str(row["document_id"]),
                    "corpus": CONCURRENCY_CORPUS,
                    "path": row["path"],
                    "version_number": row["version_number"],
                    "status": row["event"],
                    "observed_at": row["observed_at"],
                    "predecessor_id": (
                        str(row["predecessor_id"]).strip()
                        if row["predecessor_id"] is not None
                        else None
                    ),
                }
            )
            for row in target_rows
        ]
        boundary_time = result["boundary_time"]
        boundary_id = result["boundary_id"]
        expected_continuation = [
            item
            for item in all_target_items
            if (timestamp(item["observed_at"]), item["version_id"]) > (boundary_time, boundary_id)
        ]
        expected_fresh = all_target_items
        continuation = result["continuation"]
        fresh = result["fresh"]
        assert_ordered_unique(continuation, CONCURRENCY_CORPUS)
        assert_ordered_unique(fresh, CONCURRENCY_CORPUS)
        require(
            continuation == expected_continuation,
            "continuation did not follow live keyset semantics",
        )
        require(fresh == expected_fresh, "fresh traversal did not return the complete archive view")
        late_ids = {spec.version_id for spec in result["target_late"]}
        continuation_ids = {item["version_id"] for item in continuation}
        require(
            {spec.version_id for spec in result["target_late"][:2]}.issubset(continuation_ids),
            "equal-timestamp rows after the cursor were not continued",
        )
        require(
            result["target_late"][2].version_id in continuation_ids,
            "later-timestamp row was not continued",
        )
        require(
            result["target_late"][3].version_id not in continuation_ids,
            "row before the cursor was incorrectly continued",
        )
        assert_ordered_unique(result["other"], CONCURRENCY_OTHER_CORPUS)
        other_ids = {item["version_id"] for item in result["other"]}
        require(
            not other_ids.intersection(continuation_ids), "another corpus leaked into continuation"
        )
        require(
            len(other_ids) == len(result["other_fixture"].specs),
            "other-corpus fixture was not independently traversable",
        )
        equal_groups: dict[datetime, int] = {}
        for item in fresh:
            equal_groups[timestamp(item["observed_at"])] = (
                equal_groups.get(timestamp(item["observed_at"]), 0) + 1
            )
        require(
            any(count >= 2 for count in equal_groups.values()), "equal timestamps were not seeded"
        )
        fingerprint = archive_fingerprint(sync_engine, [CONCURRENCY_CORPUS])
        artifact = {
            "schema_version": 1,
            "mode": "concurrency",
            "validated_at": utc_now(),
            "corpus": CONCURRENCY_CORPUS,
            "other_corpus": CONCURRENCY_OTHER_CORPUS,
            "page_size": PAGE_SIZE_CONCURRENCY,
            "initial_version_count": len(initial_specs),
            "inserted_between_pages": len(result["target_late"]),
            "other_corpus_inserted_between_pages": len(result["other_fixture"].specs),
            "total_target_versions": len(fresh),
            "first_page_count": len(result["first_items"]),
            "continuation_item_count": len(continuation),
            "fresh_item_count": len(fresh),
            "continuation_page_count": len(result["timings"]["continuation"]["page_sizes"]),
            "fresh_page_count": len(result["timings"]["fresh"]["page_sizes"]),
            "archive_fingerprint": fingerprint,
            "no_duplicates": True,
            "ordered": True,
            "isolation": True,
            "expected_live_continuation": True,
            "equal_timestamp_tie_break": True,
            "late_before_cursor_excluded": True,
            "checks": {
                "continuation_sequence_sha256": sequence_digest(continuation),
                "fresh_sequence_sha256": sequence_digest(fresh),
                "boundary_sha256": hashlib.sha256(
                    f"{boundary_time.isoformat()}|{boundary_id}".encode()
                ).hexdigest(),
                "late_version_count": len(late_ids),
                "equal_timestamp_group_count": sum(count >= 2 for count in equal_groups.values()),
                "first_page_sql_ms": round(result["timings"]["first"]["sql_ms"], 6),
                "continuation_sql_ms": round(result["timings"]["continuation"]["sql_ms"], 6),
                "fresh_sql_ms": round(result["timings"]["fresh"]["sql_ms"], 6),
            },
            "reproducibility": reproducibility(
                database_url,
                migration_head,
                PAGE_SIZE_CONCURRENCY,
                {
                    "initial_versions": len(initial_specs),
                    "late_versions": len(result["target_late"]),
                    "other_corpus_versions": len(result["other_fixture"].specs),
                    "events": ["NEW"],
                },
            ),
        }
        assert_artifact_safe(artifact, [database_url, cursor_secret])
        write_json(OUTPUT_DIR / "concurrency-validation.json", artifact)
    finally:
        sync_engine.dispose()


def fixture_rows_for_benchmark(engine, corpus: str) -> list[dict[str, Any]]:
    rows = fetch_rows(engine, corpus)
    require(bool(rows), f"benchmark corpus {corpus} is empty")
    return rows


def encode_boundary_cursor(corpus: str, row: dict[str, Any]) -> str:
    from api.services.memory_feed import encode_cursor

    return encode_cursor(corpus, row["observed_at"], str(row["id"]).strip())


def normalize_plan(value: Any) -> Any:
    if isinstance(value, str):
        try:
            return json.loads(value)
        except json.JSONDecodeError:
            return value
    if isinstance(value, list):
        return [normalize_plan(item) for item in value]
    if isinstance(value, dict):
        return {str(key): normalize_plan(item) for key, item in value.items()}
    if hasattr(value, "items"):
        return {str(key): normalize_plan(item) for key, item in value.items()}
    return value


def walk_plan(value: Any):
    if isinstance(value, dict):
        for key, item in value.items():
            yield str(key), item
            yield from walk_plan(item)
    elif isinstance(value, list):
        for item in value:
            yield from walk_plan(item)


def redact_plan(value: Any, sensitive: Sequence[str]) -> Any:
    if isinstance(value, str):
        result = value
        for item in sensitive:
            if item:
                result = result.replace(item, "<redacted>")
        result = HEX_ID_RE.sub("<redacted-id>", result)
        return TIMESTAMP_LITERAL_RE.sub("'<redacted-timestamp>'::timestamp with time zone", result)
    if isinstance(value, list):
        return [redact_plan(item, sensitive) for item in value]
    if isinstance(value, dict):
        return {key: redact_plan(item, sensitive) for key, item in value.items()}
    return value


def explain_case(
    engine,
    corpus: str,
    rows: Sequence[dict[str, Any]],
    boundary: tuple[datetime, str] | None,
    label: str,
    sensitive: Sequence[str],
) -> dict[str, Any]:
    from api.services.memory_feed import build_feed_query

    with engine.connect() as connection:
        corpus_id = connection.execute(
            sa.text("SELECT id FROM corpora WHERE name = :name"), {"name": corpus}
        ).scalar_one()
        query, params = build_feed_query(str(corpus_id).strip(), boundary, PAGE_SIZE_BENCHMARK + 1)
        query_text = " ".join(query.text.split())
        normalized_query = query_text.lower()
        require("offset" not in normalized_query, f"{label} query contains OFFSET")
        require(
            "order by v.observed_at asc, v.id asc" in normalized_query,
            f"{label} query does not use the canonical keyset order",
        )
        if boundary is not None:
            require(
                "(v.observed_at, v.id) > (:observed_at, :version_id)" in normalized_query,
                f"{label} query does not use the tuple keyset predicate",
            )
        explain_sql = "EXPLAIN (ANALYZE, BUFFERS, FORMAT JSON) " + query_text
        result = connection.execute(sa.text(explain_sql), params).scalar_one()
    plan = normalize_plan(result)
    serialized = json.dumps(plan, sort_keys=True).lower()
    require("offset" not in serialized, f"{label} EXPLAIN plan contains OFFSET")
    index_names = {
        str(item) for key, item in walk_plan(plan) if key == "Index Name" and item is not None
    }
    expected_index = "idx_memory_versions_observed"
    require(
        expected_index in index_names,
        f"{label} EXPLAIN did not use {expected_index}; observed {sorted(index_names)}",
    )
    sanitized = redact_plan(plan, list(sensitive))
    return {
        "label": label,
        "boundary": "<redacted>" if boundary is not None else None,
        "keyset_predicate": boundary is not None,
        "no_offset": True,
        "index_use_observed": True,
        "index_names": sorted(index_names),
        "plan": sanitized,
        "query": "<canonical parameterized keyset query; parameters redacted>",
    }


def write_query_plans(cases: Sequence[dict[str, Any]], path: Path) -> None:
    lines = [
        "# M009 query plans",
        "",
        "These are real PostgreSQL `EXPLAIN (ANALYZE, BUFFERS, FORMAT JSON)` results",
        "for the canonical feed query at 10,000 versions. Boundary parameters and",
        "fixture identifiers are redacted; the runner asserts tuple keyset ordering,",
        "absence of `OFFSET`, and use of `idx_memory_versions_observed`.",
        "",
    ]
    for case in cases:
        lines.extend(
            [
                f"## {case['label']}",
                "",
                f"- keyset predicate: `{case['keyset_predicate']}`",
                f"- no OFFSET: `{case['no_offset']}`",
                f"- index use observed: `{case['index_use_observed']}`",
                f"- indexes: `{', '.join(case['index_names'])}`",
                "",
                "```json",
                json.dumps(case["plan"], indent=2, sort_keys=True, ensure_ascii=False),
                "```",
                "",
            ]
        )
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(path.name + ".tmp")
    temporary.write_text("\n".join(lines), encoding="utf-8")
    temporary.replace(path)


def run_benchmark(database_url: str, cursor_secret: str, migration_head: str) -> None:
    sync_engine = sa.create_engine(sync_url(database_url), poolclass=NullPool)
    try:
        results: dict[str, dict[str, Any]] = {}
        fixture_manifest: list[dict[str, Any]] = []
        for size, corpus in BENCHMARK_CORPUSES.items():
            base = datetime(2026, 3, 1, tzinfo=timezone.utc) + timedelta(days=size // 1000)
            specs = make_specs(corpus, size, base)
            fixture = seed_fixture(sync_engine, corpus, specs)
            rows = fixture_rows_for_benchmark(sync_engine, corpus)
            require(len(rows) == size, f"benchmark fixture {size} has {len(rows)} versions")
            middle_index = size // 2 - 1
            tail_index = size - PAGE_SIZE_BENCHMARK - 1
            middle_cursor = encode_boundary_cursor(corpus, rows[middle_index])
            tail_cursor = encode_boundary_cursor(corpus, rows[tail_index])

            async def measure_size(
                current_corpus: str = corpus,
                current_middle_cursor: str = middle_cursor,
                current_tail_cursor: str = tail_cursor,
                current_size: int = size,
                current_fixture: Fixture = fixture,
            ) -> dict[str, Any]:
                corpus = current_corpus
                middle_cursor = current_middle_cursor
                tail_cursor = current_tail_cursor
                size = current_size
                fixture = current_fixture
                runner = AsyncFeedRunner(database_url)
                try:
                    cold_full, cold_metric = await runner.traverse(
                        corpus, None, PAGE_SIZE_BENCHMARK
                    )
                    require(len(cold_full) == size, "cold benchmark traversal returned wrong count")
                    for _ in range(BENCHMARK_WARMUPS):
                        await runner.page(corpus, None, PAGE_SIZE_BENCHMARK)
                        await runner.page(corpus, middle_cursor, PAGE_SIZE_BENCHMARK)
                        await runner.page(corpus, tail_cursor, PAGE_SIZE_BENCHMARK)
                        await runner.traverse(corpus, None, PAGE_SIZE_BENCHMARK)
                    samples: dict[str, dict[str, list[Any]]] = {
                        "first": {"wall": [], "sql": [], "rows": []},
                        "middle": {"wall": [], "sql": [], "rows": []},
                        "tail": {"wall": [], "sql": [], "rows": []},
                        "full": {"wall": [], "sql": [], "rows": []},
                    }
                    for _ in range(BENCHMARK_ITERATIONS):
                        first, first_metric = await runner.page(corpus, None, PAGE_SIZE_BENCHMARK)
                        middle, middle_metric = await runner.page(
                            corpus, middle_cursor, PAGE_SIZE_BENCHMARK
                        )
                        tail, tail_metric = await runner.page(
                            corpus, tail_cursor, PAGE_SIZE_BENCHMARK
                        )
                        full, full_metric = await runner.traverse(corpus, None, PAGE_SIZE_BENCHMARK)
                        require(
                            len(first.items) == PAGE_SIZE_BENCHMARK, "first benchmark page is short"
                        )
                        require(
                            len(middle.items) == PAGE_SIZE_BENCHMARK,
                            "middle benchmark page is short",
                        )
                        require(
                            len(tail.items) == PAGE_SIZE_BENCHMARK, "tail benchmark page is short"
                        )
                        require(
                            len(full) == size, "full benchmark traversal returned the wrong count"
                        )
                        require(
                            len({item["version_id"] for item in full}) == size,
                            "full benchmark traversal contains duplicates",
                        )
                        samples["first"]["wall"].append(first_metric["wall_ms"])
                        samples["first"]["sql"].append(first_metric["sql_ms"])
                        samples["first"]["rows"].append(len(first.items))
                        samples["middle"]["wall"].append(middle_metric["wall_ms"])
                        samples["middle"]["sql"].append(middle_metric["sql_ms"])
                        samples["middle"]["rows"].append(len(middle.items))
                        samples["tail"]["wall"].append(tail_metric["wall_ms"])
                        samples["tail"]["sql"].append(tail_metric["sql_ms"])
                        samples["tail"]["rows"].append(len(tail.items))
                        samples["full"]["wall"].append(full_metric["wall_ms"])
                        samples["full"]["sql"].append(full_metric["sql_ms"])
                        samples["full"]["rows"].append(full_metric["page_sizes"])
                    return {
                        "samples": samples,
                        "fixture": fixture,
                        "cold_start": {
                            "wall_ms": round(cold_metric["wall_ms"], 6),
                            "sql_execution_ms": round(cold_metric["sql_ms"], 6),
                        },
                    }
                finally:
                    await runner.close()

            measured = asyncio.run(measure_size())
            sample_data = measured["samples"]
            traversal_results: dict[str, Any] = {}
            for name in ("first", "middle", "tail", "full"):
                wall_samples = sample_data[name]["wall"]
                sql_samples = sample_data[name]["sql"]
                require(
                    len(wall_samples) >= BENCHMARK_ITERATIONS, "insufficient benchmark iterations"
                )
                require(
                    all(isinstance(value, (int, float)) for value in wall_samples), "invalid timing"
                )
                row_counts = list(sample_data[name]["rows"])
                page_counts = (
                    [count for iteration in row_counts for count in iteration]
                    if name == "full"
                    else row_counts
                )
                traversal_results[name] = {
                    "iterations": len(wall_samples),
                    "rows_per_page": PAGE_SIZE_BENCHMARK,
                    "observed_rows_per_page": {
                        "min": min(page_counts),
                        "max": max(page_counts),
                    },
                    "page_count": (len(page_counts) // len(wall_samples) if name == "full" else 1),
                    "wall_ms": timing_summary(wall_samples),
                    "sql_execution_ms": timing_summary(sql_samples),
                    "sql_statements_per_iteration": (
                        2
                        if name != "full"
                        else 2 * ((size + PAGE_SIZE_BENCHMARK - 1) // PAGE_SIZE_BENCHMARK)
                    ),
                }
            require(
                traversal_results["full"]["observed_rows_per_page"]["max"] == PAGE_SIZE_BENCHMARK,
                "full benchmark page size is not exact",
            )
            require(
                traversal_results["full"]["page_count"]
                == (size + PAGE_SIZE_BENCHMARK - 1) // PAGE_SIZE_BENCHMARK,
                "full benchmark page count is not exact",
            )
            results[str(size)] = {
                "version_count": size,
                "corpus": corpus,
                "page_size": PAGE_SIZE_BENCHMARK,
                "cold_start_full": measured["cold_start"],
                "traversals": traversal_results,
            }
            fixture_manifest.append(
                {
                    "version_count": size,
                    "corpus": corpus,
                    "corpus_id_sha256": hashlib.sha256(fixture.corpus_id.encode()).hexdigest(),
                    "event": "NEW",
                    "path_prefix": f"m009/{corpus}/",
                }
            )

        with sync_engine.begin() as connection:
            connection.execute(sa.text("ANALYZE memory_versions"))
            connection.execute(sa.text("ANALYZE memory_documents"))
        plan_cases: list[dict[str, Any]] = []
        plan_corpus = BENCHMARK_CORPUSES[10000]
        plan_rows = fixture_rows_for_benchmark(sync_engine, plan_corpus)
        sensitive = [database_url, cursor_secret, plan_corpus]
        sensitive.extend(str(row["id"]).strip() for row in plan_rows)
        middle_index = len(plan_rows) // 2 - 1
        tail_index = len(plan_rows) - PAGE_SIZE_BENCHMARK - 1
        plan_cases.append(
            explain_case(
                sync_engine,
                plan_corpus,
                plan_rows,
                None,
                "first",
                sensitive,
            )
        )
        plan_cases.append(
            explain_case(
                sync_engine,
                plan_corpus,
                plan_rows,
                (
                    plan_rows[middle_index]["observed_at"],
                    str(plan_rows[middle_index]["id"]).strip(),
                ),
                "middle",
                sensitive,
            )
        )
        plan_cases.append(
            explain_case(
                sync_engine,
                plan_corpus,
                plan_rows,
                (plan_rows[tail_index]["observed_at"], str(plan_rows[tail_index]["id"]).strip()),
                "tail",
                sensitive,
            )
        )
        write_query_plans(plan_cases, OUTPUT_DIR / "query-plans.md")
        combined_fingerprint = archive_fingerprint(sync_engine, list(BENCHMARK_CORPUSES.values()))
        artifact = {
            "schema_version": 1,
            "mode": "benchmark",
            "validated_at": utc_now(),
            "page_size": PAGE_SIZE_BENCHMARK,
            "warmup_iterations": BENCHMARK_WARMUPS,
            "measured_iterations": BENCHMARK_ITERATIONS,
            "fixtures": fixture_manifest,
            "results": results,
            "archive_fingerprint": combined_fingerprint,
            "query_plans": {
                "path": "docs/m009/query-plans.md",
                "cases": [case["label"] for case in plan_cases],
                "keyset_asserted": True,
                "offset_free": True,
                "index_asserted": "idx_memory_versions_observed",
            },
            "reproducibility": reproducibility(
                database_url,
                migration_head,
                PAGE_SIZE_BENCHMARK,
                {
                    "version_counts": [100, 1000, 10000],
                    "fixture_event": "NEW",
                    "cold_start_recorded": True,
                    "warmup_iterations": BENCHMARK_WARMUPS,
                    "measured_iterations": BENCHMARK_ITERATIONS,
                },
            ),
        }
        assert_artifact_safe(artifact, [database_url, cursor_secret])
        write_json(OUTPUT_DIR / "scale-benchmark.json", artifact)
    finally:
        sync_engine.dispose()


def parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Validate the M009 memory feed against a disposable PostgreSQL database."
    )
    parser.add_argument(
        "mode",
        nargs="?",
        choices=("live", "concurrency", "benchmark", "all"),
        default="all",
        help="validation mode (default: all)",
    )
    parser.add_argument(
        "--mode",
        dest="mode_option",
        choices=("live", "concurrency", "benchmark", "all"),
        help="alternative spelling for the positional mode",
    )
    args = parser.parse_args(argv)
    if args.mode_option is not None:
        args.mode = args.mode_option
    return args


def run_selected(
    mode: str, database: DisposableDatabase, cursor_secret: str, migration_head: str
) -> None:
    if mode in {"live", "all"}:
        run_live(database.url, cursor_secret, migration_head)
    if mode in {"concurrency", "all"}:
        run_concurrency(database.url, cursor_secret, migration_head)
    if mode in {"benchmark", "all"}:
        run_benchmark(database.url, cursor_secret, migration_head)


def main(argv: Sequence[str] | None = None) -> int:
    ensure_project_runtime(argv)
    args = parse_args(argv)
    database = DisposableDatabase(source_database_url())
    cursor_secret = secrets.token_urlsafe(48)
    failed = False
    try:
        database.create()
        os.environ["DATABASE_URL"] = database.url
        os.environ["MIND_PALACE_CURSOR_SECRET"] = cursor_secret
        migration_head = run_migrations(database.url, cursor_secret)
        run_selected(args.mode, database, cursor_secret, migration_head)
    except Exception as exc:
        failed = True
        print(
            "M009 release validation failed: "
            + redact_text(exc, [database.url, cursor_secret, source_database_url()]),
            file=sys.stderr,
        )
    finally:
        try:
            database.drop()
        except Exception as exc:
            failed = True
            print(
                "M009 release validation cleanup failed: "
                + redact_text(exc, [database.url, cursor_secret, source_database_url()]),
                file=sys.stderr,
            )
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
