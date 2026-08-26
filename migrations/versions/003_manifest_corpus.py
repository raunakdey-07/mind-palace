"""Scope ingestion manifest by corpus.

The manifest previously keyed on path alone, which collided when two corpora
contained documents at the same relative path.
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision = "003_manifest_corpus"
down_revision = "002_corpora"
branch_labels = None
depends_on = None

DEFAULT_CORPUS = "00000000000000000000000000000000000000000000000000000000default"


def upgrade() -> None:
    op.drop_constraint("uq_manifest_path", "ingestion_manifest", type_="unique")
    op.drop_constraint("ingestion_manifest_pkey", "ingestion_manifest", type_="primary")
    op.add_column(
        "ingestion_manifest",
        sa.Column(
            "corpus_id",
            sa.CHAR(64),
            nullable=False,
            server_default=DEFAULT_CORPUS,
        ),
    )
    op.create_primary_key("ingestion_manifest_pkey", "ingestion_manifest", ["corpus_id", "path"])


def downgrade() -> None:
    op.drop_constraint("ingestion_manifest_pkey", "ingestion_manifest", type_="primary")
    op.drop_column("ingestion_manifest", "corpus_id")
    op.create_primary_key("ingestion_manifest_pkey", "ingestion_manifest", ["path"])
