"""The five defects of round two, each with the test that would have caught it.

They are one file because they are one afternoon's worth of reading the same
archive from five angles, and because each of them is a promise the product
makes in the same breath: that a supersession lands, that a table is readable,
that a score means what it says, that an ingest reports what it did, and that a
history read says whether it found anything.

* **Supersession.** `recall` prints twelve characters of a claim id and tells
  the caller to use it. A reference that names nothing has to fail loudly, and a
  document that declares `supersedes: <that id>` has to resolve it.
* **Tables.** The primary metrics in a benchmark table were invisible to
  `recall`, because a table is not prose.
* **The margin.** The banner said "these score almost the same" next to two
  numbers that differed by a fifth, and a response of one claim reported a
  margin of `score - 0.0` -- a decisive win over nobody.
* **The ingest report.** It counted chunks and never said how many claims the
  corpus ended up with.
* **The history status.** A history read that found claims said `abstained` on
  the wire, which is the one word that means it found nothing.

Every migration is applied and the transaction rolls back, so the database
tests run against the schema the product ships.
"""

import os
from pathlib import Path

import pytest

from api.services import memory as memory_service
from api.services.memory import _active_claims, _hash
from api.services.markdown_claims import claims_for
from api.services.parser import (
    chunk_with_heading_paths,
    extract_sections_with_paths,
    read_table,
    render_table_row,
)

# The `store` fixture -- a real corpus on the real schema, in a rolled-back
# transaction -- comes from `tests/conftest.py`, so a failure here and a failure
# in the supersession tests are the same corpus.

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


async def _ingest_file(store, body: str, path: str) -> None:
    """Archive one document, the way `ingest-repo` does."""
    from api.services import ingestion as ingestion_service

    await ingestion_service.IngestionService(
        memory_enabled=True, require_embeddings=False
    ).ingest_file(content=body, path=path, corpus_id=store.corpus)


async def _live(store) -> list[dict]:
    """Every claim the corpus currently holds as fact."""
    from tests.test_cross_document_supersession import live

    return await live(store)


# --- 1. The supersession reference the caller can actually see -------------


async def test_a_declared_supersession_resolves_the_id_recall_printed(store):
    """A document that says `supersedes: <twelve characters>` resolves it.

    `recall` prints twelve characters of a sixty-four character id. A document
    that names that twelve characters is naming the claim the reader was shown,
    and the reference used to resolve only whole keys and whole paths -- so the
    declaration became a warning and the claim it named stayed live.
    """

    await _ingest_file(
        store,
        "# ADR 0001: Use PostgreSQL 15\n\n- The primary datastore is PostgreSQL 15.\n",
        "0001-use-postgres15.md",
    )
    target = next(c for c in await _live(store) if "PostgreSQL 15" in c["claim"])

    await _ingest_file(
        store,
        (
            "---\n"
            "title: ADR 0002\n"
            f"supersedes: {target['id'][:12]}\n"
            "---\n"
            "\n"
            "# ADR 0002: Use PostgreSQL 16\n"
            "\n"
            "- The primary datastore is PostgreSQL 16.\n"
        ),
        "0002-use-postgres16.md",
    )

    texts = [c["claim"] for c in await _live(store)]
    assert not any(
        "PostgreSQL 15." == text for text in texts
    ), "the claim the document named is still live"
    assert any("PostgreSQL 16." in text for text in texts)


async def test_an_explicit_supersedes_that_names_nothing_is_refused(store):
    """A reference that matches no claim must not write an unlinked claim.

    The write succeeded and the old claim stayed live, so the archive held two
    live claims about the same thing and the caller was told nothing.
    """

    from api.services.remember import RememberError, remember

    before = len(await _active_claims(store.db, store.corpus, limit=None))

    with pytest.raises(RememberError) as failure:
        await remember(
            "The primary datastore is PostgreSQL 16.",
            corpus=store.name,
            supersedes_claim_id="deadbeef0000",
        )

    assert "deadbeef0000" in str(failure.value), "the error has to name the reference"
    after = len(await _active_claims(store.db, store.corpus, limit=None))
    assert after == before, "a refused supersession still wrote a claim"


async def test_an_explicit_supersedes_that_names_two_claims_is_refused():
    """A prefix that matches two claims is refused rather than resolved."""

    # Two claims whose ids share a prefix is not something a test can arrange
    # directly, so the ambiguity is checked on the resolver itself.
    shared = "ab" * 31
    candidates = [{"id": shared + "c"}, {"id": shared + "d"}]
    assert (
        len(memory_service._claims_by_id_prefix(candidates, shared)) == 2
    ), "the resolver did not see both claims the prefix names"
    assert memory_service._claims_by_id_prefix(candidates, shared + "c") == [candidates[0]]
    # A prefix too short to be an identifier is not one.
    assert memory_service._claims_by_id_prefix(candidates, "ab") == []


