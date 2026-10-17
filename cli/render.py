# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 Raunak Dey

"""Rendering for the five product operations: remember, recall, explain, history, verify.

Every renderer reads the public response contract and nothing else, so what a
developer reads is exactly what the service returned -- no second projection, and
no ranking or retrieval internals in the default view.

Progressive disclosure is the rule here. `recall` prints an answer, a source and a
time, because that is what a question needs. Everything deeper -- evidence,
conflicts, supersession lineage, the receipt digest -- is one flag away, and is
printed only when asked for.
"""

from __future__ import annotations

from api.models.memory import MemoryResponse

#: How much of a statement to show inline. Long enough to read, short enough that
#: a result list stays scannable.
PREVIEW = 200


def _short(value: str, limit: int = PREVIEW) -> str:
    value = " ".join(value.split())
    return value if len(value) <= limit else value[: limit - 1] + "…"


def _time_line(response: MemoryResponse) -> str:
    """When the answer was true, and how that was decided."""
    state = response.state
    if state.snapshot:
        return f"snapshot {state.snapshot[:12]}"
    if state.valid_at:
        return f"true as of {state.valid_at:%Y-%m-%d %H:%M} UTC"
    return "true now"


def _source_line(response: MemoryResponse, claim) -> str:
    """Where the answer came from, and the id that names it.

    The claim id is on every answer, not only on ambiguous ones. It is what
    `mindpalace remember --supersedes` takes, and `--json` prints the canonical
    form, which deliberately excludes the retrieval diagnostics -- so without it
    here there is no way for a CLI user to get a claim id at all.
    """
    path = claim.path or "unknown"
    return f"{path} @ {claim.version_id[:12]} (claim {claim.id[:12]})"


def _confidence_line(response: MemoryResponse) -> list[str]:
    """How far the ranking can be acted on, and what to read if it cannot.

    A single top hit is the whole answer as far as the response is concerned, and
    that is not enough to act on. Rank 1 is wrong for 58 of the 175 answerable dev
    questions, and nothing in a lone score says so, so the confidence and the
    shortlist behind it are part of the answer rather than a debug aid.
    """
    lines: list[str] = []
    if response.match_status == "ambiguous":
        lines.append("")
        lines.append(
            "Two or more claims score almost the same, so the top one is not safe to "
            "act on alone. Check the quote that matches your case:"
        )
        for candidate in response.candidates:
            mark = " " if candidate.status == "CURRENT" else "?"
            # The claim id is here so `--supersedes` can name it without a JSON
            # detour, which is the whole point of printing a shortlist.
            ids = " ".join(c[:12] for c in candidate.claim_ids)
            lines.append(f"  {mark} {candidate.score:.3f}  {_short(candidate.quotes[0], 72)}")
            lines.append(f"      {candidate.path} ({candidate.status.lower()}) claim {ids}")
        lines.append(
            "\nRead the source for the one you meant, or retire the wrong one with\n"
            'mindpalace remember "<new fact>" --supersedes <claim-id>'
        )
    elif response.match_margin is not None:
        lines.append("")
        lines.append(f"clear answer (margin {response.match_margin:.3f})")
    return lines


