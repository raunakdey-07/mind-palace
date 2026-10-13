"""Phase 4a: what does the product actually store, for three input shapes?

    python scripts/wedge/ingestion_inventory.py --junit /tmp/inventory.json

Measurement only. It creates a disposable corpus on whatever `DATABASE_URL`
points at, ingests one document of each shape, and reports what landed: claims,
passages, chunks, versions, and whether a question can be answered.

The point is to find out what ingestion costs *before* deciding whether a wedge
is worth building, because the answer decides the extractor question in
`docs/wedge-decision-note.md`.

Three shapes, because they are the three that matter:

| shape | why it is in the set |
|---|---|
| claims frontmatter | the documented, supported shape |
| plain markdown | what most documents in the wild actually are |
| a five-session conversation transcript | what a competitor ingests and we cannot |

Nothing here is applied, and nothing is written outside the corpus it creates.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
import uuid
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

#: Written against the product's own rule, which is stricter than it looks and
#: is the single most important thing this script measures.
#:
#: `api/services/memory.py:validate_source` requires BOTH that the claim text
#: appears verbatim in the archived chunk ("semantic inference is not supported")
#: AND that the `evidence` quote is an exact substring of one. Two drafts of
#: this document were rejected before the third was accepted -- the first because
#: a paraphrased quote is not a substring, the second because a claim sentence
#: that reads better than the document's own wording is not in the document. The
#: rule is doing exactly what it was built to do; the cost is authoring.
CLAIMS_DOC = """---
title: "Checkout rewrite"
document_type: decision
status: Accepted
claims:
  - key: checkout.primary_datastore
    value: "PostgreSQL"
    claim: "The checkout service uses PostgreSQL."
    evidence: "The checkout service uses PostgreSQL."
  - key: checkout.retry_policy
    value: "two attempts"
    claim: "Checkout retries a failed payment twice before giving up."
    evidence: "Checkout retries a failed payment twice before giving up."
---

# Checkout rewrite

The checkout service uses PostgreSQL. We moved it in March.

Checkout retries a failed payment twice before giving up. The third failure
surfaces to the customer as a declined card rather than a server error.
"""

PLAIN_DOC = """# Runbook: rotating the signing key

The signing key lives in the secrets manager under `mp/signing/current`. To
rotate it, mint a new key, publish it alongside the old one, wait for every
replica to reload, then revoke the old key. Revoking first breaks every
outbound request until each replica reloads, which is why the order matters.

Rotation is quarterly. The last two rotations were done by hand and both took
an engineer out for an afternoon.
"""

TRANSCRIPT = """# Support conversation log

## Session 1 -- 2026-03-02

**User:** Our checkout keeps timing out on the payment step. It started this morning.

**Agent:** I can see the payment gateway returning 504s. The checkout service is
holding the connection open until the gateway gives up, and our own timeout is
longer than the gateway's.

**User:** So we lose the order even though the customer paid?

**Agent:** Not always. The gateway retries internally three times before
failing, and our retry budget of two is on top of that. When the second retry
lands during a gateway retry window the order does get recorded.

## Session 2 -- 2026-03-03

**User:** Did you change anything overnight?

**Agent:** The checkout service moved to PostgreSQL on Friday. Not related to
the timeouts, but it is the change in that window.

**User:** Okay.

## Session 3 -- 2026-03-05

**User:** Any update?

**Agent:** We moved checkout onto PostgreSQL. The read replica was missing two
orders from the day before the migration, which is what you were seeing. The
migration completed at 02:00 UTC and the replica lagged behind by about four
hours.

## Session 4 -- 2026-03-06

**User:** So the timeouts were the replica?

**Agent:** No. The timeouts are the payment gateway. The replica lag is a
separate problem that made the dashboard wrong. We have raised the gateway
timeout from 30s to 45s and lowered ours to 20s, so we fail first and can retry
inside the gateway's window.

## Session 5 -- 2026-03-09

**User:** Stable since Friday?

**Agent:** Yes. Checkout retries a failed payment twice before giving up, and
that is the retry budget we settled on. The checkout service uses PostgreSQL
with a read replica for reporting queries only; writes go to the primary.

