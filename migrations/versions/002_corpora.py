"""Add corpora table and corpus scoping to documents.

Corpora are explicit namespaces: every document belongs to exactly one
corpus, and all retrieval/ingestion operations are scoped by corpus so
searches cannot leak across corpora.
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision = "002_corpora"
down_revision = "001_initial_schema"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "corpora",
        sa.Column("id", sa.CHAR(64), primary_key=True),
        sa.Column("name", sa.Text(), nullable=False, unique=True),
        sa.Column("description", sa.Text(), nullable=False, server_default=""),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
    )

    # Every existing document belongs to a default corpus so the migration
    # is safe on databases that already hold data.
    op.execute(
        "INSERT INTO corpora (id, name, description) "
        "VALUES ('00000000000000000000000000000000000000000000000000000000default', 'default', "
        "'Migrated pre-corpus documents') ON CONFLICT DO NOTHING"
    )
    op.add_column(
        "documents",
        sa.Column(
            "corpus_id",
            sa.CHAR(64),
            nullable=False,
            server_default="00000000000000000000000000000000000000000000000000000000default",
        ),
    )

    op.create_foreign_key(
        "fk_documents_corpus",
        "documents",
        "corpora",
        ["corpus_id"],
        ["id"],
        ondelete="CASCADE",
    )
    # Document paths are unique per corpus, not globally: two corpora may
    # each contain a document at the same relative path.
    op.drop_constraint("documents_path_key", "documents", type_="unique")
    op.create_unique_constraint("uq_documents_corpus_path", "documents", ["corpus_id", "path"])
    op.create_index("idx_documents_corpus", "documents", ["corpus_id"])


def downgrade() -> None:
    op.drop_index("idx_documents_corpus", table_name="documents")
    op.drop_constraint("uq_documents_corpus_path", "documents", type_="unique")
    op.create_unique_constraint("documents_path_key", "documents", ["path"])
    op.drop_constraint("fk_documents_corpus", "documents", type_="foreignkey")
    op.drop_column("documents", "corpus_id")
    op.drop_table("corpora")
