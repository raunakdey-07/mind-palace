"""The defects a live CLI run against a real corpus found, and the tests for them.

Four of them, and they are four different promises the product makes:

* **History walks the supersession graph in both directions.** `mindpalace
  history` anchored on a retired claim, found nothing to walk back to, and
  reported the retired claim as the one that is true now. The graph has edges in
  both directions and the reader has to follow both of them.
* **A table claim carries the section it is in.** A question about the SDK
  bundle size was answered with two other rows of the same table, because the
  word "SDK" was in the heading above the table and not in the cell text.
* **A wrapped sentence is not cut in half.** A statement that ended on "PDF
  and" was archived as "PDF and", which is the half that carries no fact.
* **A row that says nothing is not a claim.** A table with an empty header
  produced one claim per link and no fact at all.

The database tests run against the schema the product ships, in a rolled-back
transaction, so a failure here is a failure of the product and not of a fixture.
"""

import os

from api.models.memory import MemoryRequest
from api.services import memory_public
from api.services.markdown_claims import claims_for
from api.services.parser import (
    chunk_with_heading_paths,
    extract_sections_with_paths,
    parse_markdown,
)

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

#: The two claims of the reported defect, verbatim.
#:
#: The first was written at 11:25 and the second at 11:30, in two different
#: documents, and the second retired the first across the document boundary.
OLD_CLAIM = (
    "Mind Palace's default install is 216 MB total (79 MB embedding stack) using "
    "onnxruntime as the default embedding backend, with sentence-transformers moved "
    "to the optional [torch] extra."
)
NEW_CLAIM = (
    "Mind Palace replaced the default 5.1 GB PyTorch embedder with a 216 MB default "
    "ONNX runtime backend (79 MB embedding stack, min cosine 0.99999833), cutting "
    "cold recall latency from 7.63s to 1.42s (0.32s warm)."
)

#: The question that anchored on the retired claim and stopped there.
QUESTION = "How did the default embedding backend and install size change?"


async def _ingest_file(store, body: str, path: str) -> None:
    """Archive one document, the way `ingest-repo` does."""
    from api.services import ingestion as ingestion_service

    await ingestion_service.IngestionService(
        memory_enabled=True, require_embeddings=False
    ).ingest_file(content=body, path=path, corpus_id=store.corpus)


def _extract(body: str, path: str = "doc.md") -> list[dict]:
    """The claims and warnings the extractor reads out of one document."""
    metadata, parsed = parse_markdown(body)
    records = [
        {**chunk, "order_index": index}
        for index, chunk in enumerate(chunk_with_heading_paths(extract_sections_with_paths(parsed)))
    ]
    derived, warnings = claims_for(metadata, parsed, records, path=path)
    assert derived is not None, "the document carries an authored claims block"
    return derived["claims"], warnings


# --------------------------------------------------------------------------
# 1. History walks the supersession graph in both directions
# --------------------------------------------------------------------------


async def test_history_anchored_on_a_retired_claim_walks_forward(store):
    """The defect, reproduced: the retired claim was the end of the timeline.

    The question names the install size, which only the older claim mentions,
    and the embedding backend, which both mention. It anchored on the older
    claim, and the reader walked the supersession edges backwards only, so the
    newer claim never entered the timeline and the retired claim was reported
    as the one that is true now.
    """
    from api.services.remember import remember

    await _ingest_file(store, f"- {OLD_CLAIM}\n", "install.md")
    target = next(
        claim
        for claim in await _live_claims(store)
        if "default install is 216 MB" in claim["claim"]
    )
    await remember(NEW_CLAIM, corpus=store.name, supersedes_claim_id=target["id"])

    response = await memory_public.execute_in_session(
        store.db, "history", MemoryRequest(corpus=store.name, query=QUESTION)
    )

    timeline = [claim.claim for change in response.changes for claim in change.current]
    assert OLD_CLAIM in timeline, "the retired claim belongs in the history"
    assert NEW_CLAIM in timeline, "the claim that replaced it belongs in the history too"

    rendered = _render(response)
    assert f"current now: {NEW_CLAIM}" in rendered, rendered
    assert f"current now: {OLD_CLAIM}" not in rendered, rendered


async def test_history_reports_the_successor_when_the_anchor_is_the_older_claim(store):
    """The same question, asked of the retired claim on its own terms.

    Anchoring on the retired claim must reach the claim that retired it. The
    chain is walked in both directions, so the answer does not depend on which
    end of it the question happened to name.
    """
    from api.services.remember import remember

    await _ingest_file(store, f"- {OLD_CLAIM}\n", "install.md")
    target = next(
        claim
        for claim in await _live_claims(store)
        if "default install is 216 MB" in claim["claim"]
    )
    await remember(NEW_CLAIM, corpus=store.name, supersedes_claim_id=target["id"])

    response = await memory_public.execute_in_session(
        store.db,
        "history",
        MemoryRequest(corpus=store.name, query="What is the default embedding backend?"),
    )
    rendered = _render(response)
    assert NEW_CLAIM in rendered, rendered