**User:** Thanks.
"""

SHAPES = {
    "claims-frontmatter": CLAIMS_DOC,
    "plain-markdown": PLAIN_DOC,
    "conversation-transcript": TRANSCRIPT,
}

#: One question per shape, written so a hit has to be the document's own words.
#: A question the document does not contain, asked in the document's vocabulary,
#: so lexical and semantic ranking both have something to work with.
QUESTIONS = {
    "claims-frontmatter": "Which datastore does the checkout service use?",
    "plain-markdown": "What is the correct order for rotating the signing key?",
    "conversation-transcript": "How many times does checkout retry a failed payment?",
}


async def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--out", type=Path, default=Path("/tmp/ingestion-inventory.json"))
    args = parser.parse_args()

    from api.services import ingestion, memory, memory_public
    from api.services.corpora import get_or_create_corpus
    from api.services.db import async_engine, session_scope
    from api.models.memory import MemoryRequest

    rows = []
    async with session_scope() as db:
        corpus = await get_or_create_corpus(db, f"wedge-inventory-{uuid.uuid4().hex[:12]}")

    service = ingestion.IngestionService(require_embeddings=True)

    for shape, content in SHAPES.items():
        path = f"docs/{shape}.md"

        # Counted before as well as after: the evidence table is per corpus, so a
        # running total would report this shape's passages as everything ingested
        # so far and make plain markdown look like it wrote two.
        async with session_scope() as db:
            before = len(await memory.evidence(db, corpus["id"]))

        result = await service.ingest_file(content, path=path, corpus_id=corpus["id"])

        async with session_scope() as db:
            # Evidence rows are the passages: one per authored reference, each
            # pinned to a chunk by exact quote and offsets. Read through the
            # product's own accessor rather than a hand-written SELECT.
            evidence_rows = await memory.evidence(db, corpus["id"])
            current = await memory_public.execute("current", MemoryRequest(corpus=corpus["name"]))

        this_shape = evidence_rows[before:]
        quotes = [row["quote"] for row in this_shape]
        claims = [c for c in current.current_memories if c.path == path]

        question = QUESTIONS[shape]
        async with session_scope() as db:
            recalled = await memory_public.execute(
                "query", MemoryRequest(corpus=corpus["name"], query=question)
            )

        answers = [c.claim for c in recalled.current_memories if c.path == path]
        rows.append(
            {
                "shape": shape,
                "path": path,
                "claims_written": result.get("claim_count"),
                "chunks_written": result.get("chunk_count"),
                "warnings": result.get("warnings") or [],
                "passages_stored": len(quotes),
                "claims_returned_by_current": len(claims),
                "chunks_stored": result.get("chunk_count"),
                "claim_texts": [c.claim for c in claims],
                "quotes": quotes,
                "question": question,
                "recall_answered": bool(answers),
                "recall_top_claim": answers[0] if answers else None,
                "recall_correct": bool(answers and _matches(shape, question, answers[0], quotes)),
            }
        )
        print(
            f"{shape:26} claims={result.get('claim_count')} "
            f"chunks={result.get('chunk_count')} passages={len(quotes)} "
            f"recall={'answered' if answers else 'abstained'}"
        )
        for warning in result.get("warnings") or []:
            print(f"{'':26}   warning: {warning}")

    await async_engine.dispose()
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(json.dumps(rows, indent=2, default=str) + "\n")
    print(f"\nwrote {args.out}")
    return 0


def _matches(shape: str, question: str, answer: str, quotes: list[str]) -> bool:
    """Is the answer's own wording in the document? Deliberately crude.

    A claim cannot be a correct answer unless its evidence quote really does
    contain the words, so this asks whether the answer's distinguishing tokens
    appear in the stored quotes. It is a check on the *ingestion* question --
    did anything retrievable get stored at all -- not a benchmark.
    """
    stop = {
        "the",
        "a",
        "an",
        "of",
        "to",
        "for",
        "does",
        "do",
        "is",
        "are",
        "and",
        "what",
        "which",
        "how",
        "many",
        "times",
        "it",
        "in",
        "on",
        "we",
        "our",
        "with",
        "that",
        "this",
        "was",
        "has",
        "have",
        "be",
        "at",
        "by",
        "from",
    }
    words = {
        w
        for w in "".join(c if c.isalnum() else " " for c in answer.lower()).split()
        if len(w) > 3 and w not in stop
    }
    if not words:
        return False
    blob = " ".join(quotes).lower()
    return words <= set("".join(c if c.isalnum() else " " for c in blob).split())


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
