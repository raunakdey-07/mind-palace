"""Seal snapshot membership after references are captured."""

from alembic import op

revision = "006_snapshot_membership_seal"
down_revision = "005_multiple_evidence"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("""
        CREATE TABLE memory_snapshot_seals (
            corpus_id CHAR(64) NOT NULL,
            snapshot_id CHAR(64) NOT NULL,
            PRIMARY KEY (corpus_id, snapshot_id),
            FOREIGN KEY (corpus_id, snapshot_id)
                REFERENCES memory_snapshots(corpus_id, id)
        )
    """)
    op.execute("""
        CREATE TRIGGER memory_snapshot_seals_immutable
        BEFORE UPDATE OR DELETE OR TRUNCATE ON memory_snapshot_seals
        FOR EACH STATEMENT EXECUTE FUNCTION memory_reject_mutation()
    """)
    # Snapshots created under 004/005 already contain their complete membership.
    # Mark them sealed without changing references or replay metadata.
    op.execute("""
        INSERT INTO memory_snapshot_seals(corpus_id, snapshot_id)
        SELECT corpus_id, id FROM memory_snapshots
    """)
    op.execute("""
        CREATE FUNCTION memory_reject_sealed_snapshot_membership() RETURNS trigger
        LANGUAGE plpgsql AS $$
        DECLARE
            target_corpus CHAR(64);
        BEGIN
            FOR target_corpus IN
                SELECT DISTINCT inserted.corpus_id
                FROM inserted_memberships AS inserted
                ORDER BY inserted.corpus_id
            LOOP
                PERFORM pg_advisory_xact_lock(
                    ('x' || substr(encode(sha256(convert_to(
                        'memory:' || rtrim(target_corpus), 'UTF8')), 'hex'), 1, 16))
                        ::bit(64)::bigint
                );
                IF EXISTS (
                    SELECT 1
                    FROM inserted_memberships AS inserted
                    JOIN memory_snapshot_seals AS sealed
                      ON sealed.corpus_id = inserted.corpus_id
                     AND sealed.snapshot_id = inserted.snapshot_id
                    WHERE inserted.corpus_id = target_corpus
                ) THEN
                    RAISE EXCEPTION 'cannot add a version to a sealed snapshot'
                        USING ERRCODE = '23514';
                END IF;
            END LOOP;
            RETURN NULL;
        END $$
    """)
    op.execute("""
        CREATE TRIGGER memory_snapshot_membership_seal
        AFTER INSERT ON memory_snapshot_versions
        REFERENCING NEW TABLE AS inserted_memberships
        FOR EACH STATEMENT EXECUTE FUNCTION memory_reject_sealed_snapshot_membership()
    """)
    op.execute("""
        CREATE FUNCTION memory_require_snapshot_seal() RETURNS trigger LANGUAGE plpgsql AS $$
        BEGIN
            IF NOT EXISTS (
                SELECT 1 FROM memory_snapshot_seals
                WHERE corpus_id = NEW.corpus_id AND snapshot_id = NEW.id
            ) THEN
                RAISE EXCEPTION 'snapshot requires a seal' USING ERRCODE = '23514';
            END IF;
            RETURN NULL;
        END $$
    """)
    op.execute("""
        CREATE CONSTRAINT TRIGGER memory_snapshot_requires_seal
        AFTER INSERT ON memory_snapshots
        DEFERRABLE INITIALLY DEFERRED FOR EACH ROW
        EXECUTE FUNCTION memory_require_snapshot_seal()
    """)


def downgrade() -> None:
    op.execute("DROP TRIGGER memory_snapshot_requires_seal ON memory_snapshots")
    op.execute("DROP FUNCTION memory_require_snapshot_seal()")
    op.execute("DROP TRIGGER memory_snapshot_membership_seal ON memory_snapshot_versions")
    op.execute("DROP FUNCTION memory_reject_sealed_snapshot_membership()")
    op.execute("DROP TABLE memory_snapshot_seals")
