#!/usr/bin/env python3
"""Run the evolving Dispatch demo against a dedicated, newly reserved local corpus."""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import shutil
from dataclasses import asdict
from datetime import datetime, timezone
from pathlib import Path
from tempfile import TemporaryDirectory
from time import perf_counter
from uuid import uuid4

FIXTURES = Path(__file__).resolve().parent / "evolving-project"
STREAMING = "architecture.streaming"


def check(condition, message):
    # Keep safety/evolution assertions active even under python -O.
    if not condition:
        raise AssertionError(message)


def all_claims(response):
    return (
        response.current_memories
        + response.historical_memories
        + response.uncertain_memories
        + [claim for group in response.conflicts for claim in group.claims]
        + [claim for change in response.changes for claim in change.previous + change.current]
    )


def verify_evidence(response, authored_bodies):
    evidence = {item.id: item for item in response.evidence}
    referenced = set()
    for claim in all_claims(response):
        check(bool(claim.evidence_ids), f"Claim {claim.id} has no evidence")
        for eid in claim.evidence_ids:
            check(eid in evidence, f"Dangling evidence reference: {eid}")
            item = evidence[eid]
            check(item.claim_id == claim.id, "Evidence belongs to another claim")
            check(item.version_id == claim.version_id, "Evidence version mismatch")
            check(item.path == claim.path, "Evidence path mismatch")
            check(item.text == claim.claim, "Demo evidence must equal the authored claim")
            check(
                any(item.text in body for body in authored_bodies[item.path]),
                "Evidence does not occur in an authored source body",
            )
            check(item.start_offset >= 0, "Negative evidence offset")
            check(item.end_offset - item.start_offset == len(item.text), "Invalid quote bounds")
            referenced.add(eid)
    check(referenced == set(evidence), "Unreferenced evidence in response")
    check(
        {item.version_id for item in response.sources}
        == {item.version_id for item in response.evidence},
        "Sources must derive only from retained evidence",
    )
    for item in response.evidence:
        check(
            any(
                source.version_id == item.version_id
                and source.document_id == item.document_id
                and source.path == item.path
                and source.source_hash == item.source_hash
                and source.observed_at == item.observed_at
                for source in response.sources
            ),
            "Incomplete source attribution",
        )
    # Public evidence exposes the quote and offsets, not the full archived chunk.
    # The database enforces the exact chunk slice; here we check the public links
    # and exact authored body text without claiming to re-read a private chunk.


async def reserve_corpus(name):
    # The SDK has no create-only operation or live-document inspection method.
    # Reserve via corpus management before sync; reject ALL existing names,
    # including empty live indexes with retained history. Never delete a corpus.
    from api.services.corpora import create_corpus
    from api.services.db import session_scope

    async with session_scope() as db:
        return await create_corpus(db, name, description="M005 Dispatch evolving demo")


