"""The chunk vectors nobody reads, and the ONNX session that was slow.

Three promises, each of which the ingest depends on:

* **A document that produced claims is not embedded a second time.** `recall`,
  `history`, `explain` and `receipt` read `memory_claim_embeddings`, never
  `chunks.embedding`. The chunk vectors are the legacy chunk-level index, and
  computing one for every chunk of every document was the largest single cost
  in a sync: 59.3 s of a 112.3 s ingest on a 78-document repository.
* **A document that produced no claims keeps its vectors.** The chunk index is
  the only index such a document has, so a vector is the only way anything can
  find it.
* **The ONNX session runs at the speed the machine can actually reach.** The
  graph is optimised at every level, the session gets one thread per physical
  core rather than per logical processor, and one long text cannot pad a whole
  batch out to its own length.

The database tests run against the schema the product ships, in a rolled-back
transaction, so a failure here is a failure of the product and not of a fixture.
"""

from __future__ import annotations

import os

from api.services.embedder import (
    MAX_PADDING_RATIO,
    ONNX_BATCH_SIZE,
    _batches,
    _intra_op_threads,
    physical_cores,
)

#: A document with claim-shaped structure: a bullet under a heading.
CLAIM_BEARING = """---
title: "Chunk Vectors"
---

# Decisions

- The production datastore is PostgreSQL.
"""

#: A document with prose only. Nothing in it is claim-shaped, so it contributes
#: no claim and the chunk index is the only index it has.
PROSE_ONLY = """---
title: "Prose Only"
---

# Introduction

This is an introduction section with prose in it and nothing claim-shaped.
"""


async def _ingest(store, body: str, path: str, *, embed_chunks: bool | None = None):
    """Archive one document, the way `ingest-repo` does."""
    from api.services import ingestion as ingestion_service

    service = ingestion_service.IngestionService(memory_enabled=True)
    return await service.ingest_file(
        content=body, path=path, corpus_id=store.corpus, embed_chunks=embed_chunks
    )


async def _chunk_vectors(store, path: str) -> list:
    """The vectors stored on a document's chunks, in order."""
    from sqlalchemy import text

    rows = (
        await store.db.execute(
            text(
                "SELECT c.embedding FROM chunks c JOIN documents d ON c.doc_id = d.id "
                "WHERE d.corpus_id = :c AND d.path = :p ORDER BY c.order_index"
            ),
            {"c": store.corpus, "p": path},
        )
    ).all()
    return [row[0] for row in rows]


async def _claim_vectors(store, path: str, corpus: str | None = None) -> list:
    """The vectors stored for a document's claims, in order."""
    from sqlalchemy import text

    rows = (
        await store.db.execute(
            text(
                "SELECT e.embedding FROM memory_claim_embeddings e "
                "JOIN memory_claims c ON c.id = e.claim_id "
                "JOIN memory_versions v ON v.id = c.version_id "
                "JOIN memory_documents d ON d.id = v.memory_document_id "
                "WHERE e.corpus_id = :c AND d.path = :p ORDER BY c.key"
            ),
            {"c": corpus or store.corpus, "p": path},
        )
    ).all()
    return [row[0] for row in rows]


async def test_a_document_that_produced_claims_is_not_embedded_again(store):
    """The chunk vectors of a claim-bearing document are left null.

    The claim archive answers every read the product offers for that document,
    so the vectors would be computed for an index nothing queries.
    """
    result = await _ingest(store, CLAIM_BEARING, "chunk-vectors/claims.md")
    assert result["success"] is True
    assert result["claim_count"] >= 1, "the document produced no claim to answer from"

    vectors = await _chunk_vectors(store, "chunk-vectors/claims.md")
    assert vectors, "the document produced no chunks at all"
    assert all(
        vector is None for vector in vectors
    ), "the chunk vectors of a claim-bearing document were computed anyway"


async def test_a_document_with_no_claims_keeps_its_vectors(store):
    """The other case, and it is not the same case.

    A document with no claims contributes no claim, so the chunk index is the
    only index it has. A vector is the only way anything can find it.
    """
    result = await _ingest(store, PROSE_ONLY, "chunk-vectors/prose.md")
    assert result["success"] is True
    assert result["claim_count"] == 0, "the document produced a claim after all"

    vectors = await _chunk_vectors(store, "chunk-vectors/prose.md")
    assert vectors, "the document produced no chunks at all"
    assert all(
        vector is not None for vector in vectors
    ), "a document with no claims lost its only index"


async def test_embed_chunks_asks_for_the_vectors_anyway(store):
    """The opt-in exists for a caller who wants the chunk-level search."""
    result = await _ingest(store, CLAIM_BEARING, "chunk-vectors/opt-in.md", embed_chunks=True)
    assert result["success"] is True
    assert result["claim_count"] >= 1

    vectors = await _chunk_vectors(store, "chunk-vectors/opt-in.md")
    assert vectors, "the document produced no chunks at all"
    assert all(
        vector is not None for vector in vectors
    ), "--embed-chunks did not produce the chunk vectors"


