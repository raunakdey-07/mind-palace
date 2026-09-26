"""Cache claim representations so relevance scoring stops re-encoding the archive.

Authority resolution embedded every claim in the archive on every query. Measured
on the 202-question corpus that cost about 7.4 ms per claim, so an 88-claim corpus
spent 649 ms per question re-encoding text that cannot change. At 1,000 claims the
same line would cost seconds.

Claim representations are derived from immutable claim text, so they are a
legitimate L2 cache. This table holds no authority: dropping it changes latency
and nothing else, and every row is re-derivable from `memory_claims`.

Cache validity is explicit rather than assumed. A row is only used when the hash
of the exact embedded string, the model name and the dimension all still match
the claim being scored. A changed claim, a swapped model or a different
dimension each invalidate their own rows instead of being served stale.
"""

from alembic import op

revision = "007_claim_embedding_cache"
down_revision = "006_snapshot_membership_seal"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("""
        CREATE TABLE memory_claim_embeddings (
            corpus_id CHAR(64) NOT NULL,
            claim_id CHAR(64) NOT NULL,
            representation_hash CHAR(64) NOT NULL,
            embedding_model TEXT NOT NULL,
            embedding_dimension INTEGER NOT NULL,
            embedding_version TEXT NOT NULL,
            embedding vector(384),
            created_at TIMESTAMPTZ NOT NULL DEFAULT clock_timestamp(),
            updated_at TIMESTAMPTZ NOT NULL DEFAULT clock_timestamp(),
            PRIMARY KEY (corpus_id, claim_id),
            FOREIGN KEY (corpus_id, claim_id)
                REFERENCES memory_claims(corpus_id, id) ON DELETE CASCADE
        )
    """)
    op.execute("""
        CREATE INDEX idx_claim_embeddings_lookup
            ON memory_claim_embeddings (corpus_id, embedding_model, embedding_dimension)
    """)
    # A rebuild must be able to replace rows, unlike the archive itself.
    op.execute("""
        CREATE OR REPLACE FUNCTION memory_claim_embeddings_touch() RETURNS trigger
        LANGUAGE plpgsql AS $$
        BEGIN
            NEW.updated_at := clock_timestamp();
            RETURN NEW;
        END $$
    """)
    op.execute("""
        CREATE TRIGGER memory_claim_embeddings_touch
        BEFORE UPDATE ON memory_claim_embeddings
        FOR EACH ROW EXECUTE FUNCTION memory_claim_embeddings_touch()
    """)


def downgrade() -> None:
    op.execute("DROP TRIGGER memory_claim_embeddings_touch ON memory_claim_embeddings")
    op.execute("DROP FUNCTION memory_claim_embeddings_touch()")
    op.execute("DROP TABLE memory_claim_embeddings")
