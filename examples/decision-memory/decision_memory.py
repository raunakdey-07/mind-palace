#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
"""A team's decisions, remembered and defensible. The whole product in one file.

    python examples/decision-memory/decision_memory.py

Nothing here is Mind Palace specific plumbing: every step is one call a real
application would make. The output is the point -- an answer, the basis for it,
the history behind it, and a receipt that can be checked later by someone with no
access to this machine.

Requires a Mind Palace database (`mindpalace init` prints the commands) and no
embedding model, no API key and no LLM.
"""

from __future__ import annotations

import json
import sys
import tempfile
from pathlib import Path
from uuid import uuid4

# Keep the example runnable from a checkout without installing the package.
sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from mindpalace_sdk import MindPalace  # noqa: E402

CORPUS_PREFIX = "decision-memory"


def heading(text: str) -> None:
    print(f"\n{'=' * 72}\n{text}\n{'=' * 72}")


def main() -> int:
    client = MindPalace()
    # A fresh corpus per run, so the chain below is always this run's. Run it twice
    # and you will see the earlier run's decisions superseded by this run's, which
    # is memory behaving correctly.
    corpus = f"{CORPUS_PREFIX}-{uuid4().hex[:8]}"
    print(f"corpus: {corpus}")

    heading("1. REMEMBER — a decision becomes memory, with evidence")
    # `--key` in the CLI, `key=` here: it is how you tell Mind Palace that two
    # statements are about the same fact. Remembering the same key again is how a
    # decision changes.
    first = client.remember(
        "We will use PostgreSQL as the primary datastore.",
        corpus=corpus,
        key="decision.datastore",
    )
    print(f"recorded  {first.event:9} version {first.version_id[:12]}  {first.path}")

    # A client that lost the response and retried gets the same identity back, not
    # a second copy. Identity is derived from content, so this is safe to do.
    retry = client.remember(
        "We will use PostgreSQL as the primary datastore.",
        corpus=corpus,
        key="decision.datastore",
    )
    print(
        f"retried   {retry.event:9} version {retry.version_id[:12]}  "
        f"unchanged={retry.unchanged}"
    )

    heading("2. RECALL — an answer, with where it came from and when it was true")
    answer = client.recall("Which datastore did we choose?", corpus=corpus)
    for memory in answer.current_memories:
        print(f"\n  {memory.claim}")
        print(f"    source: {memory.path} @ {memory.version_id[:12]}")
        print(f"    status: {memory.status}")
    if answer.constraints:
        # Absence is an answer. Mind Palace records what it was told, so an
        # unfamiliar question abstains rather than inventing one.
        print(f"\n  no answer: {', '.join(answer.constraints)}")

    heading("3. EXPLAIN — why this answer, on the record")
    explained = client.explain("Which datastore did we choose?", corpus=corpus)
    receipt = explained.receipt["receipts"][0]
    print(f"  claim:    {receipt['answer']['claim']}")
    for row in receipt["evidence"]:
        print(f"  evidence: {receipt['source']['path']}")
        print(f'            "{row["text"]}"')
        print(
            f"            characters {row['start_offset']}-{row['end_offset']} "
            f"of version {receipt['source']['document_version_id'][:12]}"
        )
    print(f"  digest:   {explained.receipt['memory_pack_digest'][:32]}…")

    heading("4. CHANGE — the decision is revised, and the old one is superseded")
    revised = client.remember(
        "We will use CockroachDB as the primary datastore.",
        corpus=corpus,
        key="decision.datastore",
    )
    print(f"recorded  {revised.event:9} version {revised.version_id[:12]}")

    history = client.history("decision.datastore", corpus=corpus)
    print("\n  the chain, oldest first:")
    for change in history.changes:
        after = change.current[0].claim if change.current else "(removed)"
        if not change.previous:
            print(f"    {change.observed_at:%Y-%m-%d %H:%M}  recorded  {after}")
        else:
            before = change.previous[0].claim
            print(f"    {change.observed_at:%Y-%m-%d %H:%M}  {before}")
            print(f"                  superseded by  {after}")

    heading("5. TIME TRAVEL — what was true then is still readable")
    first_seen = history.changes[0].observed_at
    then = client.recall("Which datastore did we choose?", corpus=corpus, as_of=first_seen)
    for memory in then.current_memories:
        print(f"  as of {first_seen:%Y-%m-%d %H:%M}  {memory.claim}")

    heading("6. RECEIPT — hand someone else something checkable")
    final = client.explain("Which datastore did we choose?", corpus=corpus)
    receipt_path = Path(tempfile.gettempdir()) / "mindpalace-receipt.json"
    receipt_path.write_text(
        json.dumps(
            {"response": final.model_dump(mode="json"), "receipt": final.receipt},
            indent=2,
            sort_keys=True,
        ),
        encoding="utf-8",
    )
    print(f"  wrote {receipt_path}")
    print(
        "  anyone can check it with: mindpalace verify "
        f"{receipt_path.name}   # no database, no model, no network"
    )

    heading("SUMMARY")
    print(
        "  remember   a statement becomes memory with exact-substring evidence\n"
        "  recall     an answer, its source, and the time it was true\n"
        "  explain    the characters that support it and what replaced it\n"
        "  history    the supersession chain, oldest first\n"
        "  receipt    the answer plus the digest of the whole response\n"
        "  verify     offline: no database, no model, no network\n"
        "\n  VERIFIED means the receipt still matches the memory it describes.\n"
        "  It does not mean the decision was correct."
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