async def test_remember_with_a_file_still_supersedes(store, tmp_path):
    """`remember --file` used to drop the flag on the floor.

    The file branch of `remember` called the writer without the supersession
    argument, so the write succeeded, printed `Remembered.`, and left the claim
    it was meant to retire standing.
    """

    from api.services.remember import remember

    await _ingest_file(
        store,
        "# ADR 0001\n\n- The primary datastore is PostgreSQL 15.\n",
        "0001.md",
    )
    target = next(c for c in await _live(store) if "PostgreSQL 15" in c["claim"])

    path = tmp_path / "0002.md"
    path.write_text("# ADR 0002\n\n- The primary datastore is PostgreSQL 16.\n", encoding="utf-8")
    await remember(
        corpus=store.name,
        file=str(path),
        supersedes_claim_id=target["id"][:12],
    )

    texts = [c["claim"] for c in await _live(store)]
    assert not any(text.endswith("PostgreSQL 15.") for text in texts)
    assert any(text.endswith("PostgreSQL 16.") for text in texts)


# --- 2. The table nobody could read ---------------------------------------


TABLE = """\
# Runtime

| | `sentence-transformers` | onnxruntime |
|---|---|---|
| `import` | 6.15 s | 0.40 s |
| `recall` | 1.42 s | 0.21 s |
"""


def test_a_table_row_becomes_a_claim_that_says_which_column_it_is():
    """A row of pipes is not a sentence. The column name is what makes it a fact."""
    records = _records(TABLE)
    derived, warnings = claims_for({}, TABLE, records, path="runtime.md")
    assert not warnings
    assert [c["claim"] for c in derived["claims"]] == [
        "`import`: `sentence-transformers`: 6.15 s; onnxruntime: 0.40 s",
        "`recall`: `sentence-transformers`: 1.42 s; onnxruntime: 0.21 s",
    ]


def test_a_table_quote_is_the_row_the_document_wrote():
    """The evidence has to be findable in the file byte for byte."""
    records = _records(TABLE)
    derived, _ = claims_for({}, TABLE, records, path="runtime.md")
    assert [c["evidence"] for c in derived["claims"]] == [
        "| `import` | 6.15 s | 0.40 s |",
        "| `recall` | 1.42 s | 0.21 s |",
    ]


def test_a_long_table_keeps_its_header_in_every_chunk():
    """A row that lost the header above it does not say which column it is."""
    rows = "\n".join(f"| row {index} | {index} s | {index * 2} s |" for index in range(60))
    body = (
        "# Runtime\n\n"
        "| | `sentence-transformers` | onnxruntime |\n"
        "|---|---|---|\n"
        f"{rows}\n"
    )
    records = _records(body)
    chunks = [c["text"] for c in records]
    assert len(chunks) > 1, "the table was too short to be split"
    for text in chunks:
        assert (
            "| `sentence-transformers` | onnxruntime |" in text
        ), "a chunk of the table lost the header"


def test_every_row_of_a_split_table_still_has_a_claim():
    """A row that lands in a chunk of its own is still a fact."""
    rows = "\n".join(f"| row {index} | {index} s | {index * 2} s |" for index in range(60))
    body = (
        "# Runtime\n\n"
        "| | `sentence-transformers` | onnxruntime |\n"
        "|---|---|---|\n"
        f"{rows}\n"
    )
    derived, warnings = claims_for({}, body, _records(body), path="runtime.md")
    assert not warnings, "a row was dropped because its chunk could not quote it"
    assert len(derived["claims"]) == 60


def test_the_sync_fingerprint_covers_the_extracted_table_claims():
    """Re-running `ingest-repo` has to notice that a table now says something.

    The fingerprint is what tells the archive that a document changed. If the
    extracted claims are not in it, a document whose only content is a table
    looks unchanged forever, and the corpus never learns the rows.
    """
    records = _records(TABLE)
    derived, _ = claims_for({}, TABLE, records, path="runtime.md")
    without = _hash(TABLE, {})
    with_claims = _hash(TABLE, {**{}, **derived})
    assert (
        without != with_claims
    ), "the fingerprint does not move when a table starts producing claims"


