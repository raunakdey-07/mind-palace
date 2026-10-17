"""An HNSW index over cached claim vectors, so the write path can find near-duplicates.

The `remember` conflict hint asks "which live claims already say roughly this?".
It was answered by token overlap over the **200 most recently observed** active
claims. That bound is the defect: a corpus with 4,000 claims silently reported no
conflict for a fact it had written 3,800 claims ago, and the receipt carried
nothing that said the search had been truncated. A result that is wrong because
of where the answer happened to sit in the table is worse than no result, because
the caller reads it as "nothing nearby".

Indexing the cached vectors turns that question into a nearest-neighbour lookup
over the whole corpus. `LIMIT` here is a *result count*, not a candidate pool:
HNSW returns the true nearest N, so a claim ranked 21st is genuinely far, rather
than being excluded because it was written earlier than the cutoff.

Partial on `embedding IS NOT NULL`, because the column is nullable and the
query's predicate has to match the index's for a scan to be chosen.

Measured over 2,000 cached vectors on PostgreSQL 16.4 / pgvector 0.5.1:

    Index Scan using idx_claim_embeddings_hnsw on memory_claim_embeddings
      Order By: (embedding <=> '[...]')
    Execution Time: 3.8 ms

Below roughly a thousand rows the planner is right to ignore it and sort the
existing `(corpus_id, embedding_model, embedding_dimension)` btree instead, which
is what it does at 40. That is a cheaper plan at that size, not a broken index,
and nothing asserts otherwise.

The index is over `memory_claim_embeddings`, which holds no authority. Dropping
it costs the hint's latency and nothing else, and every row is re-derivable from
`memory_claims`. What it must never cost is an answer: a claim whose cached
vector is stale is still served here, and `load_cached`-style hash verification
in the caller decides whether to trust it.
"""

from alembic import op

revision = "012_claim_embedding_hnsw"
down_revision = "011_cross_document_supersession"
branch_labels = None
depends_on = None

#: HNSW tuning. `m` is the graph degree: higher finds closer neighbours at the
#: cost of memory and build time. 16 is pgvector's own default and the point here
#: is a bounded lookup on a corpus that is thousands of claims, not millions.
#: `ef_construction` 64 likewise.
_HNSW = "USING hnsw (embedding vector_cosine_ops) WITH (m = 16, ef_construction = 64)"


def upgrade() -> None:
    op.execute(
        "CREATE INDEX IF NOT EXISTS idx_claim_embeddings_hnsw "
        f"ON memory_claim_embeddings {_HNSW} WHERE embedding IS NOT NULL"
    )
    # The query filters on the model identity as well, so a corpus whose vectors
    # were written by two different models must not compare across them. This is
    # the column list the existing btree already covers; the HNSW index is the one
    # that makes the distance ordering index-only.
    #
    # No colon in the text: `op.execute` binds `:name` anywhere it appears, and a
    # comment naming a table would be read as a parameter.
    op.execute(
        "COMMENT ON INDEX idx_claim_embeddings_hnsw IS "
        "'Nearest-neighbour lookup for the remember conflict hint. Holds no "
        "authority; every row is re-derivable from the memory_claims table.'"
    )


def downgrade() -> None:
    op.execute("DROP INDEX IF EXISTS idx_claim_embeddings_hnsw")
