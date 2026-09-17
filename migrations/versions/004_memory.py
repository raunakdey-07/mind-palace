"""Append-only relational memory, independent of the disposable live index.

Backfill identities only. The legacy schema has neither full raw source bodies
nor complete authored metadata; chunks cannot reconstruct either losslessly.
No versions, claims, or observation times are invented. The first subsequent
record_version call archives a NEW observation, not a historical creation time.

Memory is retained even after live rows disappear. Corpus deletion is restricted
while archive rows exist; deliberate archive erasure requires a separate policy.
"""

from __future__ import annotations

from alembic import op

revision = "004_memory"
down_revision = "003_manifest_corpus"
branch_labels = None
depends_on = None

TABLES = (
    "memory_documents",
    "memory_versions",
    "memory_chunks",
    "memory_claims",
    "memory_evidence",
    "memory_snapshots",
    "memory_snapshot_versions",
)


def upgrade() -> None:
    op.execute("""
        CREATE TABLE memory_documents (
            corpus_id CHAR(64) NOT NULL REFERENCES corpora(id),
            id CHAR(64) NOT NULL,
            path TEXT NOT NULL,
            PRIMARY KEY (corpus_id, id),
            UNIQUE (corpus_id, path),
            CHECK (id = encode(sha256(convert_to(rtrim(corpus_id) || ':' || path,
                                                 'UTF8')), 'hex'))
        )
    """)
    op.execute("""
        CREATE TABLE memory_versions (
            corpus_id CHAR(64) NOT NULL,
            memory_document_id CHAR(64) NOT NULL,
            id CHAR(64) NOT NULL,
            predecessor_id CHAR(64),
            version_number INTEGER NOT NULL CHECK (version_number > 0),
            document_id TEXT,
            event TEXT NOT NULL CHECK (event IN ('NEW', 'MODIFIED', 'RESTORED', 'DELETED')),
            observed_at TIMESTAMPTZ NOT NULL DEFAULT clock_timestamp(),
            content TEXT,
            metadata JSONB,
            fingerprint CHAR(64),
            PRIMARY KEY (corpus_id, id),
            UNIQUE (corpus_id, memory_document_id, id),
            UNIQUE (corpus_id, memory_document_id, version_number),
            UNIQUE (corpus_id, predecessor_id),
            FOREIGN KEY (corpus_id, memory_document_id)
                REFERENCES memory_documents(corpus_id, id),
            FOREIGN KEY (corpus_id, memory_document_id, predecessor_id)
                REFERENCES memory_versions(corpus_id, memory_document_id, id),
            CHECK ((event = 'NEW' AND predecessor_id IS NULL AND version_number = 1)
                OR (event <> 'NEW' AND predecessor_id IS NOT NULL AND version_number > 1)),
            CHECK ((event = 'DELETED' AND content IS NULL AND metadata IS NULL
                        AND fingerprint IS NULL)
                OR (event <> 'DELETED' AND content IS NOT NULL AND metadata IS NOT NULL
                        AND fingerprint IS NOT NULL AND document_id IS NOT NULL)),
            CHECK (metadata IS NULL OR jsonb_typeof(metadata) = 'object')
        )
    """)
    op.execute("""
        CREATE UNIQUE INDEX uq_memory_root ON memory_versions(corpus_id, memory_document_id)
        WHERE predecessor_id IS NULL
    """)
    op.execute("""
        CREATE INDEX idx_memory_versions_observed ON memory_versions(corpus_id, observed_at, id)
    """)
    op.execute("""
        CREATE TABLE memory_chunks (
            corpus_id CHAR(64) NOT NULL,
            version_id CHAR(64) NOT NULL,
            id CHAR(64) NOT NULL,
            text TEXT NOT NULL,
            heading_path TEXT,
            order_index INTEGER NOT NULL CHECK (order_index >= 0),
            PRIMARY KEY (corpus_id, id),
            UNIQUE (corpus_id, version_id, id),
            UNIQUE (corpus_id, version_id, order_index),
            FOREIGN KEY (corpus_id, version_id) REFERENCES memory_versions(corpus_id, id)
        )
    """)
    op.execute("""
        CREATE TABLE memory_claims (
            corpus_id CHAR(64) NOT NULL,
            memory_document_id CHAR(64) NOT NULL,
            version_id CHAR(64) NOT NULL,
            id CHAR(64) NOT NULL,
            key TEXT NOT NULL CHECK (key <> ''),
            value JSONB NOT NULL,
            claim TEXT NOT NULL CHECK (claim <> ''),
            valid_from TIMESTAMPTZ,
            valid_until TIMESTAMPTZ,
            supersedes_id CHAR(64),
            supersession_basis TEXT CHECK (
                supersession_basis IN ('same_document_key', 'single_claim_replacement')
            ),
            CHECK (supersedes_id IS NULL OR supersedes_id <> id),
            CHECK ((supersedes_id IS NULL) = (supersession_basis IS NULL)),
            PRIMARY KEY (corpus_id, id),
            UNIQUE (corpus_id, version_id, key),
            UNIQUE (corpus_id, version_id, id),
            UNIQUE (corpus_id, memory_document_id, key, id),
            FOREIGN KEY (corpus_id, memory_document_id, version_id)
                REFERENCES memory_versions(corpus_id, memory_document_id, id),
            FOREIGN KEY (corpus_id, supersedes_id)
                REFERENCES memory_claims(corpus_id, id),
            CHECK (valid_until IS NULL OR valid_from IS NULL OR valid_until >= valid_from)
        )
    """)
    op.execute("CREATE INDEX idx_memory_claims_key ON memory_claims(corpus_id, key)")
    op.execute(
        "CREATE INDEX idx_memory_claims_validity "
        "ON memory_claims(corpus_id, valid_from, valid_until)"
    )
    op.execute("""
        CREATE INDEX idx_memory_claims_supersedes ON memory_claims(corpus_id, supersedes_id)
        WHERE supersedes_id IS NOT NULL
    """)
    op.execute("""
        CREATE TABLE memory_evidence (
            corpus_id CHAR(64) NOT NULL,
            version_id CHAR(64) NOT NULL,
            id CHAR(64) NOT NULL,
            claim_id CHAR(64) NOT NULL,
            chunk_id CHAR(64) NOT NULL,
            quote TEXT NOT NULL CHECK (quote <> ''),
            start_offset INTEGER NOT NULL CHECK (start_offset >= 0),
            end_offset INTEGER NOT NULL,
            PRIMARY KEY (corpus_id, id),
            UNIQUE (corpus_id, claim_id),
            FOREIGN KEY (corpus_id, version_id, claim_id)
                REFERENCES memory_claims(corpus_id, version_id, id),
            FOREIGN KEY (corpus_id, version_id, chunk_id)
                REFERENCES memory_chunks(corpus_id, version_id, id),
            CHECK (end_offset = start_offset + char_length(quote))
        )
    """)
    op.execute("""
        CREATE INDEX idx_memory_evidence_chunk ON memory_evidence(corpus_id, version_id, chunk_id)
    """)
    op.execute("""
        CREATE TABLE memory_snapshots (
            corpus_id CHAR(64) NOT NULL REFERENCES corpora(id),
            id CHAR(64) NOT NULL,
            observed_at TIMESTAMPTZ NOT NULL DEFAULT clock_timestamp(),
            as_of TIMESTAMPTZ NOT NULL,
            PRIMARY KEY (corpus_id, id),
            CHECK (as_of <= observed_at)
        )
    """)
    op.execute("""
        CREATE INDEX idx_memory_snapshots_observed ON memory_snapshots(corpus_id, observed_at)
    """)
    op.execute("""
        CREATE TABLE memory_snapshot_versions (
            corpus_id CHAR(64) NOT NULL,
            snapshot_id CHAR(64) NOT NULL,
            version_id CHAR(64) NOT NULL,
            PRIMARY KEY (corpus_id, snapshot_id, version_id),
            FOREIGN KEY (corpus_id, snapshot_id) REFERENCES memory_snapshots(corpus_id, id),
            FOREIGN KEY (corpus_id, version_id) REFERENCES memory_versions(corpus_id, id)
        )
    """)
    op.execute("""
        CREATE INDEX idx_memory_snapshot_version ON memory_snapshot_versions(corpus_id, version_id)
    """)
    # Enforce exact, case-sensitive, character-offset evidence even for SQL writers.
    op.execute("""
        CREATE FUNCTION memory_validate_evidence() RETURNS trigger LANGUAGE plpgsql AS $$
        BEGIN
            IF NOT EXISTS (
                SELECT 1 FROM memory_chunks
                WHERE corpus_id = NEW.corpus_id AND version_id = NEW.version_id
                  AND id = NEW.chunk_id
                  AND substring(text FROM NEW.start_offset + 1
                                FOR char_length(NEW.quote)) = NEW.quote
            ) THEN
                RAISE EXCEPTION 'evidence must exactly match its archived chunk'
                    USING ERRCODE = '23514';
            END IF;
            RETURN NEW;
        END $$
    """)
    op.execute("""
        CREATE TRIGGER memory_evidence_exact BEFORE INSERT ON memory_evidence
        FOR EACH ROW EXECUTE FUNCTION memory_validate_evidence()
    """)
    # Deferred so the claim and its evidence can be inserted in one transaction.
    op.execute("""
        CREATE FUNCTION memory_require_evidence() RETURNS trigger LANGUAGE plpgsql AS $$
        BEGIN
            IF NOT EXISTS (SELECT 1 FROM memory_evidence
                WHERE corpus_id = NEW.corpus_id AND claim_id = NEW.id) THEN
                RAISE EXCEPTION 'claim requires evidence' USING ERRCODE = '23514';
            END IF;
            RETURN NEW;
        END $$
    """)
    op.execute("""
        CREATE CONSTRAINT TRIGGER memory_claim_requires_evidence AFTER INSERT ON memory_claims
        DEFERRABLE INITIALLY DEFERRED FOR EACH ROW EXECUTE FUNCTION memory_require_evidence()
    """)
    # Edges can only point to claims in the immediate predecessor source version.
    # Together with append-only rows this rules out cycles, including direct SQL writes.
    op.execute("""
        CREATE FUNCTION memory_validate_supersession() RETURNS trigger LANGUAGE plpgsql AS $$
        BEGIN
            IF NEW.supersedes_id IS NOT NULL AND NOT EXISTS (
                SELECT 1 FROM memory_claims prior
                JOIN memory_versions v ON v.corpus_id = NEW.corpus_id AND v.id = NEW.version_id
                WHERE prior.corpus_id = NEW.corpus_id AND prior.id = NEW.supersedes_id
                  AND prior.memory_document_id = NEW.memory_document_id
                  AND prior.version_id = v.predecessor_id
                  AND (NEW.supersession_basis = 'single_claim_replacement' OR prior.key = NEW.key)
            ) THEN
                RAISE EXCEPTION
                    'supersession requires an immediate predecessor claim in the same document'
                    USING ERRCODE = '23514';
            END IF;
            RETURN NEW;
        END $$
    """)
    op.execute("""
        CREATE TRIGGER memory_claim_supersession BEFORE INSERT ON memory_claims
        FOR EACH ROW EXECUTE FUNCTION memory_validate_supersession()
    """)
    op.execute("""
        CREATE FUNCTION memory_validate_predecessor() RETURNS trigger LANGUAGE plpgsql AS $$
        BEGIN
            IF NEW.predecessor_id IS NOT NULL AND NOT EXISTS (
                SELECT 1 FROM memory_versions prior WHERE prior.corpus_id = NEW.corpus_id
                    AND prior.id = NEW.predecessor_id
                    AND prior.memory_document_id = NEW.memory_document_id
                    AND prior.version_number = NEW.version_number - 1
                    AND prior.observed_at < NEW.observed_at
                    AND ((prior.event = 'DELETED' AND NEW.event = 'RESTORED')
                      OR (prior.event <> 'DELETED' AND NEW.event IN ('MODIFIED', 'DELETED')))
            ) THEN
                RAISE EXCEPTION 'invalid predecessor lifecycle or observation order'
                    USING ERRCODE = '23514';
            END IF;
            RETURN NEW;
        END $$
    """)
    op.execute("""
        CREATE TRIGGER memory_version_predecessor BEFORE INSERT ON memory_versions
        FOR EACH ROW EXECUTE FUNCTION memory_validate_predecessor()
    """)
    op.execute("""
        CREATE FUNCTION memory_reject_mutation() RETURNS trigger LANGUAGE plpgsql AS $$
        BEGIN
            RAISE EXCEPTION 'memory archive is append-only' USING ERRCODE = '23514';
        END $$
    """)
    for table in TABLES:
        op.execute(f"""
            CREATE TRIGGER {table}_immutable BEFORE UPDATE OR DELETE OR TRUNCATE ON {table}
            FOR EACH STATEMENT EXECUTE FUNCTION memory_reject_mutation()
        """)
    op.execute("""
        INSERT INTO memory_documents(corpus_id, id, path)
        SELECT corpus_id,
               encode(sha256(convert_to(rtrim(corpus_id) || ':' || path, 'UTF8')), 'hex'), path
        FROM documents
    """)


def downgrade() -> None:
    for table in reversed(TABLES):
        op.drop_table(table)
    op.execute("DROP FUNCTION memory_validate_evidence()")
    op.execute("DROP FUNCTION memory_require_evidence()")
    op.execute("DROP FUNCTION memory_validate_supersession()")
    op.execute("DROP FUNCTION memory_validate_predecessor()")
    op.execute("DROP FUNCTION memory_reject_mutation()")
