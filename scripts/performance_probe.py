#!/usr/bin/env python3
"""Reproducible startup and rollback-only memory-operation probes."""

from __future__ import annotations

import argparse
import asyncio
import importlib.metadata
import json
import os
import platform
import statistics
import subprocess
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
OUTPUT = ROOT / "docs" / "performance"


def _git(*args: str) -> str:
    return subprocess.check_output(["git", *args], cwd=ROOT, text=True).strip()


def _metadata() -> dict[str, Any]:
    versions = {}
    for package in (
        "fastapi",
        "sqlalchemy",
        "asyncpg",
        "sentence-transformers",
        "torch",
        "pydantic",
    ):
        try:
            versions[package] = importlib.metadata.version(package)
        except importlib.metadata.PackageNotFoundError:
            versions[package] = None
    return {
        "git_commit": _git("rev-parse", "HEAD"),
        "git_tag": _git("describe", "--tags", "--always"),
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "python": sys.version,
        "platform": platform.platform(),
        "dependencies": versions,
    }


def _cli_path() -> Path:
    local = ROOT / "venvmp" / "bin" / "mindpalace"
    if local.exists():
        return local
    return Path(sys.executable).with_name("mindpalace")


def _child_env() -> dict[str, str]:
    env = os.environ.copy()
    env.update(
        {
            "HF_HUB_OFFLINE": "1",
            "TRANSFORMERS_OFFLINE": "1",
            "TOKENIZERS_PARALLELISM": "false",
            "PYTHONDONTWRITEBYTECODE": "1",
            "PYTHONPATH": str(ROOT)
            + (os.pathsep + env["PYTHONPATH"] if env.get("PYTHONPATH") else ""),
        }
    )
    return env


def _run_child(code: str, timeout: float = 180) -> dict[str, Any]:
    started = time.perf_counter()
    proc = subprocess.run(
        [sys.executable, "-c", code],
        cwd=ROOT,
        env=_child_env(),
        text=True,
        capture_output=True,
        timeout=timeout,
    )
    lines = [line for line in proc.stdout.splitlines() if line.strip()]
    result: dict[str, Any] = {
        "returncode": proc.returncode,
        "process_wall_ms": round((time.perf_counter() - started) * 1000, 3),
    }
    if lines:
        try:
            result.update(json.loads(lines[-1]))
        except json.JSONDecodeError:
            result["stdout"] = proc.stdout[-1000:]
    if proc.returncode:
        result["stderr"] = proc.stderr[-1000:]
    return result


def run_semantic() -> dict[str, Any]:
    from api.services.embedder import Embedder

    started = time.perf_counter()
    embedder = Embedder()
    construct_ms = (time.perf_counter() - started) * 1000
    started = time.perf_counter()
    first = embedder.embed_single("cold semantic probe")
    cold_ms = (time.perf_counter() - started) * 1000
    started = time.perf_counter()
    second = embedder.embed_single("warm semantic probe")
    warm_ms = (time.perf_counter() - started) * 1000
    return {
        "mode": "semantic",
        "metadata": _metadata(),
        "construct_ms": round(construct_ms, 3),
        "cold_first_embedding_ms": round(cold_ms, 3),
        "warm_embedding_ms": round(warm_ms, 3),
        "dimension": embedder.dimension,
        "same_dimension": len(first) == len(second) == embedder.dimension,
        "model_loaded_after_use": True,
    }


