"""Profile one `ingest-repo` run, phase by phase.

    python3 scripts/benchmark/ingest_profile.py \
        --corpus mind-palace=docs \
        --corpus cv-embed=/mnt/Warehouse/projects/cv-embed/docs

Every phase is timed where the work happens, so the numbers add up to the wall
clock instead of describing it. The phases are:

    connection setup      the PostgreSQL connections the run opened
    chunk embeddings      the ONNX forwards over every chunk's full text
    claim representations  the ONNX forwards over every claim's lead clause
    claims and evidence    the archive rows for the claims
    claim vector rows      the `memory_claim_embeddings` rows
    chunk rows             the `chunks` rows

The corpora are disposable. The run writes into a corpus whose name begins
with `_profile_`, never into the name on the command line, and deletes it when
the run ends. `--reset` used to erase the corpus the command line named, so a
benchmark run erased the live `mind-palace` and `cv-embed` corpora outright.
The name on the command line is a report label; the corpus it writes into is
derived from it, and a label that already carries the prefix is used as it
stands:

    --corpus mind-palace=docs     writes into `_profile_mind_palace`
    --corpus _profile_mp=docs     writes into `_profile_mp`

`--keep` leaves the disposable corpora behind, for a run whose output is
wanted after the script exits.
"""

from __future__ import annotations

import argparse
import asyncio
import os
import re
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

#: Every corpus this script writes into carries this prefix. It is what makes
#: the corpus disposable, and it is what a reader of the database sees.
DISPOSABLE_PREFIX = "_profile_"


def _disposable_name(label: str) -> str:
    """The corpus name this script writes `label` into.

    A label that already carries the prefix is used as it stands, so a caller
    who wants `_profile_mp` or `_profile_cv` can say so. Anything else is
    turned into one, because the label is a report label and the corpus it
    names is a scratch corpus that is deleted when the run ends.
    """
    if label.startswith(DISPOSABLE_PREFIX):
        return label
    slug = re.sub(r"[^a-z0-9]+", "_", label.casefold()).strip("_")
    return DISPOSABLE_PREFIX + (slug or "corpus")


def _instrument(timings: dict, connections: dict) -> list:
    """Wrap the phase functions so every phase is timed where it runs."""
    import inspect

    from api.services import claim_embeddings, ingestion, memory
    from api.services.db import async_engine

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

    # The connection setup, at the pool. `NullPool` opens a DB-API connection
    # per checkout, so this is where the cost of not reusing a connection
    # lands, and the count is how many connections the run opened.
    pool = async_engine.sync_engine.pool
    original_connect = pool.connect

    def timed_connect(*args, **kwargs):
        start = time.perf_counter()
        try:
            return original_connect(*args, **kwargs)
        finally:
            connections["count"] += 1
            timings["connection setup"] = timings.get("connection setup", 0.0) + (
                time.perf_counter() - start
            )

    pool.connect = timed_connect
    patches.append((pool, "connect", original_connect))
    return patches


async def _erase(names: list[str]) -> None:
    """Delete the disposable corpora the run created."""
    if not names:
        return
    from api.services.corpora import delete_corpus
    from api.services.db import session_scope

    for name in names:
        try:
            async with session_scope() as db:
                await delete_corpus(db, name)
        except Exception as exc:  # noqa: BLE001 - a cleanup failure is not a run failure
            print(f"could not delete the disposable corpus {name}: {exc}", file=sys.stderr)


async def _run(args) -> dict:
    from api.services.corpora import delete_corpus, get_or_create_corpus
    from api.services.db import session_scope
    from api.services.ingestion import IngestionService

    totals = {"wall clock": 0.0}
    per_corpus = {}
    connections = {"count": 0}
    patches = _instrument(totals, connections)
    disposable: list[str] = []
    try:
        for spec in args.corpus:
            label, _, source = spec.partition("=")
            path = Path(source).expanduser().resolve()
            if not path.is_dir():
                raise SystemExit(f"not a directory: {path}")
            name = _disposable_name(label)
            if args.reset:
                async with session_scope() as db:
                    await delete_corpus(db, name)
            async with session_scope() as db:
                corpus = await get_or_create_corpus(db, name)
            disposable.append(name)
            before = dict(totals)
            start = time.perf_counter()
            service = IngestionService()
            async with session_scope() as db:
                summary = await service.sync_repo(str(path), corpus["id"])
            elapsed = time.perf_counter() - start
            totals["wall clock"] += elapsed
            per_corpus[label] = {
                "corpus": name,
                "source": str(path),
                "seconds": round(elapsed, 2),
                "added": summary["added"],
                "changed": summary["changed"],
                "unchanged": summary["unchanged"],
                "failed": summary["failed"],
                "chunks": summary["chunk_count"],
                "claims": summary["claim_count"],
                "phases": {
                    label_: round(totals[label_] - before.get(label_, 0.0), 2)
                    for label_ in sorted(totals)
                    if label_ != "wall clock"
                },
            }
    finally:
        for owner, name, original in patches:
            setattr(owner, name, original)
        if not args.keep:
            await _erase(disposable)
    return {
        "per_corpus": per_corpus,
        "connections": connections["count"],
        "totals": {k: round(v, 2) for k, v in totals.items()},
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--corpus",
        action="append",
        required=True,
        metavar="NAME=PATH",
        help="a report label and the directory to sync into a disposable corpus",
    )
    parser.add_argument(
        "--reset",
        action="store_true",
        help="erase each disposable corpus first, so the run measures a cold ingest",
    )
    parser.add_argument(
        "--keep",
        action="store_true",
        help="leave the disposable corpora behind instead of deleting them",
    )
    args = parser.parse_args()
    url = os.getenv("DATABASE_URL")
    if not url:
        print("FAIL: set DATABASE_URL", file=sys.stderr)
        return 1
    report = asyncio.run(_run(args))
    for label, data in report["per_corpus"].items():
        print(f"\n{label}  <- {data['source']}  (corpus {data['corpus']})")
        print(
            f"  +{data['added']} ~{data['changed']} ={data['unchanged']} "
            f"!{data['failed']}, {data['chunks']} chunks, {data['claims']} claims"
        )
        for phase, seconds in data["phases"].items():
            print(f"  {phase:<24} {seconds:>7.2f} s")
        print(f"  {'wall clock':<24} {data['seconds']:>7.2f} s")
    print("\nTOTAL")
    for phase, seconds in report["totals"].items():
        print(f"  {phase:<24} {seconds:>7.2f} s")
    print(f"  {'connections opened':<24} {report['connections']:>7d}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
