"""Multiple distinct references per claim, without changing captured snapshots."""

from alembic import op

revision = "005_multiple_evidence"
down_revision = "004_memory"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.drop_constraint("memory_evidence_corpus_id_claim_id_key", "memory_evidence", type_="unique")
    op.create_unique_constraint(
        "uq_memory_evidence_reference",
        "memory_evidence",
        ["corpus_id", "claim_id", "chunk_id", "start_offset", "end_offset"],
    )
    op.create_index("idx_memory_evidence_claim", "memory_evidence", ["corpus_id", "claim_id"])
    # Snapshots resolve evidence via version references. Serialize with the same
    # corpus lock as memory.snapshot, including for direct SQL evidence writers.
    op.execute("""
        CREATE FUNCTION memory_freeze_snapshot_evidence() RETURNS trigger LANGUAGE plpgsql AS $$
        BEGIN
            PERFORM pg_advisory_xact_lock(
                ('x' || substr(encode(sha256(convert_to('memory:' || rtrim(NEW.corpus_id),
                    'UTF8')), 'hex'), 1, 16))::bit(64)::bigint);
            IF EXISTS (SELECT 1 FROM memory_snapshot_versions
                WHERE corpus_id = NEW.corpus_id AND version_id = NEW.version_id) THEN
                RAISE EXCEPTION 'cannot add evidence to a snapshotted version'
                    USING ERRCODE = '23514';
            END IF;
            RETURN NEW;
        END $$
    """)
    op.execute("""
        CREATE TRIGGER memory_evidence_snapshot BEFORE INSERT ON memory_evidence
        FOR EACH ROW EXECUTE FUNCTION memory_freeze_snapshot_evidence()
    """)


def downgrade() -> None:
    # Lock before checking so a concurrent insert cannot invalidate the check.
    # Refuse before any DDL; never choose a surviving row or discard provenance.
    op.execute("LOCK TABLE memory_evidence IN ACCESS EXCLUSIVE MODE")
    op.execute("""
        DO $$ BEGIN
            IF EXISTS (SELECT 1 FROM memory_evidence
                GROUP BY corpus_id, claim_id HAVING count(*) > 1) THEN
                RAISE EXCEPTION 'cannot downgrade: multiple evidence references per claim'
                    USING ERRCODE = '23514';
            END IF;
        END $$
    """)
    op.execute("DROP TRIGGER memory_evidence_snapshot ON memory_evidence")
    op.execute("DROP FUNCTION memory_freeze_snapshot_evidence()")
    op.drop_index("idx_memory_evidence_claim", table_name="memory_evidence")
    op.drop_constraint("uq_memory_evidence_reference", "memory_evidence", type_="unique")
    op.create_unique_constraint(
        "memory_evidence_corpus_id_claim_id_key", "memory_evidence", ["corpus_id", "claim_id"]
    )