def render_recall(
    response: MemoryResponse, *, explain: bool = False, budget: int | None = None
) -> str:
    """The answer, its source and its time. Plus why, on request."""
    lines = []
    if response.truncated:
        lines.append("(truncated to fit the response budget)")

    current = list(response.current_memories)
    historical = list(response.historical_memories)
    conflicts = response.conflicts

    if not current:
        if conflicts:
            # A conflict is not an absence. Two documents declaring the same key
            # give exactly this: no current claim, two disagreeing ones. Saying
            # "nothing matched" sends the reader looking for a missing document
            # when the documents are both there and disagreeing.
            lines.append(f"{len(conflicts)} key(s) have sources that disagree:")
            for conflict in conflicts:
                claims = getattr(conflict, "claims", []) or []
                lines.append(f"  {getattr(conflict, 'key', 'unknown')}:")
                for claim in claims:
                    lines.append(
                        f"    - {_short(claim.claim)}  ({getattr(claim, 'path', 'unknown')})"
                    )
            lines.append(
                "\nNo answer is given while sources disagree. Resolve it by "
                "re-ingesting the newer document as a new version of the same "
                "path, or by using `mindpalace remember` with the same --key. "
                "See docs/authoring.md."
            )
            return "\n".join(lines)
        lines.append("No memory matched that question.")
        if response.constraints:
            lines.append(f"  {', '.join(response.constraints)}")
        # Prose with nothing claim-shaped in it is archived and searchable but
        # contributes no memory, and an abstention has to say which of the two it
        # is. "Nothing matched" alone sends the reader looking for a retrieval
        # fault when the document simply states nothing assertable.
        lines.append(
            "\nA document becomes memory from its headings, list items and\n"
            "`Key: Value` lines, or from an authored `claims:` block. A file\n"
            "with prose and no such lines contributes nothing here. See\n"
            "docs/authoring.md for both forms."
        )
        lines.append("\nNothing was invented: Mind Palace records only what was remembered.")
        return "\n".join(lines)

    for index, claim in enumerate(current, 1):
        prefix = f"{index}. " if len(current) > 1 else ""
        lines.append(f"{prefix}{claim.claim}")
        lines.append(f"   source: {_source_line(response, claim)}")
        lines.append(f"   time:   {_time_line(response)}")

    lines.extend(_confidence_line(response))

    if historical:
        lines.append("")
        lines.append("No longer current:")
        for claim in historical:
            lines.append(f"  - {claim.status}: {_short(claim.claim)}")

    if conflicts:
        lines.append("")
        lines.append("Sources disagree:")
        for group in conflicts:
            for claim in group.claims:
                lines.append(f"  - {_short(claim.claim)}  ({claim.path})")

    if budget is not None:
        lines.append("")
        lines.append(f"budget: {len(response.canonical_json())}/{budget} unicode characters")

    if explain:
        lines.append("")
        lines.append(render_explain(response))
    else:
        lines.append("")
        lines.append('Ask for the basis with: mindpalace explain "<question>"')
    return "\n".join(lines)


def render_explain(response: MemoryResponse, *, changes=None) -> str:
    """Why this answer: source, time, evidence, history, and what can be verified.

    This is the recorded provenance of the answer, not a reasoning trace. No model
    internals, prompts or ranking scores are exposed, because none of them are
    what makes the answer trustworthy.

    `changes` is passed in rather than read from `response.changes`, so a caller
    can add the change set without mutating a response whose receipt digest
    already covers it.
    """
    sections: list[str] = []
    current = list(response.current_memories)
    sections.append(_explain_answer(response, current))
    sections.append(_explain_time(response))
    sections.append(_explain_evidence(response, current))
    sections.append(_explain_history(changes if changes is not None else response.changes))
    sections.append(_explain_verification(response))
    sections.append(
        "This explains the recorded provenance and temporal basis for the memory. "
        "It does not establish that the original source was factually correct."
    )
    return "\n\n".join(section for section in sections if section)


def _explain_answer(response: MemoryResponse, current: list) -> str:
    if not current:
        return "ANSWER\n  Nothing matched this question, so there is nothing to explain."
    lines = ["ANSWER"]
    for claim in current:
        lines.append(f"  {claim.claim}")
        if claim.status != "CURRENT":
            lines.append(f"    status: {claim.status}")
    return "\n".join(lines)


def _explain_time(response: MemoryResponse) -> str:
    state = response.state
    lines = ["TIME"]
    if state.as_of:
        lines.append(f"  reconstructed as of: {state.as_of:%Y-%m-%d %H:%M} UTC")
    if state.valid_at:
        lines.append(f"  answer true at:       {state.valid_at:%Y-%m-%d %H:%M} UTC")
    if state.snapshot:
        lines.append(f"  snapshot:             {state.snapshot}")
    if not any((state.as_of, state.valid_at, state.snapshot)):
        lines.append("  now (the latest recorded state)")
    return "\n".join(lines)


def _explain_evidence(response: MemoryResponse, current: list) -> str:
    if not current:
        return ""
    # Ordered by the claims, not by evidence id, so the evidence under each answer
    # reads in the same order as the answers themselves.
    by_id = {e.id: e for e in response.evidence}
    rows = [by_id[eid] for claim in current for eid in claim.evidence_ids if eid in by_id]
    if not rows:
        return ""
    lines = ["EVIDENCE"]
    for evidence in rows:
        heading = f" > {evidence.heading}" if evidence.heading else ""
        lines.append(f"  {evidence.path}{heading}")
        lines.append(f'    "{_short(evidence.text, 160)}"')
        lines.append(
            f"    characters {evidence.start_offset}-{evidence.end_offset} "
            f"of version {evidence.version_id[:12]}"
        )
    return "\n".join(lines)