def run(args):
    from api.models.memory import MemoryRequest
    from mindpalace_sdk import MindPalace

    MemoryRequest(corpus=args.corpus)  # Validate before any persistent write.
    check(bool(os.environ.get("DATABASE_URL")), "Set DATABASE_URL explicitly before running")
    output = args.output or FIXTURES / "runs" / args.corpus
    output = output.resolve()
    check(not output.exists(), f"Refusing to overwrite output directory: {output}")
    bodies = {}
    for source in sorted(FIXTURES.glob("stage-*/*.md")):
        body = source.read_text(encoding="utf-8").split("---", 2)[2]
        bodies.setdefault(source.name, []).append(body)

    corpus = asyncio.run(reserve_corpus(args.corpus))
    output.mkdir(parents=True, exist_ok=False)
    report = {"corpus": corpus, "started_at": datetime.now(timezone.utc).isoformat(), "calls": []}
    print(f"corpus={args.corpus} corpus_id={corpus['id']} output={output}", flush=True)
    print("Dedicated corpus reserved; do not write to it concurrently. No automatic deletion.")

    def save_report():
        (output / "report.json").write_text(
            json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
        )

    def capture(label, call):
        start = perf_counter()
        response = call()
        elapsed = round((perf_counter() - start) * 1000, 3)
        canonical = response.canonical_json()
        (output / f"{label}.json").write_text(canonical + "\n", encoding="utf-8")
        verify_evidence(response, bodies)
        result = {
            "label": label,
            "elapsed_ms": elapsed,
            "canonical_characters": len(canonical),
            "current_values": [claim.value for claim in response.current_memories],
            "historical_count": len(response.historical_memories),
            "conflict_count": len(response.conflicts),
            "evidence_count": len(response.evidence),
            "change_count": len(response.changes),
            "truncated": response.truncated,
            "state": response.state.model_dump(mode="json"),
        }
        report["calls"].append(result)
        save_report()
        print(json.dumps(result, ensure_ascii=False), flush=True)
        return response

    def streaming_current(label, expected):
        response = capture(label, lambda: mp.memory.current(query="streaming"))
        check([c.value for c in response.current_memories] == expected, f"{label}: wrong current")
        check(all(c.key == STREAMING for c in response.current_memories), "Wrong key")
        return response

    def sync(label, expected):
        start = perf_counter()
        summary = mp.sync(str(staging))
        result = {
            "label": label,
            **asdict(summary),
            "wall_ms": round((perf_counter() - start) * 1000, 3),
        }
        report["calls"].append(result)
        save_report()
        print(json.dumps(result), flush=True)
        check(summary.success and summary.failed == 0, f"{label}: sync failed")
        actual = (summary.added, summary.changed, summary.unchanged, summary.deleted)
        check(actual == expected, f"{label}: expected {expected}, received {actual}")

    try:
        start = perf_counter()
        mp = MindPalace(args.corpus, create_if_missing=False)
        report["sdk_initialization_ms"] = round((perf_counter() - start) * 1000, 3)
        with TemporaryDirectory(prefix="mindpalace-m005-") as temporary:
            staging = Path(temporary)
            for source in (FIXTURES / "stage-a").glob("*.md"):
                shutil.copyfile(source, staging / source.name)
            sync("A-sync", (3, 0, 0, 0))
            a = streaming_current("A-current", ["Redis Streams"]).current_memories[0]
            auth = capture("A-auth", lambda: mp.memory.current(query="auth"))
            check(not auth.current_memories and len(auth.conflicts) == 1, "Auth must conflict")
            check({c.value for c in auth.conflicts[0].claims} == {"OIDC", "API key"}, "Auth sides")
            check(all(c.status == "CONFLICTING" for c in auth.conflicts[0].claims), "Auth status")
            saved = capture("A-snapshot", mp.memory.snapshot)
            check(saved.snapshot is not None, "Snapshot was not created")
            cutoff = saved.snapshot.as_of
            snapshot_id = saved.snapshot.id
            report["snapshot_id"] = snapshot_id
            report["as_of"] = cutoff.isoformat()
            baseline = capture("A-replay", lambda: mp.memory.replay_snapshot(snapshot_id))
            check(baseline.canonical_json() == saved.canonical_json(), "Committed snapshot differs")
            check(baseline.state.valid_at == cutoff, "Snapshot must fix validity at saved as_of")
            sync("A-unchanged-sync", (0, 0, 3, 0))
            unchanged = capture("A-unchanged-history", mp.memory.history)
            check(len(unchanged.changes) == 3, "Unchanged sync appended history")

            claims = [a]
            for stage, value in (("B", "Kafka"), ("C", "Kafka managed")):
                shutil.copyfile(
                    FIXTURES / f"stage-{stage.lower()}" / "architecture.md",
                    staging / "architecture.md",
                )
                sync(f"{stage}-sync", (0, 1, 2, 0))
                current = streaming_current(f"{stage}-current", [value]).current_memories[0]
                check(current.supersedes_id == claims[-1].id, "Missing same-key supersession")
                check(current.observed_at > claims[-1].observed_at, "Observation order")
                claims.append(current)

            (staging / "architecture.md").unlink()
            sync("deletion-sync", (0, 0, 2, 1))
            streaming_current("deletion-current", [])
            deleted = capture("deletion-history", lambda: mp.memory.history(query="streaming"))
            check(claims[-1].id in {c.id for c in deleted.uncertain_memories}, "Deleted status")
            archived = capture("deletion-evidence", lambda: mp.memory.evidence(a.id))
            check(bool(archived.evidence), "Deletion lost original evidence")

            shutil.copyfile(FIXTURES / "stage-c" / "architecture.md", staging / "architecture.md")
            sync("restoration-sync", (1, 0, 2, 0))
            restored = streaming_current("restoration-current", ["Kafka managed"]).current_memories[
                0
            ]
            check(restored.id != claims[-1].id, "Restoration must create a new claim")
            check(restored.supersedes_id is None, "Restoration cannot supersede across tombstone")
            history = capture("final-history", lambda: mp.memory.history(query="streaming"))
            changes = capture("final-changes", lambda: mp.memory.changes(query="streaming"))
            events = changes.changes
            check(
                [e.event for e in events] == ["NEW", "MODIFIED", "MODIFIED", "DELETED", "RESTORED"],
                "Unexpected lifecycle",
            )
            check(
                [e.relationship for e in events[1:3]] == ["SUPERSEDES", "SUPERSEDES"],
                "Modifications must express supersession",
            )
            check(
                all(
                    after.predecessor_id == before.version_id
                    for before, after in zip(events, events[1:])
                ),
                "Broken predecessor chain",
            )
            check(
                all(
                    after.observed_at > before.observed_at
                    for before, after in zip(events, events[1:])
                ),
                "Non-increasing observation timestamps",
            )
            check(len({e.document_id for e in history.evidence}) == 1, "Document identity changed")
            historical = {c.id: c for c in history.historical_memories}
            check(all(historical[c.id].status == "SUPERSEDED" for c in claims[:2]), "Old status")
            check(historical[claims[-1].id].status == "UNCERTAIN", "Pre-deletion claim status")
            past = capture("final-as-of-A", lambda: mp.memory.as_of(cutoff, query="streaming"))
            check([c.id for c in past.current_memories] == [a.id], "As-of did not preserve A")
            replay = capture("final-replay-A", lambda: mp.memory.replay_snapshot(snapshot_id))
            check(replay.canonical_json() == baseline.canonical_json(), "Snapshot replay drifted")
            final_auth = capture("final-auth", lambda: mp.memory.current(query="auth"))
            check(final_auth.conflicts == auth.conflicts, "Auth conflict changed unexpectedly")

            for budget in (1024, 8000, 128000):
                pack = capture(f"final-pack-{budget}", lambda: mp.memory.pack(budget=budget))
                check(pack.budget_unit == "unicode_characters", "Wrong budget unit")
                check(len(pack.canonical_json()) <= budget, "Complete pack exceeds budget")
                for group in pack.conflicts:
                    check(
                        {c.id for c in group.claims} == {c.id for c in auth.conflicts[0].claims},
                        "Pack split conflict sides",
                    )
                if budget == 1024:
                    check(pack.truncated, "Small demo pack should be truncated")
                if budget == 128000:
                    check(not pack.truncated and bool(pack.evidence), "Large demo pack incomplete")
                    check(len(pack.conflicts) == 1, "Full pack lost auth conflict")

            report["architecture_events"] = [event.model_dump(mode="json") for event in events]
            for event in events:
                print(
                    f"event={event.event} version_id={event.version_id} "
                    f"observed_at={event.observed_at.isoformat()}"
                )
            for claim in claims + [restored]:
                print(
                    f"claim_id={claim.id} value={claim.value} "
                    f"observed_at={claim.observed_at.isoformat()}"
                )
            print(f"snapshot_id={snapshot_id} as_of={cutoff.isoformat()}")
        report["success"] = True
        print(
            "PASS: evolution, conflicts, evidence, committed replay, and complete-envelope bounds"
        )
    except Exception as exc:
        report["success"] = False
        report["error_type"] = type(exc).__name__
        raise
    finally:
        report["finished_at"] = datetime.now(timezone.utc).isoformat()
        save_report()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--corpus",
        default=f"m005-demo-{uuid4().hex}",
        help="Dedicated NEW name; all existing names are rejected (default: UUID).",
    )
    parser.add_argument(
        "--output", type=Path, help="New response archive directory; must not exist."
    )
    args = parser.parse_args()
    try:
        run(args)
    except (AssertionError, ValueError) as exc:
        parser.exit(1, f"Demo stopped: {exc}\n")
    except Exception as exc:
        # Avoid dumping DB connection details; the partial report records failure type.
        parser.exit(
            1,
            f"Demo stopped ({type(exc).__name__}). Check prerequisites, use a NEW corpus "
            "and output directory, and never delete an existing corpus to retry.\n",
        )


if __name__ == "__main__":
    main()
