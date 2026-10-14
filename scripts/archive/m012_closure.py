"""M012: is the authority closure bounded by the question or by the archive?

This is a pure structural question about the relations the resolver actually
follows, so it is answered on synthetic version lists rather than by ingesting
them. Ingesting a thousand revisions of one document costs minutes and tells us
nothing extra; the closure arithmetic depends only on the shape.

Relations, derived from what `project` and `select` read:

    same authored key        conflict sides, current/historical pair
    document version list    currency: what is current is decided by the list
    claims on those versions  a change record may reference one from elsewhere

If the closure stays small as the archive grows, a relation index is viable. If
it tracks corpus size, the idea is dead and no index should be built.
"""

from __future__ import annotations

import argparse
import collections
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "scripts" / "benchmark"))


def synth(shape: str, n: int) -> list[dict]:
    """A version list with the requested shape, as raw archive rows."""
    versions: list[dict] = []
    if shape == "wide":
        for i in range(n):
            versions.append(
                {
                    "id": f"v{i}",
                    "path": f"docs/{i}.md",
                    "claims": [{"id": f"c{i}", "key": f"svc.{i}.database"}],
                }
            )
    elif shape == "deep":
        for i in range(n):
            versions.append(
                {
                    "id": f"v{i}",
                    "path": "docs/central.md",
                    "claims": [{"id": f"c{i}", "key": "central.setting"}],
                }
            )
    elif shape == "hotkey":
        for i in range(n):
            versions.append(
                {
                    "id": f"v{i}",
                    "path": f"ops/{i}.md",
                    "claims": [{"id": f"c{i}", "key": "global.policy"}],
                }
            )
    elif shape == "mixed":
        # A realistic blend: many documents, a few revised repeatedly.
        docs = max(1, n // 5)
        for i in range(n):
            path = f"docs/{i % docs}.md"
            versions.append(
                {
                    "id": f"v{i}",
                    "path": path,
                    "claims": [{"id": f"c{i}", "key": f"svc.{i % docs}.database"}],
                }
            )
    else:
        raise ValueError(shape)
    return versions


def index(versions: list[dict]):
    claims = {
        c["id"]: {**c, "version_id": v["id"], "path": v["path"]}
        for v in versions
        for c in v["claims"]
    }
    by_version = collections.defaultdict(list)
    for cid, c in claims.items():
        by_version[c["version_id"]].append(cid)
    doc_versions = collections.defaultdict(list)
    for v in versions:
        doc_versions[v["path"]].append(v["id"])
    by_key = collections.defaultdict(list)
    for cid, c in claims.items():
        by_key[c["key"]].append(cid)
    return claims, by_version, doc_versions, by_key


def closure(versions: list[dict], seed: str) -> dict:
    claims, by_version, doc_versions, by_key = index(versions)
    seen: set[str] = set()
    versions_touched: set[str] = set()
    docs_touched: set[str] = set()
    frontier = [seed]
    while frontier:
        cid = frontier.pop()
        if cid in seen or cid not in claims:
            continue
        seen.add(cid)
        c = claims[cid]
        versions_touched.add(c["version_id"])
        docs_touched.add(c["path"])
        frontier.extend(by_key.get(c["key"], []))
        for vid in doc_versions[c["path"]]:
            versions_touched.add(vid)
            frontier.extend(by_version.get(vid, []))
    return {
        "claims": len(seen),
        "versions": len(versions_touched),
        "documents": len(docs_touched),
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--sizes", default="100,1000,10000,100000")
    parser.add_argument("--out", default=str(ROOT / "docs/performance/m012-closure.json"))
    args = parser.parse_args()

    shapes = ("wide", "deep", "hotkey", "mixed")
    sizes = [int(s) for s in args.sizes.split(",")]
    rows = []
    print(
        f"{'shape':<7} {'claims':>8} {'answer':>7} {'closure c':>10} {'%':>7} "
        f"{'closure v':>10} {'closure d':>10}"
    )
    for shape in shapes:
        for n in sizes:
            versions = synth(shape, n)
            seed = versions[0]["claims"][0]["id"]
            got = closure(versions, seed)
            total = len(versions)
            rows.append(
                {
                    "shape": shape,
                    "claims": total,
                    "seed": seed,
                    **got,
                    "pct_of_claims": round(got["claims"] / total * 100, 2),
                    "pct_of_versions": round(got["versions"] / total * 100, 2),
                }
            )
            print(
                f"{shape:<7} {total:>8} {1:>7} {got['claims']:>10} "
                f"{rows[-1]['pct_of_claims']:>6.1f}% {got['versions']:>10} {got['documents']:>10}",
                flush=True,
            )

    print("\ngrowth of the closure as the archive grows 100 -> 100,000:")
    for shape in shapes:
        series = [r for r in rows if r["shape"] == shape]
        first, last = series[0], series[-1]
        factor = (last["claims"] / max(1, first["claims"])) if first["claims"] else 0
        corpus_factor = last["claims"] / max(1, first["claims"])
        print(
            f"  {shape:<7} closure {first['claims']:>5} -> {last['claims']:>6} "
            f"(x{factor:.1f}) while corpus x{corpus_factor:.1f}"
        )

    Path(args.out).write_text(
        json.dumps({"rows": rows, "sizes": sizes, "shapes": list(shapes)}, indent=2) + "\n"
    )
    print(f"\nwritten: {args.out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