def run_startup() -> dict[str, Any]:
    probes = {
        "sdk_import": (
            "import json,resource,sys,time; s=time.perf_counter(); import mindpalace_sdk; "
            "print(json.dumps({'wall_ms':(time.perf_counter()-s)*1000,"
            "'module_count':len(sys.modules),"
            "'rss_kb':resource.getrusage(resource.RUSAGE_SELF).ru_maxrss,"
            "'torch_loaded':'torch' in sys.modules,"
            "'sentence_transformers_loaded':'sentence_transformers' in sys.modules,"
            "'transformers_loaded':'transformers' in sys.modules}))"
        ),
        "api_main_import": (
            "import json,resource,sys,time; s=time.perf_counter(); import api.main; "
            "print(json.dumps({'wall_ms':(time.perf_counter()-s)*1000,"
            "'module_count':len(sys.modules),"
            "'rss_kb':resource.getrusage(resource.RUSAGE_SELF).ru_maxrss,"
            "'torch_loaded':'torch' in sys.modules,"
            "'sentence_transformers_loaded':'sentence_transformers' in sys.modules,"
            "'transformers_loaded':'transformers' in sys.modules}))"
        ),
        "mindpalace_import": (
            "import json,resource,sys,time; s=time.perf_counter(); import mindpalace; "
            "print(json.dumps({'wall_ms':(time.perf_counter()-s)*1000,"
            "'module_count':len(sys.modules),"
            "'rss_kb':resource.getrusage(resource.RUSAGE_SELF).ru_maxrss}))"
        ),
    }
    result = {"mode": "startup", "metadata": _metadata(), "processes": {}}
    for name, code in probes.items():
        result["processes"][name] = _run_child(code)
    for name, command in {
        "cli_root_help": [str(_cli_path()), "--help"],
        "cli_memory_help": [str(_cli_path()), "memory", "--help"],
        "cli_feed_help": [str(_cli_path()), "memory", "feed", "--help"],
    }.items():
        started = time.perf_counter()
        proc = subprocess.run(
            command, cwd=ROOT, env=_child_env(), text=True, capture_output=True, timeout=60
        )
        result["processes"][name] = {
            "returncode": proc.returncode,
            "wall_ms": round((time.perf_counter() - started) * 1000, 3),
            "output_bytes": len(proc.stdout),
        }
    return result


def _median(values: list[float]) -> float:
    return round(statistics.median(values), 3)