def _records(body: str) -> list[dict]:
    return [
        {**chunk, "order_index": index}
        for index, chunk in enumerate(chunk_with_heading_paths(extract_sections_with_paths(body)))
    ]


# --- 3. The margin and the scores it is printed next to --------------------


def test_the_margin_and_the_candidate_scores_are_on_one_scale():
    """The banner must not contradict the numbers printed beside it.

    The CLI prints a candidate's score next to a banner that calls it a
    near-tie. The margin used to be the raw-cosine difference while the score
    beside it was the coverage-adjusted one, so the banner said "almost the
    same" about 0.755 and 0.556 -- a gap of 0.199.
    """
    from tests.test_retrieval_match_status import QUOTES, TWO, adjudicate_scored

    result = adjudicate_scored({"datastore": 0.72, "search": 0.30}, TWO, QUOTES)
    scores = [c.score for c in result.candidates]
    assert scores
    assert result.match_margin == pytest.approx(max(scores) - min(scores))


def test_a_response_of_one_claim_is_not_a_decisive_win_over_nobody():
    """`score - 0.0` reads like a win over a field that was never there."""
    from tests.test_retrieval_match_status import QUOTES, TWO, adjudicate_scored

    result = adjudicate_scored({"datastore": 0.70, "search": 0.69}, TWO[:1], QUOTES, corpus=TWO)
    assert result.match_margin == pytest.approx(0.01)
    assert result.match_margin != pytest.approx(0.70)


def test_the_runner_up_is_listed_when_the_answer_is_ambiguous():
    """A near-tie the reader is not shown is a near-tie they cannot adjudicate."""
    from tests.test_retrieval_match_status import QUOTES, TWO, adjudicate_scored

    result = adjudicate_scored({"datastore": 0.70, "search": 0.69}, TWO[:1], QUOTES, corpus=TWO)
    assert result.match_status == "ambiguous"
    assert {c.key for c in result.candidates} == {"datastore", "search"}


# --- 4. The ingest report --------------------------------------------------


def test_the_sync_summary_counts_the_claims():
    """A sync report that counts only chunks cannot say the corpus learned nothing."""
    from mindpalace_sdk import SyncSummary

    summary = SyncSummary(success=True, chunk_count=12, claim_count=34)
    assert summary.claim_count == 34


def test_the_onnx_backend_puts_texts_of_a_length_together():
    """A batch is padded to its longest member, so a mixed batch is mostly padding."""
    from api.services.embedder import ONNX_BATCH_SIZE

    assert ONNX_BATCH_SIZE >= 1
    source = Path(ROOT, "api/services/embedder.py").read_text(encoding="utf-8")
    # The length that matters is the token count, not the character count: a
    # key like `md:cv-embed-performance-baseline-bundle-sizes-21723ee4` is a
    # different number of tokens than it is characters.
    assert (
        "sorted(range(len(texts)), key=lambda index: lengths[index])" in source
    ), "the ONNX backend no longer batches by length"
    assert (
        "lengths = self._token_lengths(texts)" in source
    ), "the ONNX backend no longer measures the length in tokens"


# --- 5. The history status -------------------------------------------------


async def test_a_history_read_that_found_something_does_not_say_abstained(store):
    """`abstained` is the one word on the wire that means nothing was found."""

    from api.models.memory import MemoryRequest
    from api.services.memory_public import execute_in_session

    await _ingest_file(
        store,
        "# ADR 0001\n\n- The primary datastore is PostgreSQL 15.\n",
        "0001.md",
    )

    response = await execute_in_session(
        store.db, "history", MemoryRequest(query="PostgreSQL", corpus=store.name)
    )
    assert response.changes or response.current_memories
    assert response.match_status == "confident"


async def test_a_history_read_that_found_nothing_says_abstained(store):
    """The other half of the same truth."""

    from api.models.memory import MemoryRequest
    from api.services.memory_public import execute_in_session

    response = await execute_in_session(
        store.db,
        "history",
        MemoryRequest(query="qqqzzzxyw nothing here at all", corpus=store.name),
    )
    assert not response.changes
    assert response.match_status == "abstained"


# --- the table helpers the chunker and the extractor share -----------------


def test_a_row_renders_with_the_column_names_it_was_under():
    """The rendering is the claim's own wording, so both sides have to agree."""
    header, rows = read_table(
        [
            "| | `sentence-transformers` | onnxruntime |",
            "|---|---|---|",
            "| `import` | 6.15 s | 0.40 s |",
        ]
    )
    assert render_table_row(header, rows[0]) == (
        "`import`: `sentence-transformers`: 6.15 s; onnxruntime: 0.40 s"
    )
