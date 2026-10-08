# SPDX-License-Identifier: Apache-2.0
"""Use Mind Palace for a project's own decision log.

This is the dogfooding example, and it is deliberately boring: it imports nothing but
the public SDK, runs no SQL, reads no internal module, and needs no environment
variable of its own. If it needs a change to work, the SDK is not usable yet -- which
is the whole reason this file exists.

What it does:

    remember    record each decision in decisions.md as an authoritative claim
    recall      "why don't we use a vector database?"
    explain     the evidence and the history behind that answer
    history     what a decision said before it was changed
    receipt     export an answer that can be checked later, by anyone
    verify      check that receipt with no database and no model

Run it:

    pip install -e .
    export DATABASE_URL=postgresql://mpadmin:secret@localhost:5432/mindpalace
    python -m alembic -c migrations/alembic.ini upgrade head
    python examples/project-log/project_log.py

Safe to run repeatedly: the same decisions produce the same memory.
"""

from __future__ import annotations

import json
import sys
import tempfile
from pathlib import Path

# Work from a checkout as well as from an installed package. `pip install -e .` is
# the documented install and this is a no-op there; without it, the repository root
# has to be importable for `mindpalace_sdk` and the standard-library verifier to
# resolve. Deliberately the only path manipulation in the file.
sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from mindpalace_sdk import MindPalace  # noqa: E402

HERE = Path(__file__).resolve().parent
DECISIONS = HERE / "decisions.md"

#: A corpus of its own, so this example never mixes with a project's real memories.
CORPUS = "project-log"

#: The questions a contributor actually asks. Not "retrieve top-k"; these are the
#: questions that mean "someone decided this and I want to know why".
QUESTIONS = [
    "Why don't we use a dedicated vector database?",
    "Do we sign receipts?",
    "Why did we reject reciprocal rank fusion?",
    "What did we decide about the acceptance gate?",
    "How do I know a VERIFIED receipt means something?",
    "What is our incident response SLA?",  # deliberately not in the log
]


def load_decisions() -> list[dict]:
    """Read decisions.md, in the document format Mind Palace already understands.

    The same frontmatter shape `mindpalace remember --file` accepts, so a project can
    keep its decisions in a normal Markdown file next to its code and this example
    needs no parser of its own.
    """
    import yaml

    text = DECISIONS.read_text(encoding="utf-8")
    if not text.startswith("---"):
        raise SystemExit(f"{DECISIONS} must start with YAML frontmatter")
    _, frontmatter, _body = text.split("---", 2)
    metadata = yaml.safe_load(frontmatter) or {}
    return metadata.get("claims") or []


def main() -> int:
    client = MindPalace(name=CORPUS, runtime=True)

    print("=" * 74)
    print("1. RECORD — every decision becomes memory with evidence")
    print("=" * 74)
    decisions = load_decisions()
    for decision in decisions:
        written = client.memory.remember(decision["claim"], key=decision["key"])
        state = "already recorded" if written.unchanged else f"recorded ({written.event})"
        print(f"  {decision['key']:34s} {state}")
    print(f"\n  {len(decisions)} decisions in corpus '{CORPUS}'")

    print()
    print("=" * 74)
    print("2. RECALL — the question a contributor actually asks")
    print("=" * 74)
    for question in QUESTIONS:
        result = client.recall(question)
        print(f"\n  {question}")
        if not result.current_memories:
            print(f"    -> {', '.join(result.constraints) or 'no memory'}")
            print("       (nothing invented: this project never decided that)")
            continue
        for claim in result.current_memories:
            print(f"    {claim.claim}")
            print(f"      key={claim.key}  {claim.path}  status={claim.status}")

    print()
    print("=" * 74)
    print("3. EXPLAIN — why that answer, and what it replaced")
    print("=" * 74)
    explained = client.explain("Why don't we use a dedicated vector database?")
    if not explained.current_memories:
        print("  (nothing answered -- the log is empty or the corpus changed)")
        return 1
    for claim in explained.current_memories:
        print(f"  claim:    {claim.claim}")
        for evidence_id in claim.evidence_ids:
            evidence = next(e for e in explained.evidence if e.id == evidence_id)
            print(f"  evidence: {evidence.text}")
            print(
                f"            {evidence.path}, characters "
                f"{evidence.start_offset}-{evidence.end_offset} of {evidence.version_id[:12]}"
            )
    print(f"  digest:   {explained.receipt['memory_pack_digest'][:32]}…")

    print()
    print("=" * 74)
    print("4. CHANGE A DECISION — the old answer is still reachable")
    print("=" * 74)
    # A real refinement, deliberately different from the text in decisions.md so
    # this is a supersession rather than a repeat of the same write. Re-running the
    # example converges: step 1 restores the file's text, this step replaces it.
    refinement = (
        "Re-confirmed at v0.9.0: we rejected Qdrant, Weaviate and Milvus as a "
        "dedicated vector store. The authoritative projection is loaded anyway to "
        "decide what is true, so a second store would add a consistency problem and "
        "remove nothing."
    )
    updated = client.memory.remember(refinement, key="retrieval.no-vector-database")
    print(f"  recorded {updated.event} — the earlier text is superseded, not deleted")
    chain = client.memory.history(corpus=CORPUS, path=updated.path)
    for change in sorted(chain.changes, key=lambda c: c.observed_at):
        for claim in change.current:
            marker = "recorded  " if not change.previous else "superseded"
            print(f"  {change.observed_at:%Y-%m-%d %H:%M:%S}  {marker}  {claim.claim[:64]}…")

    print()
    print("=" * 74)
    print("5. RECEIPT — hand the answer to someone who has never heard of this")
    print("=" * 74)
    final = client.explain("Why don't we use a dedicated vector database?")
    path = Path(tempfile.gettempdir()) / "project-log-receipt.json"
    path.write_text(
        json.dumps(
            {"response": final.model_dump(mode="json"), "receipt": final.receipt},
            indent=2,
            sort_keys=True,
        ),
        encoding="utf-8",
    )
    print(f"  wrote {path}")
    print(f"  anyone can check it with: mindpalace verify {path.name}")
    print("  no database, no model, no network, and no Mind Palace server")

    print()
    print("=" * 74)
    print("6. RECALL IT AGAIN — the changed decision, from the same corpus")
    print("=" * 74)
    after = client.recall("Why don't we use a dedicated vector database?")
    for claim in after.current_memories:
        print(f"  {claim.claim[:96]}…")

    print()
    print("=" * 74)
    print("SUMMARY")
    print("=" * 74)
    print("  A project keeps its decisions in a Markdown file.")
    print("  Mind Palace turns them into memory you can ask questions of,")
    print("  with the evidence and the history attached, and a receipt you can hand")
    print("  to someone who cannot check your database.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