async def _operation_run(
    size: int, warmups: int, iterations: int, database_url: str
) -> dict[str, Any]:
    from sqlalchemy import event

    from api.models.memory import MemoryRequest
    from api.services.memory_benchmark import memory_benchmark_workload
    from api.services.memory_feed import feed

    async with memory_benchmark_workload(
        "eval/memory_benchmarks.yaml", embeddings="fixture", database_url=database_url
    ) as workload:
        ingest_started = time.perf_counter()
        for index in range(size):
            claim = f"Synthetic claim {index}."
            content = (
                "---\n"
                f"title: Synthetic {index:06d}\n"
                "claims:\n"
                f"  - key: synthetic.item.{index}\n"
                f"    value: {index}\n"
                f"    claim: {claim}\n"
                f"    evidence: {claim}\n"
                "---\n\n"
                f"# Synthetic {index}\n\n{claim}\n"
            )
            result = await workload.ingest(content, f"synthetic/{index:06d}.md")
            if not result.get("success"):
                raise RuntimeError(result)
        ingest_ms = (time.perf_counter() - ingest_started) * 1000

        base = MemoryRequest(corpus=workload.corpus, query="")
        current = await workload.execute("current", base)
        claim_id = current.current_memories[0].id
        snapshot = await workload.execute("snapshot", MemoryRequest(corpus=workload.corpus))
        requests = {
            "current": base,
            "history": base,
            "changes": base,
            "evidence": MemoryRequest(corpus=workload.corpus, claim_id=claim_id),
            "as-of": MemoryRequest(corpus=workload.corpus, as_of=datetime.now(timezone.utc)),
            "snapshot": MemoryRequest(corpus=workload.corpus),
            "replay": MemoryRequest(corpus=workload.corpus, snapshot_id=snapshot.snapshot.id),
            "pack": MemoryRequest(corpus=workload.corpus, budget=8000),
        }
        statements: list[str] = []
        sql_times: list[float] = []
        active: list[float] = []
        connection = workload.connection.sync_connection

        def before(_conn, _cursor, statement, _parameters, _context, _executemany):
            active.append(time.perf_counter())
            statements.append(statement)

        def after(_conn, _cursor, _statement, _parameters, _context, _executemany):
            if active:
                sql_times.append((time.perf_counter() - active.pop()) * 1000)

        event.listen(connection, "before_cursor_execute", before)
        event.listen(connection, "after_cursor_execute", after)
        operations: dict[str, Any] = {}
        try:
            for operation, request in requests.items():
                walls: list[float] = []
                sql_samples: list[float] = []
                counts: list[int] = []
                response = None
                for iteration in range(warmups + iterations):
                    statements.clear()
                    sql_times.clear()
                    started = time.perf_counter()
                    response = await workload.execute(operation, request)
                    wall_ms = (time.perf_counter() - started) * 1000
                    if iteration >= warmups:
                        walls.append(wall_ms)
                        sql_samples.extend(sql_times)
                        counts.append(len(statements))
                operations[operation] = {
                    "wall_ms_p50": _median(walls),
                    "sql_ms_p50": _median(sql_samples),
                    "statements_p50": _median([float(value) for value in counts]),
                    "response_chars": (
                        len(response.canonical_json())
                        if hasattr(response, "canonical_json")
                        else None
                    ),
                }

            walls = []
            sql_samples = []
            counts = []
            for iteration in range(warmups + iterations):
                statements.clear()
                sql_times.clear()
                started = time.perf_counter()
                async with workload.sessions() as db:
                    await feed(db, workload.corpus, None, 50)
                if iteration >= warmups:
                    walls.append((time.perf_counter() - started) * 1000)
                    sql_samples.extend(sql_times)
                    counts.append(len(statements))
            operations["feed"] = {
                "wall_ms_p50": _median(walls),
                "sql_ms_p50": _median(sql_samples),
                "statements_p50": _median([float(value) for value in counts]),
            }
        finally:
            event.remove(connection, "before_cursor_execute", before)
            event.remove(connection, "after_cursor_execute", after)
        return {
            "versions": size,
            "ingest_ms": round(ingest_ms, 3),
            "operations": operations,
        }


async def run_operations(sizes: list[int], warmups: int, iterations: int) -> dict[str, Any]:
    database_url = os.getenv("DATABASE_URL")
    if not database_url:
        raise RuntimeError("DATABASE_URL is required for the operation probe")
    if not os.getenv("MIND_PALACE_CURSOR_SECRET"):
        raise RuntimeError("MIND_PALACE_CURSOR_SECRET is required for the feed probe")
    result = {
        "mode": "operations",
        "metadata": _metadata(),
        "database": "existing PostgreSQL via memory_benchmark_workload",
        "fixture": "one unique claim per document with exact evidence",
        "warmups": warmups,
        "iterations": iterations,
        "results": {},
    }
    for size in sizes:
        print(f"running {size} versions", flush=True)
        result["results"][str(size)] = await _operation_run(size, warmups, iterations, database_url)
    return result


def write_result(result: dict[str, Any], output: Path) -> None:
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(result, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print(f"wrote {output}")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("mode", choices=("startup", "semantic", "operations"))
    parser.add_argument("--output", type=Path)
    parser.add_argument("--sizes", default="100,500,1000")
    parser.add_argument("--warmups", type=int, default=1)
    parser.add_argument("--iterations", type=int, default=3)
    args = parser.parse_args()
    if args.warmups < 0 or args.iterations < 1:
        raise SystemExit("warmups must be >= 0 and iterations must be >= 1")
    output = args.output or OUTPUT / f"{args.mode}-results.json"
    if args.mode == "startup":
        result = run_startup()
    elif args.mode == "semantic":
        result = run_semantic()
    else:
        sizes = [int(value) for value in args.sizes.split(",") if value.strip()]
        result = asyncio.run(run_operations(sizes, args.warmups, args.iterations))
    write_result(result, output)


if __name__ == "__main__":
    main()