async def test_a_chunk_search_fills_the_index_and_answers(store):
    """The chunk index is not dead: the search gives the chunks a vector first.

    A corpus synced without chunk vectors has chunks the search cannot read.
    The search gives them one before it looks, so it answers instead of
    silently returning nothing for a corpus full of text.
    """
    from api.services.embedder import Embedder
    from api.services.retrieval import RetrievalService

    await _ingest(store, CLAIM_BEARING, "chunk-vectors/search.md")
    assert all(vector is None for vector in await _chunk_vectors(store, "chunk-vectors/search.md"))

    embedder = Embedder()
    vector = embedder.embed_single("production datastore PostgreSQL")
    results = await RetrievalService(store.db, embedder=embedder).search(vector, k=5)

    assert results, "the chunk search found nothing in a corpus that holds the text"
    assert all(
        vector is not None for vector in await _chunk_vectors(store, "chunk-vectors/search.md")
    )


async def test_the_claim_vectors_are_the_same_either_way(store):
    """Skipping the chunk vectors changes what is stored for the claims.

    It is the claim vectors the read path uses, so they have to be the same
    whether or not the chunk vectors were computed. The same document is
    archived into two corpora at the same path, because the claim
    representation carries the path and two paths would be two different
    representations.
    """
    import hashlib
    from uuid import uuid4

    from sqlalchemy import text

    from api.services import ingestion as ingestion_service

    other = hashlib.sha256(uuid4().bytes).hexdigest()
    await store.db.execute(
        text("INSERT INTO corpora(id, name) VALUES (:id, :name)"),
        {"id": other, "name": store.name + "-other"},
    )
    service = ingestion_service.IngestionService(memory_enabled=True)
    await service.ingest_file(
        content=CLAIM_BEARING, path="chunk-vectors/same.md", corpus_id=store.corpus
    )
    await service.ingest_file(
        content=CLAIM_BEARING, path="chunk-vectors/same.md", corpus_id=other, embed_chunks=True
    )

    without = await _claim_vectors(store, "chunk-vectors/same.md")
    with_chunks = await _claim_vectors(store, "chunk-vectors/same.md", corpus=other)
    assert without and with_chunks
    assert [str(v) for v in without] == [str(v) for v in with_chunks]


def test_one_long_text_cannot_pad_the_batch_it_shares():
    """An outlier gets a batch of its own.

    A batch is padded to its longest member, so a batch holding one long text
    and seven short ones makes all eight pay for the long one. A claim
    representation is a single lead clause, so an outlier is one document's
    unusually long first line.
    """
    lengths = [10, 11, 12, 13, 14, 15, 16, 17, 400, 401]
    order = sorted(range(len(lengths)), key=lambda index: lengths[index])

    batches = _batches(order, lengths)

    short = next(batch for batch in batches if 0 in batch)
    assert 8 not in short, "the 400-token text shared a batch with the short ones"
    # The short texts are padded to their own longest member, not to 401.
    assert max(lengths[index] for index in short) <= 17


def test_batches_stay_within_the_batch_size():
    """The guard never makes a batch larger than the session's batch size."""
    lengths = list(range(1, 200))
    order = sorted(range(len(lengths)), key=lambda index: lengths[index])
    for batch in _batches(order, lengths):
        assert len(batch) <= ONNX_BATCH_SIZE


def test_the_padding_ratio_is_bounded():
    """The guard is a bound, not a guess: a batch is never spread wider than it.

    Every batch holds texts whose longest is at most `MAX_PADDING_RATIO` times
    its shortest, except for a batch of one, which cannot be spread at all.
    """
    lengths = [5, 6, 7, 8, 9, 10, 11, 12, 13, 14, 60, 61, 200]
    order = sorted(range(len(lengths)), key=lambda index: lengths[index])
    for batch in _batches(order, lengths):
        if len(batch) == 1:
            continue
        shortest = min(lengths[index] for index in batch)
        longest = max(lengths[index] for index in batch)
        assert longest <= shortest * MAX_PADDING_RATIO


def test_the_session_is_told_to_optimise_the_graph():
    """The ONNX session optimises the graph at every level the runtime knows."""
    import onnxruntime as ort

    from api.services.embedder import _session_options

    options = _session_options()
    assert options.graph_optimization_level == ort.GraphOptimizationLevel.ORT_ENABLE_ALL


def test_the_session_gets_one_thread_per_physical_core():
    """The thread count is the physical cores, not the logical processors.

    onnxruntime's default is one thread per logical processor, and on a machine
    with SMT the two halves of a core fight for the same execution units. The
    vectors are bit-identical either way; the wall clock is not.
    """
    cores = physical_cores()
    threads = _intra_op_threads()
    if cores is None:
        # The machine does not say. The runtime's own default is then correct,
        # and asking for zero threads is how it says so.
        assert threads == 0
        return
    assert threads == cores
    assert threads <= (os.cpu_count() or cores)


def test_the_thread_count_can_be_overridden():
    """An operator whose machine this cannot see can say what to use."""
    os.environ["MIND_PALACE_ONNX_THREADS"] = "3"
    try:
        assert _intra_op_threads() == 3
    finally:
        os.environ.pop("MIND_PALACE_ONNX_THREADS")
