#!/usr/bin/env python3
"""Walk the real A–G memory fixture in a PostgreSQL transaction that always rolls back.

Run from an installed checkout: python examples/memory_evaluation_demo.py
No model constructor, provider, production SQL, durable corpus, or output artifact.
The deterministic fixture embeddings are NOT evidence of retrieval quality.
"""

from __future__ import annotations

import argparse
import asyncio
import json
from pathlib import Path

from api.models.memory import MemoryRequest, MemoryResponse
from api.services.memory_benchmark import (
    PACK_BUDGETS,
    check_provenance,
    memory_benchmark_workload,
    score_expectations,
)

SPEC = Path(__file__).resolve().parents[1] / "eval/memory_benchmarks.yaml"


def require(condition: bool, message: str) -> None:
    """Keep checks enabled even when Python runs with -O."""
    if not condition:
        raise AssertionError(message)


def claims(response: MemoryResponse) -> dict:
    return {
        claim.id: claim
        for claim in (
            response.current_memories
            + response.historical_memories
            + response.uncertain_memories
            + [claim for group in response.conflicts for claim in group.claims]
            + [claim for change in response.changes for claim in change.previous + change.current]
        )
    }


def selection(response: MemoryResponse) -> dict:
    fields = (
        "current_memories",
        "historical_memories",
        "uncertain_memories",
        "changes",
        "conflicts",
        "evidence",
        "sources",
    )
    payload = response.model_dump(mode="json")
    return {field: payload[field] for field in fields}


