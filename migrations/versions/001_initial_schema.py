"""Initial schema migration for Mind Palace."""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from pgvector.sqlalchemy import Vector
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision = "001_initial_schema"
down_revision = None
branch_labels = None
depends_on = None


def upgrade() -> None:
    # PostgreSQL server extensions must exist before the schema that uses
    # them. The Python `pgvector` package does NOT install the server
    # extension; the database image must provide it (pgvector/pgvector,
    # ankane/pgvector both do).
    op.execute("CREATE EXTENSION IF NOT EXISTS vector")
    op.execute("CREATE EXTENSION IF NOT EXISTS pgcrypto")  # gen_random_uuid()
    op.execute("CREATE EXTENSION IF NOT EXISTS pg_trgm")  # hybrid keyword search

    # Create documents table
    op.create_table(
        "documents",
        sa.Column("id", sa.CHAR(64), primary_key=True),
        sa.Column("title", sa.Text(), nullable=False),
        sa.Column("path", sa.Text(), unique=True, nullable=False),
        sa.Column("document_type", sa.Text(), nullable=False, server_default="note"),
        sa.Column("date", sa.Date(), nullable=True),
        sa.Column("summary", sa.Text(), nullable=True),
        sa.Column("tags", postgresql.ARRAY(sa.Text()), nullable=False, server_default="{}"),
        sa.Column("git_repo", sa.Text(), nullable=True),
        sa.Column("status", sa.Text(), nullable=False, server_default="active"),
        sa.Column("content_hash", sa.Text(), nullable=False),
        sa.Column(
            "last_indexed",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.Column("ingested_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("indexed_at", sa.DateTime(timezone=True), nullable=True),
    )

    # Create chunks table
    op.create_table(
        "chunks",
        sa.Column(
            "id",
            postgresql.UUID(as_uuid=True),
            primary_key=True,
            server_default=sa.text("gen_random_uuid()"),
        ),
        sa.Column(
            "doc_id",
            sa.CHAR(64),
            sa.ForeignKey("documents.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("order_index", sa.Integer(), nullable=False),
        sa.Column("text", sa.Text(), nullable=False),
        sa.Column("heading_path", sa.Text(), nullable=True),
        sa.Column("document_type", sa.Text(), nullable=True),
        sa.Column("source_url", sa.Text(), nullable=True),
        sa.Column("tags", postgresql.ARRAY(sa.Text()), nullable=False, server_default="{}"),
        sa.Column("language", sa.Text(), nullable=False, server_default="en"),
        sa.Column("token_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("embedding", Vector(384), nullable=True),
        sa.Column("embedding_model", sa.Text(), nullable=True),
        sa.Column("embedding_dimension", sa.Integer(), nullable=True),
        sa.Column("embedding_version", sa.Text(), nullable=True),
        sa.Column("metadata", postgresql.JSONB(), nullable=False, server_default="{}"),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
    )

    # Create ingestion_manifest table
    op.create_table(
        "ingestion_manifest",
        sa.Column("path", sa.Text(), primary_key=True),
        sa.Column("content_hash", sa.Text(), nullable=False),
        sa.Column(
            "doc_id",
            sa.CHAR(64),
            sa.ForeignKey("documents.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column("chunk_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column(
            "last_ingested",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
    )

    # Create indexes
    op.create_index(
        "idx_chunks_embedding",
        "chunks",
        ["embedding"],
        postgresql_using="hnsw",
        postgresql_ops={"embedding": "vector_cosine_ops"},
    )
    op.create_index("idx_chunks_doc_id", "chunks", ["doc_id"])
    op.create_index("idx_chunks_document_type", "chunks", ["document_type"])
    op.create_index("idx_chunks_heading_path", "chunks", ["heading_path"])
    op.create_index("idx_documents_type", "documents", ["document_type"])
    op.create_index("idx_documents_updated_at", "documents", ["updated_at"])
    op.create_index("idx_documents_ingested_at", "documents", ["ingested_at"])
    op.create_index("idx_documents_indexed_at", "documents", ["indexed_at"])

    # Add unique constraints
    op.create_unique_constraint("uq_documents_doc_id", "documents", ["id"])
    op.create_unique_constraint("uq_manifest_path", "ingestion_manifest", ["path"])
    op.create_unique_constraint("uq_chunks_doc_chunk", "chunks", ["doc_id", "order_index"])


def downgrade() -> None:
    # Drop constraints
    op.drop_constraint("uq_chunks_doc_chunk", "chunks", type_="unique")
    op.drop_constraint("uq_manifest_path", "ingestion_manifest", type_="unique")
    op.drop_constraint("uq_documents_doc_id", "documents", type_="unique")

    # Drop indexes
    op.drop_index("idx_documents_indexed_at", table_name="documents")
    op.drop_index("idx_documents_ingested_at", table_name="documents")
    op.drop_index("idx_documents_updated_at", table_name="documents")
    op.drop_index("idx_documents_type", table_name="documents")
    op.drop_index("idx_chunks_heading_path", table_name="chunks")
    op.drop_index("idx_chunks_document_type", table_name="chunks")
    op.drop_index("idx_chunks_doc_id", table_name="chunks")
    op.drop_index("idx_chunks_embedding", table_name="chunks")

    # Drop tables
    op.drop_table("ingestion_manifest")
    op.drop_table("chunks")
    op.drop_table("documents")

    # Extensions are left in place on downgrade: dropping them could affect
    # other databases/objects sharing the cluster, and they are cheap to keep.
