# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 Raunak Dey

"""Host calibration probe: was this machine quiet when we measured?

On this host wall-clock numbers move by ~2.5x purely with background load, and a
pure-Python stage inflates by the same factor as a database stage. That makes an
artifact unfalsifiable: a regression and a busy box look identical.

This probe separates the two causes and returns an exit code, so it can gate a
measurement run the same way ``--max-load`` does:

    0  CALIBRATED   timings are trustworthy
    1  MARGINAL     mildly inflated, record but do not compare
    2  CONTENDED    do not read these numbers as a baseline

Two stages are timed:

* pure-Python fixed arithmetic. It never touches Postgres, so it moves only with
  CPU contention.
* ``SELECT 1`` round-trips through the product's own asyncpg/SQLAlchemy path, so
  it moves with database-side contention.

The verdict keys off the pure-Python probe only, because that is the one that
inflates uniformly across every stage and so invalidates a whole artifact. The
Postgres probe only names which stage moved.

Retune ROUNDS after any interpreter or CPU change:

    venvmp/bin/python -c 'import sys; sys.path.insert(0,"scripts/benchmark"); \\
        from host_calibration import _burn; import time; \\
        t=time.perf_counter(); _burn(); print(round((time.perf_counter()-t)*1000,1))'

Usage:
    PYTHONPATH=scripts/benchmark DATABASE_URL=... \\
        venvmp/bin/python scripts/benchmark/host_calibration.py [--json]
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import statistics
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "scripts" / "benchmark"))

# Fixed arithmetic work, sized for roughly 300-600 ms on an idle host. The count
# is fixed on purpose: an adaptive budget would move with the load it is meant to
# detect, and the run-to-run ratio would stop meaning anything.
ROUNDS = 4_000_000

# Idle-host reference for the pure-Python probe. Overridable per host via
# --reference-ms or MP_HOST_REFERENCE_MS; retune from the least-contended run.
BUILTIN_REFERENCE_MS = 400.0
BUILTIN_POSTGRES_REFERENCE_US = 150.0

POSTGRES_QUERIES = 50
CALIBRATED_MAX = 1.25
MARGINAL_MAX = 2.0
EXIT_CODES = {"CALIBRATED": 0, "MARGINAL": 1, "CONTENDED": 2}


def _burn(rounds: int = ROUNDS) -> int:
    """Fixed integer work. Two scalars, no containers, no imports, no I/O.

    Every value stays inside 30 bits so CPython keeps them off the heap, and the
    loop body is only arithmetic. Contention shows up as a longer wall clock and
    nothing else can.
    """
    x = 1
    acc = 0
    for _ in range(rounds):
        x = (x * 1103515245 + 12345) & 0x3FFFFFFF
        acc += x & 0xFF
    return acc


def _p50(values: list[float]) -> float:
    return statistics.median(sorted(values))


def _verdict(ratio: float) -> str:
    if ratio <= CALIBRATED_MAX:
        return "CALIBRATED"
    if ratio <= MARGINAL_MAX:
        return "MARGINAL"
    return "CONTENDED"


async def postgres_probe(queries: int = POSTGRES_QUERIES) -> list[float]:
    """Per-query round-trip time in microseconds, on the product's own path."""
    from memory_bakeoff import _async_url
    from sqlalchemy import text
    from sqlalchemy.ext.asyncio import create_async_engine
    from sqlalchemy.pool import NullPool

    url = os.getenv("MEMORY_TEST_DATABASE_URL") or os.getenv("DATABASE_URL")
    if not url:
        raise SystemExit("FAIL: set MEMORY_TEST_DATABASE_URL or DATABASE_URL")

    engine = create_async_engine(_async_url(url), poolclass=NullPool)
    samples: list[float] = []
    try:
        async with engine.connect() as conn:
            # Untimed: connection setup is not a round-trip and would otherwise
            # dominate one sample out of fifty.
            await conn.execute(text("SELECT 1"))
            for _ in range(queries):
                start = time.perf_counter()
                await conn.execute(text("SELECT 1"))
                samples.append((time.perf_counter() - start) * 1_000_000.0)
    finally:
        await engine.dispose()
    return samples


def _reference_ms(flag: float | None) -> float:
    if flag is not None:
        return flag
    env = os.getenv("MP_HOST_REFERENCE_MS")
    if env:
        return float(env)
    return BUILTIN_REFERENCE_MS


def _postgres_reference_us() -> float:
    return float(os.getenv("MP_POSTGRES_REFERENCE_US") or BUILTIN_POSTGRES_REFERENCE_US)


def _explain(
    ratio: float,
    ref_ms: float,
    py_ms: float,
    pg_us: float | None,
    pg_ref_us: float,
    verdict: str,
) -> str:
    if ratio > MARGINAL_MAX:
        moved = "pure-Python/CPU stage"
    elif ratio > CALIBRATED_MAX:
        moved = "pure-Python/CPU stage, mildly"
    elif pg_us is None:
        moved = "unknown (no Postgres reading)"
    elif pg_us > MARGINAL_MAX * pg_ref_us:
        moved = "Postgres round-trip stage"
    elif pg_us > CALIBRATED_MAX * pg_ref_us:
        moved = "Postgres round-trip stage, mildly"
    else:
        moved = "nothing, both stages sit at reference"
    if pg_us is None:
        return (
            f"{verdict}: python probe {py_ms:.0f} ms is {ratio:.2f}x the "
            f"{ref_ms:.0f} ms reference; Postgres stage not measured. Inflated by: {moved}."
        )
    return (
        f"{verdict}: python probe {py_ms:.0f} ms is {ratio:.2f}x the {ref_ms:.0f} ms "
        f"reference; postgres {pg_us:.0f} us is {pg_us / pg_ref_us:.2f}x its "
        f"{pg_ref_us:.0f} us reference. Inflated by: {moved}."
    )


