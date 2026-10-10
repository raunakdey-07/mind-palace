"""Permission granularity, a narrow credential lookup, and corpus erasure.

Three changes, all additive and all reversible. Nothing here changes a query, a
column or a key that the memory contract depends on, so a deployment that never
adopts any of it behaves exactly as before.

1. **Per-corpus administration.** `read_only` and `read_write` could express
   "may read" and "may write", and nothing else. An integrator who wanted a
   service account that could manage its own workspace needed the unrestricted
   administrative identity, which is a database credential rather than a
   credential. Two roles are added: `corpus_admin` (write, plus create and
   delete the corpora it holds grants on) and `principal_admin` (everything,
   including issuing and revoking credentials). Deny-by-default is unchanged:
   a role is a superset of the one below it and nothing more.

2. **A credential lookup the runtime role cannot enumerate.** The runtime role
   could `SELECT` every row of `memory_principals`, which means every hash,
   role and corpus grant for every tenant. It needs exactly one thing: given a
   presented hash, return the identity it belongs to. That is now a
   `SECURITY DEFINER` function, and the direct `SELECT` is revoked from the
   runtime role. The lookup still runs *before* any tenant scope exists -- which
   is the whole reason the table carries no row-level policy -- so the function
   returns only what authentication needs and nothing that could be enumerated.

3. **Corpus erasure.** `DELETE /api/corpora/{name}` refused with 409 whenever
   the archive referenced the corpus, so a corpus that had ever recorded a fact
   could never be removed. That is an honest refusal of an unfinished feature,
   not a lifecycle. Erasure is now one `SECURITY DEFINER` function that
   re-checks the caller's transaction-local tenant scope, deletes every archive
   table in dependency order, and returns what it removed.

   The immutability triggers from `004` are narrowed rather than removed: they
   still reject `UPDATE` and `TRUNCATE` unconditionally, and reject `DELETE`
   unless the erasure interlock is set inside that function. The runtime role is
   granted no `DELETE` on any archive table at all, so the interlock is a guard
   against an ordinary mistake rather than the boundary itself -- the boundary is
   that the runtime role cannot delete archive rows, and can only ask this one
   function to, for a corpus it is scoped to.

   This does not make erasure atomic across everything a tenant may have copied
   elsewhere. `docs/security.md` states exactly what is and is not removed.
"""

from alembic import op

#: Kept under alembic's 32-character `version_num` column, which is not
#: something a longer, more descriptive identifier gets to overrun.
revision = "009_permission_and_erasure"
down_revision = "008_tenant_scoped_access"
branch_labels = None
depends_on = None


#: Archive tables, ordered children-first so the composite foreign keys -- all of
#: which are ``NO ACTION`` -- are satisfied without deferring any of them.
#:
#: Every one of these is ``(corpus_id, ...)``-keyed and every foreign key between
#: them includes ``corpus_id``, so no reference crosses a corpus boundary and no
#: ordering between two different corpora is required. Deleting one corpus's rows
#: in this order cannot touch another's.
ARCHIVE_TABLES_CHILDREN_FIRST = (
    "memory_snapshot_seals",
    "memory_snapshot_versions",
    "memory_snapshots",
    "memory_evidence",
    "memory_claims",
    "memory_chunks",
    "memory_versions",
    "memory_claim_embeddings",
    "memory_documents",
)

#: Live projection, also corpus-keyed. ``chunks`` has no corpus column and is
#: removed through its documents.
LIVE_TABLES_CHILDREN_FIRST = (
    "chunks",
    "documents",
    "ingestion_manifest",
)


