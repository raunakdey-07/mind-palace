"""Migration 016: governance events (append-only)."""

from alembic import op

revision = "016_governance_events"
down_revision = "015_rls_scope_enforcement"


def upgrade() -> None:
    op.execute("""
        CREATE TABLE memory_governance_events (
            event_id TEXT PRIMARY KEY,
            corpus_id CHAR(64) NOT NULL REFERENCES corpora(id),
            doc_path TEXT,
            heading_slug TEXT,
            claim_type TEXT,
            content_hash TEXT,
            event_type TEXT NOT NULL CHECK (event_type IN ('set_state','pin','unpin','approve')),
            info_state TEXT,
            pinned BOOLEAN,
            actor TEXT,
            reason TEXT,
            created_at TIMESTAMPTZ NOT NULL DEFAULT now()
        )
    """)
    op.execute("CREATE INDEX idx_gov_events_corpus ON memory_governance_events (corpus_id)")
    op.execute("CREATE INDEX idx_gov_events_target ON memory_governance_events (corpus_id, doc_path, heading_slug, claim_type, content_hash)")

    op.execute("""
        ALTER TABLE memory_governance_events ENABLE ROW LEVEL SECURITY
    """)
    op.execute("""
        CREATE POLICY memory_governance_events_scope ON memory_governance_events
        USING (mindpalace_allowed_corpus(corpus_id))
        WITH CHECK (mindpalace_allowed_corpus(corpus_id))
    """)
    op.execute("""
        CREATE TRIGGER memory_governance_events_immutable
        BEFORE UPDATE OR DELETE OR TRUNCATE ON memory_governance_events
        FOR EACH STATEMENT EXECUTE FUNCTION memory_reject_mutation()
    """)

    # grants: runtime INSERT + SELECT only
    op.execute("""
        DO $$
        BEGIN
            IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'mindpalace_runtime') THEN
                EXECUTE 'GRANT INSERT, SELECT ON memory_governance_events TO mindpalace_runtime';
            END IF;
            IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'mindpalace_owner') THEN
                EXECUTE 'GRANT ALL ON memory_governance_events TO mindpalace_owner';
            END IF;
        END $$;
    """)


def downgrade() -> None:
    op.execute("DROP TABLE IF EXISTS memory_governance_events")