async def test_a_retired_claim_is_never_reported_as_current_now(store):
    """The rule, stated on its own: the last line is a claim that is live.

    Whatever the question was, the line that says what is true now has to name
    a claim the archive still holds live.
    """
    from api.services.remember import remember

    await _ingest_file(store, f"- {OLD_CLAIM}\n", "install.md")
    target = next(
        claim
        for claim in await _live_claims(store)
        if "default install is 216 MB" in claim["claim"]
    )
    await remember(NEW_CLAIM, corpus=store.name, supersedes_claim_id=target["id"])

    for question in (QUESTION, "PyTorch embedder", "default install", "embedding backend"):
        response = await memory_public.execute_in_session(
            store.db, "history", MemoryRequest(corpus=store.name, query=question)
        )
        rendered = _render(response)
        line = next(line for line in rendered.splitlines() if line.startswith("current now:"))
        assert line != f"current now: {OLD_CLAIM}", (question, rendered)


async def _live_claims(store) -> list[dict]:
    from api.services.memory import _active_claims

    return await _active_claims(store.db, store.corpus, limit=None)


def _render(response) -> str:
    from cli.render import render_history

    return render_history(response)


# --------------------------------------------------------------------------
# 2. A table claim carries the section it is in
# --------------------------------------------------------------------------


async def test_a_table_under_a_heading_answers_a_question_about_that_heading(store):
    """The word "SDK" was in the heading above the table, not in the cell.

    A row reads as "`embed.js`: Gzip: 20.40 kB" and says nothing about which
    part of the document it came from. The heading above the table is what
    makes it the SDK's bundle size rather than any other file's, and a question
    that names the heading is a question about that table.

    The heading is long on purpose. A claim's key holds a slug of its heading
    path, cut at 48 characters, so a deeply nested heading loses the words that
    identify it -- and the words that identify the table are exactly the ones
    a question about the table uses. The archive still holds the whole
    heading, on the chunk the claim was quoted from.
    """
    body = (
        "# Performance\n"
        "\n"
        "## Measurements, methodology and the numbers we publish > SDK bundle\n"
        "\n"
        "| File | Gzip |\n"
        "| --- | --- |\n"
        "| `embed.js` | 20.40 kB |\n"
    )
    await _ingest_file(store, body, "PERFORMANCE.md")

    response = await memory_public.execute_in_session(
        store.db,
        "query",
        MemoryRequest(corpus=store.name, query="What is the gzipped SDK bundle size?"),
    )
    answered = [claim.claim for claim in response.current_memories]
    assert any("20.40 kB" in claim for claim in answered), answered


def test_a_table_claim_reads_with_the_heading_above_it():
    """The heading is the claim's context, and the archive knows what it is.

    A row reads as "`embed.js`: Gzip: 20.40 kB" and says nothing about which
    part of the document it came from. The heading above the table is what
    makes it the SDK's bundle size, and the archive records that heading on the
    chunk the claim was quoted from.
    """
    from api.services.memory_relevance import claim_headings

    body = (
        "## SDK bundle\n" "\n" "| File | Gzip |\n" "| --- | --- |\n" "| `embed.js` | 20.40 kB |\n"
    )
    claims, warnings = _extract(body)
    assert not warnings, warnings
    assert len(claims) == 1, claims

    class _Evidence:
        claim_id = "c1"
        heading = "SDK bundle"

    class _Response:
        evidence = [_Evidence()]

    headings = claim_headings(_Response())
    assert "sdk" in headings["c1"], headings


# --------------------------------------------------------------------------
# 3. A wrapped sentence is not cut in half
# --------------------------------------------------------------------------


def test_a_statement_ending_on_a_conjunction_continues_on_the_next_line():
    """The reported cut: "PDF and" was the whole claim.

    The writer wrapped the sentence and the next line completed it. The line
    opened with a capital, and the rule that ended a field statement there was
    what cut the sentence in half.
    """
    body = (
        "Numbers, not adjectives. [`docs/PERFORMANCE.md`](docs/PERFORMANCE.md) records\n"
        "the measured baseline: bundle sizes, first-paint and keystroke latency, PDF and\n"
        "DOCX export cost, and bridge message volume. Reproduce it with:\n"
    )
    claims, warnings = _extract(body)
    assert not warnings, warnings
    assert len(claims) == 1, claims
    claim = claims[0]["claim"]
    assert "PDF and" in claim, claim
    assert "DOCX export cost" in claim, claim


def test_a_statement_that_ends_a_sentence_still_ends_there():
    """The fix is narrow: a finished sentence still ends the field.

    A `Key: Value` field is one sentence, not a paragraph. The line that
    follows a finished one is the next statement, and folding it in would
    turn one fact into two.
    """
    body = "Status: Accepted\nOwner: The platform team\n"
    claims, warnings = _extract(body)
    assert not warnings, warnings
    assert [claim["value"] for claim in claims] == ["Accepted", "The platform team"], claims