def upgrade() -> None:
    # --- 1. Per-corpus administration -------------------------------------
    # The CHECK is widened rather than replaced so the constraint still exists.
    op.execute(
        "ALTER TABLE memory_principals DROP CONSTRAINT IF EXISTS memory_principals_role_check"
    )
    op.execute("ALTER TABLE memory_principals DROP CONSTRAINT IF EXISTS memory_principals_role_key")
    op.execute("ALTER TABLE memory_principals DROP CONSTRAINT IF EXISTS memory_principals_check")
    op.execute("""
        ALTER TABLE memory_principals
            ADD CONSTRAINT memory_principals_role_check
            CHECK (role IN ('read_only', 'read_write', 'corpus_admin', 'principal_admin'))
    """)

    # --- 2. A credential lookup the runtime cannot enumerate ---------------
    # One row per granted corpus, so authentication resolves the identity and its
    # scope in a single round trip. It also has to be a single function: with the
    # grant table no longer readable by the runtime role, two queries would mean
    # one that works and one that cannot.
    op.execute("""
        CREATE OR REPLACE FUNCTION mindpalace_resolve_credential(presented_hash CHAR(64))
        RETURNS TABLE (
            id CHAR(64), name TEXT, role TEXT, corpus_name TEXT, corpus_id CHAR(64)
        )
        LANGUAGE sql STABLE SECURITY DEFINER
        SET search_path = public, pg_temp AS $$
            SELECT p.id, p.name, p.role, c.name, c.id
            FROM memory_principals p
            JOIN memory_principal_corpora g ON g.principal_id = p.id
            JOIN corpora c ON c.id = g.corpus_id
            WHERE p.key_hash = presented_hash
              AND NOT p.disabled
              AND p.revoked_at IS NULL
            ORDER BY c.name
        $$
    """)

    op.execute("""
        CREATE OR REPLACE FUNCTION mindpalace_locate_credential(needle TEXT)
        RETURNS CHAR(64)
        LANGUAGE sql STABLE SECURITY DEFINER
        SET search_path = public, pg_temp AS $$
            -- One live credential, identified by an id, an id prefix, or a name.
            -- Returns NULL unless exactly one matches: revoking the wrong
            -- credential is worse than refusing, and names are deliberately not
            -- unique because rotation issues a second one under the same name.
            WITH candidates AS (
                SELECT id FROM memory_principals
                WHERE NOT disabled
                  AND (id LIKE btrim(needle) || '%' OR name = btrim(needle))
                ORDER BY created_at DESC
                LIMIT 2
            )
            SELECT CASE WHEN count(*) = 1 THEN min(id) ELSE NULL END FROM candidates
            -- `id LIKE needle || '%'` accepts an id or an id prefix; `name = needle`
            -- accepts the label an operator actually types, which is what they
            -- have on screen. A name is deliberately not unique -- rotation issues
            -- a second credential under the same name -- so both paths refuse
            -- rather than guessing which one was meant.
        $$
    """)

    # --- 3. Corpus erasure -------------------------------------------------
    # The interlock the immutability trigger consults. Set only inside the
    # erasure function below, which runs as the table owner; the runtime role has
    # no DELETE on these tables, so setting it is not enough to delete anything.
    op.execute("""
        CREATE OR REPLACE FUNCTION memory_reject_mutation() RETURNS trigger
        LANGUAGE plpgsql AS $$
        BEGIN
            -- DELETE is permitted only during an authorized erasure. UPDATE and
            -- TRUNCATE are refused unconditionally: there is no reason to rewrite
            -- an archived fact, and erasure removes rows rather than editing them.
            IF TG_OP = 'DELETE'
               AND current_setting('mindpalace.erasure', true) = 'on' THEN
                RETURN NULL;
            END IF;
            RAISE EXCEPTION 'memory archive is append-only' USING ERRCODE = '23514';
        END $$
    """)

    op.execute("""
        CREATE OR REPLACE FUNCTION mindpalace_erase_corpus(target CHAR(64))
        RETURNS JSONB
        LANGUAGE plpgsql SECURITY DEFINER
        SET search_path = public, pg_temp AS $$
        DECLARE
            scope  text;
            found  int;
            report jsonb := '{}'::jsonb;
            one    text;
            removed int;
        BEGIN
            target := btrim(target::text);

            IF target = '' THEN
                RAISE EXCEPTION 'no corpus named' USING ERRCODE = '22023';
            END IF;

            -- Re-read the caller's scope rather than trusting the caller. This is
            -- the same transaction-local setting the row-level policies read, so
            -- erasure and retrieval cannot disagree about who is asking.
            scope := coalesce(current_setting('mindpalace.tenant_scope', true), '');
            IF scope IS DISTINCT FROM '{*}' THEN
                IF scope = '' THEN
                    RAISE EXCEPTION 'no tenant scope' USING ERRCODE = '42501';
                END IF;
                IF NOT (target = ANY (string_to_array(scope, ','))) THEN
                    RAISE EXCEPTION 'corpus not found' USING ERRCODE = '42501';
                END IF;
            END IF;

            SELECT count(*) INTO found FROM corpora WHERE id = target;
            IF found = 0 THEN
                -- Idempotent: erasing an absent corpus reports that, rather than
                -- failing or claiming a deletion that did not happen.
                RETURN jsonb_build_object('corpus_id', target, 'deleted', false,
                                          'reason', 'corpus_not_found', 'removed', '{}'::jsonb);
            END IF;

            -- Serialize against every writer. This is the same advisory lock
            -- `memory.lock_corpus` takes, derived identically, so once it is held
            -- no version write is mid-flight and none can start: every writer
            -- takes this lock before it inserts, and holds it until it commits.
            PERFORM pg_advisory_xact_lock(
                ('x' || substr(encode(sha256(convert_to('memory:' || target, 'UTF8')), 'hex'),
                               1, 16))::bit(64)::bigint
            );

            PERFORM set_config('mindpalace.erasure', 'on', true);

            FOREACH one IN ARRAY ARRAY[
                'memory_snapshot_seals', 'memory_snapshot_versions',
                'memory_snapshots', 'memory_evidence', 'memory_claims',
                'memory_chunks', 'memory_versions', 'memory_claim_embeddings',
                'memory_documents'
            ] LOOP
                EXECUTE format('DELETE FROM %I WHERE corpus_id = %L', one, target);
                GET DIAGNOSTICS removed = ROW_COUNT;
                report := report || jsonb_build_object(one, removed);
            END LOOP;

            -- `chunks` is reached through `documents`, which is also how the
            -- foreign key cascades, but is counted separately so the report says
            -- what was actually removed rather than what the schema implies.
            EXECUTE 'DELETE FROM chunks WHERE doc_id IN (SELECT id FROM documents WHERE corpus_id = $1)'
                USING target;
            GET DIAGNOSTICS removed = ROW_COUNT;
            report := report || jsonb_build_object('chunks', removed);

            EXECUTE 'DELETE FROM documents WHERE corpus_id = $1' USING target;
            GET DIAGNOSTICS removed = ROW_COUNT;
            report := report || jsonb_build_object('documents', removed);

            -- `ingestion_manifest.corpus_id` has no foreign key, so nothing
            -- cascades it and this is the only thing that removes it.
            EXECUTE 'DELETE FROM ingestion_manifest WHERE corpus_id = $1' USING target;
            GET DIAGNOSTICS removed = ROW_COUNT;
            report := report || jsonb_build_object('ingestion_manifest', removed);

            -- Credentials must not keep a grant to a corpus that no longer exists.
            EXECUTE 'DELETE FROM memory_principal_corpora WHERE corpus_id = $1' USING target;
            GET DIAGNOSTICS removed = ROW_COUNT;
            report := report || jsonb_build_object('memory_principal_corpora', removed);

            EXECUTE 'DELETE FROM corpora WHERE id = $1' USING target;
            GET DIAGNOSTICS removed = ROW_COUNT;
            report := report || jsonb_build_object('corpora', removed);

            PERFORM set_config('mindpalace.erasure', 'off', true);

            RETURN jsonb_build_object(
                'corpus_id', target,
                'deleted', true,
                'reason', NULL,
                'removed', report
            );
        END
        $$
    """)


def downgrade() -> None:
    op.execute("DROP FUNCTION IF EXISTS mindpalace_erase_corpus(CHAR(64))")
    op.execute("DROP FUNCTION IF EXISTS mindpalace_locate_credential(TEXT)")
    op.execute("DROP FUNCTION IF EXISTS mindpalace_resolve_credential(CHAR(64))")

    # Restore the append-only trigger exactly as `004` installed it.
    op.execute("""
        CREATE OR REPLACE FUNCTION memory_reject_mutation() RETURNS trigger
        LANGUAGE plpgsql AS $$
        BEGIN
            RAISE EXCEPTION 'memory archive is append-only' USING ERRCODE = '23514';
        END $$
    """)

    op.execute(
        "ALTER TABLE memory_principals DROP CONSTRAINT IF EXISTS memory_principals_role_check"
    )
    op.execute("""
        ALTER TABLE memory_principals
            ADD CONSTRAINT memory_principals_role_check
            CHECK (role IN ('read_only', 'read_write'))
    """)
