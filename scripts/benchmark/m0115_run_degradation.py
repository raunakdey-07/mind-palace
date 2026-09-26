"""M011.5 driver: run the degradation phases in separate processes.

A failure mode like "the model is gone" only exists in a fresh process, because
the embedder keeps one resident model and a warm pool hides the problem. This
drives the phases the same way a real deployment would hit them, one process
each, and collects the results into one artifact.
"""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[1]
PYTHON = str(ROOT / "venvmp" / "bin" / "python")
SCRIPT = str(HERE / "m0115_degradation.py")
SCHEMA_FILE = Path("/tmp/m115_schema")


def run(action: str, *, phase: str | None = None, env: dict | None = None) -> str:
    cmd = [PYTHON, SCRIPT, action]
    if phase:
        cmd += ["--phase", phase]
    base = dict(os.environ)
    base["PYTHONPATH"] = str(HERE)
    base.setdefault("HF_HUB_OFFLINE", "1")
    if env:
        base.update(env)
    result = subprocess.run(
        cmd, capture_output=True, text=True, timeout=3600, env=base, cwd=str(ROOT)
    )
    if result.returncode != 0:
        return "FAILED: " + (
            result.stderr.strip().splitlines()[-1] if result.stderr else "no output"
        )
    return result.stdout


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--out", default=str(ROOT / "docs/performance/m0115-degradation.json"))
    args = parser.parse_args()

    url = os.environ.get("MEMORY_TEST_DATABASE_URL") or os.environ.get("DATABASE_URL")
    if not url:
        print("FAIL: set MEMORY_TEST_DATABASE_URL or DATABASE_URL", file=sys.stderr)
        return 1
    env = {"M115_URL": url}

    print("ingesting corpus once...")
    schema = run("ingest", env=env).strip().splitlines()[-1]
    SCHEMA_FILE.write_text(schema)
    env["M115_SCHEMA"] = schema
    print(f"schema: {schema}\n")

    report = {"phases": {}}

    for phase in ("full", "nomodel", "nlexical"):
        print(f"phase: {phase}")
        override = {}
        if phase == "nomodel":
            override = {"EMBEDDING_MODEL": "no-such-model/mini-nonexistent"}
        out = run("measure", phase=phase, env={**env, **override})
        try:
            report["phases"][phase] = json.loads(out)
        except json.JSONDecodeError:
            report["phases"][phase] = {"raw": out[-400:]}
        got = report["phases"][phase]
        print(
            f"  answered={got.get('answered')} correct={got.get('correct')} "
            f"abstained={got.get('abstained')} conflicts={got.get('conflicts_reported')} "
            f"failures={len(got.get('failures', []))}"
        )

    # Delete every derived row, then re-measure with the model still available.
    print("\ndestroying L2...")
    destroyed = run("destroy", env=env).strip()
    print(" ", destroyed.splitlines()[-1] if destroyed else "done")
    if destroyed.startswith("FAILED"):
        print("  destroy failed; the L2 comparison below would be vacuous")
        report["l2_invariance"] = {"error": destroyed}
        Path(args.out).write_text(json.dumps(report, indent=2, sort_keys=True) + "\n")
        return 1
    out = run("measure", phase="nol2", env=env)
    try:
        report["phases"]["nol2"] = json.loads(out)
    except json.JSONDecodeError:
        report["phases"]["nol2"] = {"raw": out[-400:]}
    got = report["phases"]["nol2"]
    print(
        f"  answered={got.get('answered')} correct={got.get('correct')} "
        f"abstained={got.get('abstained')}"
    )

    print("\npoisoning:")
    out = run("poison", env=env)
    try:
        report["poisoning"] = json.loads(out)
    except json.JSONDecodeError:
        report["poisoning"] = {"raw": out[-400:]}
    p = report["poisoning"]
    print(f"  untrusted claims authored : {p.get('untrusted_claim_count')}")
    print(f"  untrusted evidence cited : {p.get('untrusted_evidence_count')}")
    print(f"  injection text in claims : {p.get('markers_in_claims')}")
    print(f"  injection text in evidence: {p.get('markers_in_evidence')}")

    # Are the authoritative answers the same with and without L2?
    full = set(report["phases"]["full"].get("digests", []))
    nol2 = set(report["phases"]["nol2"].get("digests", []))
    report["l2_invariance"] = {
        "digests_full": len(full),
        "digests_without_l2": len(nol2),
        "identical": sorted(full) == sorted(nol2),
    }
    print(f"\ndistinct authoritative packs: full={len(full)} without L2={len(nol2)}")
    print(f"identical: {report['l2_invariance']['identical']}")

    Path(args.out).write_text(json.dumps(report, indent=2, sort_keys=True) + "\n")
    print(f"\nwritten: {args.out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
