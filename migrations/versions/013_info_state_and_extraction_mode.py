"""Add info_state and extraction_mode columns to memory_claims."""

from alembic import op

revision = "013_info_state"
down_revision = "012_claim_embedding_hnsw"


def upgrade() -> None:
    op.execute("""
        ALTER TABLE memory_claims
        ADD COLUMN IF NOT EXISTS info_state TEXT NOT NULL DEFAULT 'approved'
    """)
    op.execute("""
        ALTER TABLE memory_claims
        ADD COLUMN IF NOT EXISTS extraction_mode TEXT NOT NULL DEFAULT 'deterministic'
    """)
    op.execute("""
        ALTER TABLE memory_claims
        ADD COLUMN IF NOT EXISTS provenance_state TEXT NOT NULL DEFAULT 'direct_claim'
    """)


def downgrade() -> None:
    op.execute("ALTER TABLE memory_claims DROP COLUMN IF EXISTS provenance_state")
    op.execute("ALTER TABLE memory_claims DROP COLUMN IF EXISTS extraction_mode")
    op.execute("ALTER TABLE memory_claims DROP COLUMN IF EXISTS info_state")