def _explain_history(changes) -> str:
    """The chain that produced this answer. A truncation notice is stated, not hidden."""
    if not changes:
        return ""
    lines = ["HISTORY"]
    for change in changes:
        stamp = f"{change.observed_at:%Y-%m-%d %H:%M} UTC"
        current = _short(change.current[0].claim) if change.current else None
        if change.event == "TRUNCATED":
            lines.append(f"  ({change.path})")
        elif not change.previous:
            # The first version of a document records memory; it does not replace any.
            lines.append(f"  {stamp}  recorded  {current or change.path}")
        elif change.memory_changed and current:
            lines.append(f"  {stamp}  {_short(change.previous[0].claim)}")
            lines.append(f"             superseded by  {current}")
        else:
            # A document edited without changing its memory: worth seeing, but not a
            # supersession, and not dressed up as one.
            lines.append(f"  {stamp}  {change.event} {change.path} (memory unchanged)")
    return "\n".join(lines)


def _explain_verification(response: MemoryResponse) -> str:
    """What can be proved, and how. Never overstates what verification covers."""
    lines = ["VERIFICATION"]
    if not response.receipt:
        lines.append("  no receipt on this response")
        lines.append(
            '  Get one with: mindpalace receipt "' + (response.query or "your question") + '"'
        )
        return "\n".join(lines)
    digest = response.receipt.get("memory_pack_digest")
    count = response.receipt.get("receipt_count", 0)
    lines.append(f"  receipt available: yes ({count} receipt(s))")
    lines.append(f"  authoritative digest: {digest}")
    lines.append("  export and verify offline with:")
    lines.append(f'    mindpalace receipt "{response.query}" -o receipt.json')
    lines.append("    mindpalace verify receipt.json")
    lines.append("  Authenticity is not established without a trust anchor you pinned yourself.")
    return "\n".join(lines)


def render_history(response: MemoryResponse) -> str:
    """The supersession chain, oldest first, as a readable progression.

    Reads the version graph directly rather than the query's current selection, so
    the last line of the chain is the last line of the archive -- not whichever
    claim happened to match the question most strongly.
    """
    changes = list(response.changes)
    if not changes:
        return "No recorded history for that memory."

    lines = []
    for change in changes:
        stamp = f"{change.observed_at:%Y-%m-%d %H:%M} UTC"
        current = change.current[0].claim if change.current else None
        if not change.previous:
            lines.append(f"{stamp}  recorded  {current or change.path}")
        elif change.memory_changed and current:
            lines.append(f"{stamp}  {change.previous[0].claim}")
            lines.append(f"            superseded by  {current}")
        else:
            lines.append(f"{stamp}  {change.event} {change.path} (memory unchanged)")

    tail = changes[-1].current or changes[-1].previous
    lines.append("")
    lines.append(f"current now: {tail[0].claim if tail else 'nothing recorded'}")
    return "\n".join(lines)


def render_remember(result: dict, *, corpus: str) -> str:
    """Confirm what was written, be explicit when nothing was, and name near-misses."""
    event = result.get("event", "NEW")
    if event == "UNCHANGED":
        return "\n".join(
            [
                "Already remembered. Nothing changed, so no new version was recorded.",
                f"  version: {result.get('version_id', '')[:12]}",
                f"  corpus:  {corpus}",
            ]
        )
    lines = [
        "Remembered.",
        f"  version: {result.get('version_id', '')[:12]} ({event.lower()})",
        f"  corpus:  {corpus}",
        f"  path:    {result.get('path', '')}",
    ]
    related = result.get("related_active_claims") or []
    if related:
        # The archive cannot tell whether one of these contradicts the new claim or
        # should have been replaced by it. Saying so is more useful than picking.
        lines.append("")
        lines.append(f"{len(related)} live claim(s) already read like this one:")
        for item in related:
            lines.append(f"  {item['score']:.2f}  {_short(item['claim'], 88)}")
            lines.append(f"      claim {item['claim_id'][:12]} on key {item['key']}")
        lines.append(
            "\nIf one of them is now wrong, record the replacement with "
            "`--supersedes <claim-id>`."
        )
    return "\n".join(lines)
