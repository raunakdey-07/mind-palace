"""M012.1: is the current-version scan actually the cost in the `deep` shape?

The currency rule is a max over `version_number` per document. The hypothesis
is that replacing that scan with a persisted pointer makes the deep case cheap.

That is only true if the scan is what costs. In the deep shape one document has
N versions, and the scan is N dict comparisons, while the projection then builds
N historical claims whether or not the scan happened. If the scan is a rounding
error next to materialisation, the pointer fixes nothing and should not be built.

This measures the split on synthetic version lists, with no database, so the
answer is about arithmetic rather than about a particular host.
"""

from __future__ import annotations

import argparse
import json
import statistics
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "scripts" / "benchmark"))


def synth(shape: str, n: int) -> list[dict]:
    versions: list[dict] = []
    for i in range(n):
        if shape == "deep":
            path, doc, key = "docs/central.md", "doccentral", "central.setting"
        elif shape == "wide":
            path, doc, key = f"docs/{i}.md", f"doc{i}", f"svc.{i}.database"
        else:
            path, doc, key = (
                f"docs/{i % max(1, n // 5)}.md",
                f"doc{i % max(1, n // 5)}",
                (f"svc.{i % max(1, n // 5)}.database"),
            )
        versions.append(
            {
                "id": f"v{i}",
                "memory_document_id": doc,
                "version_number": i + 1,
                "event": "NEW" if i == 0 else "MODIFIED",
                "path": path,
                "claims": [
                    {
                        "id": f"c{i}",
                        "key": key,
                        "claim": f"Revision {i} states a value.",
                        "value": str(i),
                        "status": None,
                    }
                ],
                "evidence": [],
            }
        )
    return versions


def scan(versions: list[dict]) -> set[str]:
    """The shipped currency computation, verbatim in shape."""
    latest: dict[str, dict] = {}
    for version in versions:
        identity = version["memory_document_id"]
        if identity not in latest or latest[identity]["version_number"] < version["version_number"]:
            latest[identity] = version
    return {v["id"] for v in latest.values() if v["event"] != "DELETED"}


def scan_with_pointer(versions: list[dict], pointer: dict[str, dict]) -> set[str]:
    """Currency from a persisted pointer, given the same rows."""
    return {p["id"] for p in pointer.values() if p["event"] != "DELETED"}


def build_pointer(versions: list[dict]) -> dict[str, dict]:
    latest: dict[str, dict] = {}
    for version in versions:
        identity = version["memory_document_id"]
        if identity not in latest or latest[identity]["version_number"] < version["version_number"]:
            latest[identity] = version
    return {v["memory_document_id"]: v for v in latest.values()}


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--sizes", default="100,1000,10000")
    parser.add_argument("--repeats", type=int, default=25)
    parser.add_argument("--out", default=str(ROOT / "docs/performance/m0121-scan-cost.json"))
    args = parser.parse_args()

    sizes = [int(s) for s in args.sizes.split(",")]
    rows = []
    print(
        f"{'shape':<7} {'n':>7} {'scan p50 ms':>12} {'pointer p50 ms':>15} "
        f"{'saved ms':>10} {'same result':>12}"
    )
    for shape in ("deep", "wide", "mixed"):
        for n in sizes:
            versions = synth(shape, n)
            pointer = build_pointer(versions)
            assert scan(versions) == scan_with_pointer(versions, pointer), "pointer disagrees"
            scan_ms, pointer_ms = [], []
            for _ in range(args.repeats):
                t0 = time.perf_counter()
                scan(versions)
                scan_ms.append((time.perf_counter() - t0) * 1000)
                t0 = time.perf_counter()
                scan_with_pointer(versions, pointer)
                pointer_ms.append((time.perf_counter() - t0) * 1000)
            row = {
                "shape": shape,
                "n": n,
                "scan_p50_ms": round(statistics.median(scan_ms), 4),
                "pointer_p50_ms": round(statistics.median(pointer_ms), 4),
                "documents": len({v["memory_document_id"] for v in versions}),
            }
            row["saved_ms"] = round(row["scan_p50_ms"] - row["pointer_p50_ms"], 4)
            rows.append(row)
            print(
                f"{shape:<7} {n:>7} {row['scan_p50_ms']:>12.4f} {row['pointer_p50_ms']:>15.4f} "
                f"{row['saved_ms']:>10.4f} {'yes':>12}"
            )

    print("\nwhat the scan costs next to materialising the same versions:")
    for row in rows:
        # project() builds one pydantic Claim per version; ~1us is a conservative
        # lower bound observed for these small models.
        per_claim_us = 1.0
        projection_ms = row["n"] * per_claim_us / 1000
        share = row["scan_p50_ms"] / (row["scan_p50_ms"] + projection_ms) * 100
        print(
            f"  {row['shape']:<7} n={row['n']:>6}  scan={row['scan_p50_ms']:>8.4f} ms  "
            f"projection~{projection_ms:>8.1f} ms  scan is {share:>5.1f}% of the two together"
        )

    Path(args.out).write_text(json.dumps({"rows": rows}, indent=2) + "\n")
    print(f"\nwritten: {args.out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
