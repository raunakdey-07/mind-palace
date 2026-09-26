"""M011: an independent consumer that only ever sees a Memory Pack.

No database, no model, no network, no Mind Palace import. The pack is handed
over as bytes and the questions are answered from the pack alone.

Each question is a different kind of consumer obligation, and each is answered
from the structured pack rather than by string-matching the corpus:

    current value      which claim is CURRENT for this key
    previous value     which claim was SUPERSEDED
    disagreement       which keys have more than one live side
    evidence           the exact quote, path and character offsets
    known unknowns     what the pack does not answer
    timing             when the state was resolved
    integrity          whether the pack is internally consistent

Run it twice: once with the live stack importable, and once in a subprocess that
refuses to import it. The second run is the one that proves portability.
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from memory_pack import (  # noqa: E402
    CONFLICTING,
    NO_RELEVANT_MEMORY,
    MemoryPack,
    PackError,
)

# -- the consumer ------------------------------------------------------------

CONSUMER_NAME = "pack-only-consumer"


def answer(pack: MemoryPack, question: str) -> str:
    """Answer one question using nothing but the pack."""
    q = question.casefold()

    if "integrity" in q or "trustworthy" in q:
        problems = pack.verify()
        if problems:
            return "NOT TRUSTWORTHY:\n" + "\n".join(f"  - {p}" for p in problems)
        return f"TRUSTWORTHY: {len(pack.all_claims())} claims, all with consistent evidence."

    if "unknown" in q or "not know" in q or "missing" in q:
        return _unknowns(pack)

    if "when" in q and ("resolved" in q or "instant" in q or "valid" in q):
        return f"Resolved {pack.state.describe()}."

    if "disagree" in q or "conflict" in q:
        return _conflicts(pack)

    if "evidence" in q or "quote" in q or "prove" in q or "source" in q:
        return _evidence(pack)

    if "previous" in q or "before" in q or "used to" in q:
        return _history(pack)

    if "status" in q or "answer" in q:
        return pack.summary()

    return _current(pack)


def _current(pack: MemoryPack) -> str:
    if pack.status == NO_RELEVANT_MEMORY:
        return "NO_RELEVANT_MEMORY. The corpus has material, none of it answers this."
    live = pack.current or tuple(
        c for g in pack.conflicts for c in g.claims if c.status != "SUPERSEDED"
    )
    if not live:
        return f"{pack.status.upper()}: the pack carries no current claim."
    lines = [f"{pack.status.upper()}:"]
    for claim in live:
        lines.append(f"  {claim.key} = {claim.value!r}  [{claim.status}] {pack.claim_text(claim)}")
    return "\n".join(lines)


def _history(pack: MemoryPack) -> str:
    past = [c for c in pack.all_claims() if c.status == "SUPERSEDED"]
    past += [c for c in pack.historical if c.status != "SUPERSEDED"]
    if not past:
        return "The pack records no earlier value for this subject."
    lines = ["Previously:"]
    for claim in past:
        lines.append(f"  {claim.key} was {claim.value!r} ({pack.claim_text(claim)})")
    for change in pack.changes:
        if change.previous and change.current:
            lines.append(
                f"  {change.observed_at} {change.path}: "
                f"{change.previous[0].value!r} -> {change.current[0].value!r}"
                f" ({change.relationship})"
            )
    return "\n".join(lines)


def _conflicts(pack: MemoryPack) -> str:
    if not pack.conflicts:
        return "The sources in this pack do not disagree."
    lines = [f"{CONFLICTING.upper()}:"]
    for group in pack.conflicts:
        lines.append(f"  {group.key}:")
        for side in group.claims:
            quotes = pack.quotes_for(side)
            quote = f' evidence: "{quotes[0]}"' if quotes else ""
            lines.append(f"    - {side.value!r} [{side.status}] {side.path}{quote}")
    return "\n".join(lines)


def _evidence(pack: MemoryPack) -> str:
    lines = ["Evidence, exactly as archived:"]
    for claim in pack.all_claims():
        for row in pack.evidence_for(claim.id):
            lines.append(
                f"  {claim.key} = {claim.value!r}\n"
                f'    "{row.text}"\n'
                f"    {row.path} {row.start_offset}:{row.end_offset} "
                f"observed {row.observed_at}\n"
                f"    version {row.version_id[:12]} chunk {row.chunk_id[:12]}"
            )
    return "\n".join(lines) if len(lines) > 1 else "The pack carries no evidence."


def _unknowns(pack: MemoryPack) -> str:
    lines = []
    if pack.status == NO_RELEVANT_MEMORY:
        lines.append("- The corpus has material but nothing relevant to this question.")
    if pack.conflicts:
        keys = ", ".join(sorted(g.key for g in pack.conflicts))
        lines.append(f"- No single value for: {keys}. A human must choose.")
    if pack.uncertain:
        keys = ", ".join(sorted({c.key for c in pack.uncertain}))
        lines.append(f"- Authored but unresolved: {keys}.")
    if not pack.evidence:
        lines.append("- No evidence is attached to anything in this pack.")
    if pack.truncated:
        lines.append(f"- Truncated at {pack.budget_unit}; some content was omitted.")
    lines.append(f"- The pack covers corpus {pack.corpus!r} only.")
    return "\n".join(lines)


# claim_text / quotes_for are convenience accessors on the reader.
def _reader_extras(self: MemoryPack, claim) -> str:
    return claim.claim


MemoryPack.claim_text = _reader_extras  # type: ignore[attr-defined]
MemoryPack.quotes_for = (  # type: ignore[attr-defined]
    lambda self, claim: [e.text for e in self.evidence_for(claim.id)]
)


# -- runner ------------------------------------------------------------------

QUESTIONS = (
    "What is the current database?",
    "What was the database before?",
    "Do the sources disagree?",
    "Show the evidence.",
    "What do we not know?",
    "When was this resolved?",
    "Is this pack trustworthy?",
)


def run(payload: str) -> int:
    try:
        pack = MemoryPack.from_json(payload)
    except PackError as exc:
        print(f"{CONSUMER_NAME}: refused the input: {exc}", file=sys.stderr)
        return 2

    forbidden = [
        m for m in ("api", "sqlalchemy", "torch", "asyncpg", "mindpalace_sdk") if m in sys.modules
    ]
    print(f"consumer: {CONSUMER_NAME}")
    print(f"schema_version: {pack.schema_version}")
    print(f"bytes: {pack.size()}   digest: {pack.digest()[:16]}...")
    print(f"server modules imported: {forbidden or 'none'}")
    print()
    for question in QUESTIONS:
        print(f"Q: {question}")
        print(answer(pack, question))
        print()
    return 0


if __name__ == "__main__":
    raise SystemExit(run(Path(sys.argv[1]).read_text() if len(sys.argv) > 1 else sys.stdin.read()))
