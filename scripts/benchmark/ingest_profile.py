"""Profile one `ingest-repo` run, phase by phase.

    python3 scripts/benchmark/ingest_profile.py \
        --corpus mind-palace=docs \
        --corpus cv-embed=/mnt/Warehouse/projects/cv-embed/docs \
        --reset

Every phase is timed where the work happens, so the numbers add up to the wall
clock instead of describing it. The phases are:

    chunk embeddings      the ONNX forwards over every chunk's full text
    claim representations  the ONNX forwards over every claim's lead clause
    claims and evidence    the archive rows for the claims
    claim vector rows      the `memory_claim_embeddings` rows
    chunk rows             the `chunks` rows

`--reset` erases each corpus first, so the run measures a cold ingest. Without
it a second run short-circuits on the manifest and measures nothing.
"""

from __future__ import annotations

import argparse
import asyncio
import os
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))


def _instrument(timings: dict) -> list:
    """Wrap the phase functions so every phase is timed where it runs."""
    import inspect

    from api.services import claim_embeddings, ingestion, memory

    patches: list = []

    def wrap(owner, name, label):
        original = getattr(owner, name)

        if inspect.iscoroutinefunction(original):

            async def timed(*args, **kwargs):
                start = time.perf_counter()
                try:
                    return await original(*args, **kwargs)
                finally:
                    timings[label] = timings.get(label, 0.0) + (time.perf_counter() - start)

        else:

            def timed(*args, **kwargs):
                start = time.perf_counter()
                try:
                    return original(*args, **kwargs)
                finally:
                    timings[label] = timings.get(label, 0.0) + (time.perf_counter() - start)

        setattr(owner, name, timed)
        patches.append((owner, name, original))

    wrap(ingestion.IngestionService, "_embed_records", "chunk embeddings")
    wrap(claim_embeddings, "embed_representations", "claim representations")
    wrap(claim_embeddings, "store", "claim vector rows")
    # `ingestion` imported `insert_chunks` by name, so the module attribute is
    # not the one the call site resolves.
    wrap(ingestion, "insert_chunks", "chunk rows")
    wrap(memory, "record_version", "claims and evidence")
    return patches


async def _run(args) -> dict:
    from api.services.corpora import delete_corpus, get_or_create_corpus
    from api.services.db import session_scope
    from api.services.ingestion import IngestionService

    totals = {"wall clock": 0.0}
    per_corpus = {}
    patches = _instrument(totals)
    try:
        for spec in args.corpus:
            name, _, source = spec.partition("=")
            path = Path(source).expanduser().resolve()
            if not path.is_dir():
                raise SystemExit(f"not a directory: {path}")
            if args.reset:
                async with session_scope() as db:
                    await delete_corpus(db, name)
            async with session_scope() as db:
                corpus = await get_or_create_corpus(db, name)
            before = dict(totals)
            start = time.perf_counter()
            service = IngestionService()
            async with session_scope() as db:
                summary = await service.sync_repo(str(path), corpus["id"])
            elapsed = time.perf_counter() - start
            totals["wall clock"] += elapsed
            per_corpus[name] = {
                "source": str(path),
                "seconds": round(elapsed, 2),
                "added": summary["added"],
                "changed": summary["changed"],
                "unchanged": summary["unchanged"],
                "failed": summary["failed"],
                "chunks": summary["chunk_count"],
                "claims": summary["claim_count"],
                "phases": {
                    label: round(totals[label] - before.get(label, 0.0), 2)
                    for label in sorted(totals)
                    if label != "wall clock"
                },
            }
    finally:
        for owner, name, original in patches:
            setattr(owner, name, original)
    return {"per_corpus": per_corpus, "totals": {k: round(v, 2) for k, v in totals.items()}}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--corpus",
        action="append",
        required=True,
        metavar="NAME=PATH",
        help="a corpus name and the directory to sync into it",
    )
    parser.add_argument(
        "--reset",
        action="store_true",
        help="erase each corpus first, so the run measures a cold ingest",
    )
    args = parser.parse_args()
    url = os.getenv("DATABASE_URL")
    if not url:
        print("FAIL: set DATABASE_URL", file=sys.stderr)
        return 1
    report = asyncio.run(_run(args))
    for name, data in report["per_corpus"].items():
        print(f"\n{name}  <- {data['source']}")
        print(
            f"  +{data['added']} ~{data['changed']} ={data['unchanged']} "
            f"!{data['failed']}, {data['chunks']} chunks, {data['claims']} claims"
        )
        for label, seconds in data["phases"].items():
            print(f"  {label:<24} {seconds:>7.2f} s")
        print(f"  {'wall clock':<24} {data['seconds']:>7.2f} s")
    print("\nTOTAL")
    for label, seconds in report["totals"].items():
        print(f"  {label:<24} {seconds:>7.2f} s")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