def measure(samples: int, reference_ms: float | None) -> dict:
    loadavg_1m = os.getloadavg()[0]
    cores = os.cpu_count() or 1

    # Two runs per sample: the pair is the stability check. On a quiet host they
    # agree to a few percent; a wide pair means the host moved mid-probe.
    pairs: list[list[float]] = []
    for _ in range(samples):
        pair = []
        for _ in range(2):
            start = time.perf_counter()
            _burn()
            pair.append((time.perf_counter() - start) * 1000.0)
        pairs.append(pair)
    python_runs = [run for pair in pairs for run in pair]
    py_ms = _p50(python_runs)
    run_ratio = _p50([pair[1] / pair[0] for pair in pairs])

    try:
        pg_samples = asyncio.run(postgres_probe())
        pg_us = _p50(pg_samples)
        pg_error = None
    except Exception as exc:  # noqa: BLE001 - the DB probe must not sink the CPU probe
        pg_samples = []
        pg_us = None
        pg_error = f"{type(exc).__name__}: {str(exc).splitlines()[0][:120]}"

    ref_ms = _reference_ms(reference_ms)
    pg_ref_us = _postgres_reference_us()
    ratio = py_ms / ref_ms if ref_ms else float("inf")
    verdict = _verdict(ratio)

    return {
        "loadavg_1m": round(loadavg_1m, 3),
        "cores": cores,
        "per_core": round(loadavg_1m / cores, 3),
        "python_probe_ms": round(py_ms, 1),
        "postgres_probe_us": None if pg_us is None else round(pg_us, 1),
        "ratio_to_reference": round(ratio, 3),
        "verdict": verdict,
        "explanation": _explain(ratio, ref_ms, py_ms, pg_us, pg_ref_us, verdict),
        "exit_code": EXIT_CODES[verdict],
        "reference_ms": ref_ms,
        "postgres_reference_us": pg_ref_us,
        "postgres_ratio_to_reference": None if pg_us is None else round(pg_us / pg_ref_us, 3),
        "python_runs_ms": [[round(run, 1) for run in pair] for pair in pairs],
        "python_run_ratio": round(run_ratio, 3),
        "samples": samples,
        "postgres_queries": len(pg_samples),
        "postgres_probe_error": pg_error,
    }


def render(report: dict) -> str:
    first, second = report["python_runs_ms"][0]
    runs = ", ".join(f"{run:.1f}" for pair in report["python_runs_ms"] for run in pair)
    pg = (
        "not measured"
        if report["postgres_probe_us"] is None
        else f"{report['postgres_probe_us']:.1f}"
    )
    lines = [
        f"host calibration: {report['verdict']} (exit {report['exit_code']})",
        (
            f"  loadavg_1m      {report['loadavg_1m']}   cores {report['cores']}"
            f"   per_core {report['per_core']}"
        ),
        (
            f"  python probe    run1 {first:.1f} ms  run2 {second:.1f} ms"
            f"  ratio {report['python_run_ratio']}  p50 {report['python_probe_ms']} ms"
        ),
        f"  python runs     {runs}",
        f"  postgres probe  p50 {pg} us over {report['postgres_queries']} queries",
        f"  reference       {report['reference_ms']} ms -> ratio {report['ratio_to_reference']}",
        f"  {report['explanation']}",
    ]
    if report["postgres_probe_error"]:
        lines.append(f"  postgres error  {report['postgres_probe_error']}")
    return "\n".join(lines)


def self_check() -> int:
    assert _p50([3.0, 1.0, 2.0]) == 2.0
    assert _p50([4.0, 1.0, 2.0, 3.0]) == 2.5
    assert _verdict(0.5) == "CALIBRATED"
    assert _verdict(CALIBRATED_MAX) == "CALIBRATED"
    assert _verdict(1.2501) == "MARGINAL"
    assert _verdict(MARGINAL_MAX) == "MARGINAL"
    assert _verdict(2.0001) == "CONTENDED"
    assert EXIT_CODES["CALIBRATED"] == 0 and EXIT_CODES["MARGINAL"] == 1
    assert EXIT_CODES["CONTENDED"] == 2
    assert _burn(1000) == _burn(1000), "burn loop is not deterministic"
    print("self-check OK")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description="Calibration probe for host load.")
    parser.add_argument("--json", action="store_true", help="emit only the JSON object")
    parser.add_argument("--samples", type=int, default=5, help="probe samples (default 5)")
    parser.add_argument(
        "--reference-ms",
        type=float,
        default=None,
        help="idle-host reference; defaults to MP_HOST_REFERENCE_MS then the built-in value",
    )
    parser.add_argument("--self-check", action="store_true", help="run asserts and exit")
    args = parser.parse_args()

    if args.self_check:
        return self_check()

    report = measure(args.samples, args.reference_ms)
    print(json.dumps(report) if args.json else render(report))
    return report["exit_code"]


if __name__ == "__main__":
    raise SystemExit(main())