async def walk_workload() -> None:
    print("Dispatch A–G | real authored fixture | PostgreSQL, always rolled back")
    print("Artificial deterministic fixture embeddings; NOT retrieval-quality evidence.")
    print("No model download, LLM call, persistent corpus, or report file.\n")
    async with memory_benchmark_workload(SPEC, embeddings="fixture") as workload:
        require([s["id"] for s in workload.spec["stages"]] == list("ABCDEFG"), "Expected A–G")
        require(len(workload.spec["queries"]) == 39, "Expected 39 authored queries")
        captured_bytes, captured_normalized, responses = {}, {}, {}
        checked = 0
        prior_version_count = 0
        expected_conflicts = dict(zip("ABCDEFG", (0, 0, 0, 0, 1, 0, 1)))

        for stage in workload.spec["stages"]:
            alias = stage["id"]
            await workload.apply_stage(alias)
            snapshot = workload.snapshots[alias]
            replay_request = MemoryRequest(corpus=workload.corpus, snapshot_id=snapshot.id)
            replay = await workload.execute("replay", replay_request)
            captured_bytes[alias] = replay.canonical_json().encode("utf-8")
            captured_normalized[alias] = workload.normalize(replay)
            require(replay.state.valid_at == snapshot.as_of, f"{alias}: replay validity drift")
            provenance = await check_provenance(workload, replay)
            require(provenance["passed"], f"{alias}: {provenance['errors']}")
            claim_texts = {cid: claim.claim for cid, claim in claims(replay).items()}

            # Execute each authored operation while its declared live stage actually exists.
            for query in workload.spec["queries"]:
                if query["stage"] != alias:
                    continue
                request = await workload.request_for(query)
                response = await workload.execute(query["operation"], request)
                scores = score_expectations(
                    query, response, workload.normalize(response), claim_texts
                )
                failed = [label for label, score in scores.items() if not score["passed"]]
                require(not failed, f"{query['id']}: failed labels {failed}")
                provenance = await check_provenance(workload, response)
                require(provenance["passed"], f"{query['id']}: {provenance['errors']}")
                responses[query["id"]] = response
                checked += 1

            current = await workload.execute(
                "current", MemoryRequest(corpus=workload.corpus, valid_at=snapshot.as_of)
            )
            counts = await workload.counts()
            require(len(current.conflicts) == expected_conflicts[alias], f"{alias}: conflicts")
            changes = replay.changes[prior_version_count:]
            prior_version_count = len(replay.changes)
            values = ", ".join(
                f"{claim.key}={json.dumps(claim.value, ensure_ascii=False)}"
                for claim in current.current_memories
                if claim.key in {"architecture.streaming", "architecture.kafka.partitions"}
            )
            print(
                f"{alias} | current={len(current.current_memories)} unopposed, "
                f"history={len(replay.historical_memories)}, conflicts={len(current.conflicts)}, "
                f"evidence={counts['memory_evidence']}, snapshot={alias} "
                f"({len(snapshot.version_ids)} frozen versions)"
            )
            print(f"  Current: {values}")
            for change in changes:
                print(
                    f"  Changed: {change.event} {change.path}; "
                    f"memory_changed={change.memory_changed}, {change.relationship}"
                )
            for group in current.conflicts:
                print(f"  Conflict {group.key}: " + " | ".join(c.claim for c in group.claims))

        prose = responses["prose-only-modification"].changes[-1]
        require(not prose.memory_changed, "Prose-only edit must not change memory")
        require(prose.relationship == "DOCUMENT_MODIFIED", "Prose edit is not supersession")
        storage = responses["authoritative-storage-evidence"]
        storage_claims = claims(storage)
        require(len(storage_claims) == 1, "Storage evidence must support ONE claim")
        require(len(storage.evidence) == 2, "Storage claim must retain TWO evidence references")
        require(len({e.chunk_id for e in storage.evidence}) == 2, "Expected distinct chunks")
        require(
            set(next(iter(storage_claims.values())).evidence_ids)
            == {e.id for e in storage.evidence},
            "Both references must belong to the storage claim",
        )
        print("\nEvidence | one storage claim, two exact archived references:")
        for evidence in storage.evidence:
            print(
                f"  {evidence.path} [{evidence.start_offset}:{evidence.end_offset}]: "
                f"{evidence.text}"
            )

        hostile = responses["hostile-text-is-evidence"]
        authored = next(
            q for q in workload.spec["queries"] if q["id"] == "hostile-text-is-evidence"
        )
        require(
            [e.text for e in hostile.evidence] == authored["expected_evidence"],
            "Hostile source text must remain verbatim data",
        )
        print("Hostile content | archived DATA, not executed or sent to a model:")
        print("  " + json.dumps(hostile.evidence[0].text, ensure_ascii=False))

        for alias, expected in captured_bytes.items():
            replay = await workload.execute(
                "replay",
                MemoryRequest(corpus=workload.corpus, snapshot_id=workload.snapshots[alias].id),
            )
            require(replay.canonical_json().encode("utf-8") == expected, f"{alias}: replay changed")
            require(
                workload.normalize(replay) == captured_normalized[alias],
                f"{alias}: normalized replay changed",
            )
        print(
            "\nSnapshots | A–G replay bytes equal capture bytes after all later updates/deletions."
        )
        print(
            "History | pilot Redis retained; current Kafka; "
            "deletion evidence retained (labels checked)."
        )

        full = await workload.execute(
            "replay",
            MemoryRequest(corpus=workload.corpus, snapshot_id=workload.snapshots["G"].id),
        )
        print("Budget | complete canonical JSON, Unicode characters (not tokens or bytes):")
        for budget in PACK_BUDGETS:
            request = MemoryRequest(
                corpus=workload.corpus, snapshot_id=workload.snapshots["G"].id, budget=budget
            )
            pack = await workload.execute("pack", request)
            repeated = await workload.execute("pack", request)
            require(pack.canonical_json() == repeated.canonical_json(), "Frozen pack changed")
            require(len(pack.canonical_json()) <= budget, f"Budget {budget}: overflow")
            provenance = await check_provenance(workload, pack)
            require(provenance["passed"], f"Budget {budget}: {provenance['errors']}")
            selected = set(claims(pack))
            for group in full.conflicts:
                alternatives = {c.id for c in group.claims}
                require(
                    not selected & alternatives or alternatives <= selected,
                    f"Budget {budget}: omitted a counterclaim",
                )
            require(
                pack.truncated
                == (
                    selection(pack) != selection(full)
                    or len(pack.model_copy(update={"truncated": False}).canonical_json()) > budget
                ),
                f"Budget {budget}: incorrect truncation flag",
            )
            print(
                f"  {budget:5d}: used={len(pack.canonical_json()):5d}, "
                f"claims={len(selected)}, evidence={len(pack.evidence)}, "
                f"conflicts={len(pack.conflicts)}, truncated={pack.truncated}"
            )

        counts = await workload.counts()
        expected_counts = {
            "memory_versions": 17,
            "memory_claims": 16,
            "memory_evidence": 17,
            "memory_snapshots": 7,
            "documents": 11,
            "conflicts": 1,
        }
        require(all(counts[k] == v for k, v in expected_counts.items()), f"Final counts: {counts}")
        require(
            counts["lifecycle"] == {"NEW": 11, "MODIFIED": 4, "DELETED": 1, "RESTORED": 1},
            f"Unexpected lifecycle counts: {counts['lifecycle']}",
        )
        require(checked == 39, "Not all authored queries were checked")
        print(
            "\nFinal counts | "
            + json.dumps({k: counts[k] for k in expected_counts}, sort_keys=True)
        )
    print(
        "PASS | 39 authored cases, provenance, frozen replay, and pack safety; "
        "transaction rolled back."
    )
    print("Not a retrieval benchmark, generation evaluation, or deployment-readiness result.")


async def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--timeout-seconds",
        type=int,
        default=120,
        help="workload timeout, 1–600 seconds (default 120)",
    )
    args = parser.parse_args()
    if not 1 <= args.timeout_seconds <= 600:
        parser.error("--timeout-seconds must be between 1 and 600")
    try:
        await asyncio.wait_for(walk_workload(), timeout=args.timeout_seconds)
    except TimeoutError as exc:
        raise SystemExit(
            "Demo timed out; rollback cleanup was requested. No PASS recorded."
        ) from exc


if __name__ == "__main__":
    asyncio.run(main())