def test_a_chunk_does_not_end_on_a_conjunction():
    """The chunk boundary rule: a chunk never stops on "and".

    A chunk that stops mid-phrase holds half a sentence, and the claim read
    out of it inherits the cut.
    """
    from api.services.parser import _trim_trailing_word

    assert _trim_trailing_word("the measured baseline, PDF and") == "the measured baseline, PDF"
    assert _trim_trailing_word("the measured baseline, PDF") == "the measured baseline, PDF"
    assert _trim_trailing_word("the install size in") == "the install size"


# --------------------------------------------------------------------------
# 4. A row that says nothing is not a claim
# --------------------------------------------------------------------------


def test_a_table_with_an_empty_header_produces_no_claims():
    """A navigation table is not a decision.

    The header row was empty, so no column could name a value, so every row
    rendered as its own label: one claim per link, saying nothing about what
    any of them is.
    """
    body = (
        "| | | |\n"
        "|---|---|---|\n"
        "| [Errors](docs/errors.md) | every code the CLI returns | x |\n"
        "| [Security](docs/security.md) | threat model | y |\n"
    )
    claims, warnings = _extract(body)
    assert not warnings, warnings
    assert claims == [], claims


def test_a_row_of_status_marks_produces_no_claim():
    """A row that says everything is fine names nothing."""
    body = "| Check | Status |\n" "|---|---|\n" "| Lint | ✓ |\n" "| Build | — |\n"
    claims, warnings = _extract(body)
    assert not warnings, warnings
    assert claims == [], claims


def test_a_real_table_row_still_produces_its_claim():
    """The filter is narrow: a row with values still says what it measured."""
    body = "| File | Gzip |\n" "|---|---|\n" "| `embed.js` | 20.40 kB |\n"
    claims, warnings = _extract(body)
    assert not warnings, warnings
    assert len(claims) == 1, claims
    assert "20.40 kB" in claims[0]["claim"], claims[0]["claim"]


# --------------------------------------------------------------------------
# 6. The ingest writes its rows in batches
# --------------------------------------------------------------------------


async def test_the_ingest_writes_its_rows_in_batches(store, monkeypatch):
    """The ingest used to spend a database round trip per row.

    The claims and the evidence were written one row at a time, inside a Python
    loop. A repository of 78 documents produced 2,487 claims and 2,487 evidence
    rows: 4,974 statements for a write that is 4,974 rows. They are written in
    two statements now.

    The assertion is on the ratio rather than on a count, because a small
    fixture cannot show what a large one shows: twenty claims written one at a
    time is twenty statements, and twenty claims written in a batch is one.
    """
    from sqlalchemy.ext.asyncio import AsyncSession

    captured: list[str] = []
    original = AsyncSession.execute

    async def capture(self, statement, *args, **kwargs):
        captured.append(str(statement))
        return await original(self, statement, *args, **kwargs)

    monkeypatch.setattr(AsyncSession, "execute", capture)

    body = "# Decisions\n\n" + "".join(
        f"- Decision number {index} was recorded and signed off.\n" for index in range(20)
    )
    await _ingest_file(store, body, "decisions.md")

    inserts = [statement for statement in captured if "INSERT INTO memory_claims" in statement]
    evidence = [statement for statement in captured if "INSERT INTO memory_evidence" in statement]
    assert len(inserts) == 1, f"the claims were written {len(inserts)} times, not once"
    assert len(evidence) == 1, f"the evidence was written {len(evidence)} times, not once"


# --------------------------------------------------------------------------
# 5. The same table row in two tables
# --------------------------------------------------------------------------


def test_a_row_that_appears_in_two_tables_is_quoted_from_the_right_one():
    """The same bytes under two headers are two facts, and each needs its own.

    `| recall@1 | 0.5284 | 0.5227 |` under a "before / after" header and under
    an "OLD / NEW" header renders two different claims. The archive resolves a
    quote against the first chunk that holds it, so the second claim was looked
    for in the first table, was not found there, and the document was refused
    outright. Two of this repository's own documents were refused that way.
    """
    from api.services import memory

    body = (
        "# Measurements\n"
        "\n"
        "| | before | after |\n"
        "|---|---:|---:|\n"
        "| recall@1 | 0.5284 | 0.5227 |\n"
        "\n"
        "| | OLD | NEW |\n"
        "|---|---:|---:|\n"
        "| recall@1 | 0.5284 | 0.5227 |\n"
    )
    metadata, parsed = parse_markdown(body)
    records = [
        {**chunk, "order_index": index}
        for index, chunk in enumerate(chunk_with_heading_paths(extract_sections_with_paths(parsed)))
    ]
    derived, warnings = claims_for(metadata, parsed, records, path="measurements.md")
    assert not warnings, warnings
    metadata = {**metadata, **derived}
    memory.validate_source("measurements.md", body, metadata, records)
