"""Resource-level authorization inside a corpus.

Corpus-level isolation is necessary and it is not sufficient. Two people sharing a
workspace currently share every fact in it, permanently, with no way to keep
something private. This migration adds the missing boundary without changing what
any existing row means.

**The resource is a document, and the archive is a tree.** `memory_document_id`
is `sha256(corpus_id, path)`, so a document's identity is already `(corpus, path)`
-- the unit a user ingests. Every archive row reaches a root:

    memory_documents                       <- gains ownership
      └── memory_versions                  (memory_document_id)
            ├── memory_snapshot_versions   (version_id)
            │     └── memory_snapshot_seals (snapshot_id)
            ├── memory_chunks              (version_id)
            ├── memory_evidence            (version_id)
            └── memory_claims              (version_id, memory_document_id)
                  └── memory_claim_embeddings (claim_id)

Nothing in the archive is an orphan, so one column at the root is enough and
inheritance does the rest. The ingestion projection gets the same treatment:
`documents` gains ownership, and `chunks` already inherits through it.

**`NULL` owner means shared within the corpus, which is exactly the previous
behaviour.** That is what makes this additive: every existing row is shared the
moment the column appears, no backfill runs, and nothing that passes today
changes. Every row in `documents` is `NULL` after this migration too.

Three things this deliberately does not do:

* **It does not make records private by default.** A shared corpus is the
  documented shape, and flipping the default would silently change what every
  existing deployment stores. Privacy is opt-in, per document.
* **It does not contain a compromised application process.** `owner_principal_id`
  is compared against `mindpalace.acting_principal`, which is a session setting
  the application writes -- the same property, and the same limit, as
  `mindpalace.tenant_scope`. See `docs/security.md` and the design note.
* **It does not make a claim its own resource.** Per-document is the granularity
  the archive's own identity function uses; per-claim would need ownership
  threaded through supersession and conflict closure, which changes authoritative
  memory semantics and is a separate decision.
"""

import sqlalchemy as sa
from alembic import op

revision = "011_resource_level_authorization"
down_revision = "010_bind_unrestricted_scope"
branch_labels = None
depends_on = None


#: The archive root -- the resource -- and the table holding explicit shares.
#: Named here rather than inlined so the upgrade and the downgrade cannot disagree
#: about which tables were protected.
ARCHIVE_ROOT = "memory_documents"
GRANTS = "memory_resource_grants"


def _search_path() -> str:
    """The schema being migrated, plus `pg_temp`, for the new functions.

    Not a literal `public`. PostgreSQL validates a `LANGUAGE sql` body at
    `CREATE FUNCTION` time, resolving names using the function's *own*
    `search_path` -- so a function pinned to `public` while the migration is
    replaying into another schema validates its body against the wrong tables and
    fails on a column that was, moments earlier, added correctly.

    That is not hypothetical: `tests/test_memory_ingestion.py` replays the whole
    chain into a throwaway schema, and it failed here exactly that way.
    Interpolating the name keeps the pinning -- which is the security property
    worth having -- without pinning it to the wrong schema.
    """
    schema = op.get_bind().execute(sa.text("SELECT current_schema()")).scalar()
    return f"{schema}, pg_temp"


def upgrade() -> None:
    _path = _search_path()

    # 1. Ownership on both roots.
    #
    #    `ON DELETE RESTRICT`, and that is forced by the archive rather than
    #    chosen for convenience. `SET NULL` would issue an `UPDATE` on every
    #    document the deleted principal owns -- and `memory_reject_mutation`
    #    refuses `UPDATE` on these tables, so deleting a principal that owns
    #    anything fails with "memory archive is append-only", which is a
    #    bewildering message for what is actually an ownership conflict.
    #    `CASCADE` would be worse: it deletes archive rows, which is precisely
    #    what that trigger exists to prevent.
    #
    #    So the rule is: a principal that owns a document cannot be deleted until
    #    that document is erased or shared. An operator gets a real refusal naming
    #    the reason, which is the only honest option for an append-only table whose
    #    ownership is immutable.
    op.execute(f"""
        ALTER TABLE {ARCHIVE_ROOT}
            ADD COLUMN owner_principal_id CHAR(64)
            REFERENCES memory_principals(id) ON DELETE RESTRICT
    """)

    # 2. Explicit shares. This is the "shared" half of the private/shared model:
    #    a document with an owner can still be granted to named principals.
    op.execute(f"""
        CREATE TABLE {GRANTS} (
            principal_id       CHAR(64) NOT NULL
                REFERENCES memory_principals(id) ON DELETE CASCADE,
            corpus_id          CHAR(64) NOT NULL,
            memory_document_id CHAR(64) NOT NULL,
            granted_at         TIMESTAMPTZ NOT NULL DEFAULT now(),
            PRIMARY KEY (principal_id, corpus_id, memory_document_id)
        )
    """)
    op.execute(f"CREATE INDEX ON {GRANTS} (corpus_id, memory_document_id)")
    op.execute(f"CREATE INDEX ON {GRANTS} (memory_document_id)")

    # Row-level security has to be *enabled* on a table for a policy on it to do
    # anything. Creating the policy alone leaves it inert, and the schema-derived
    # check in `test_deployment_profiles_and_lifecycle.py` caught exactly that.
    op.execute(f"ALTER TABLE {GRANTS} ENABLE ROW LEVEL SECURITY")
    op.execute(f"ALTER TABLE {GRANTS} FORCE ROW LEVEL SECURITY")

    # 3. The acting principal, as a session setting like the tenant scope.
    op.execute("""
        CREATE OR REPLACE FUNCTION mindpalace_acting_principal() RETURNS CHAR(64)
        LANGUAGE sql STABLE AS $$
            SELECT nullif(
                btrim(coalesce(current_setting('mindpalace.acting_principal', true), '')),
                ''
            )::char(64)
        $$
    """)

    # 4. The ownership predicate.
    #
    #    SECURITY DEFINER, and that is load-bearing rather than incidental. A
    #    plain function would re-enter `memory_documents`' own policy, which calls
    #    this function, and PostgreSQL raises "infinite recursion detected in
    #    policy" instead of answering. Running as the owner, which holds
    #    BYPASSRLS, breaks the cycle.
    #
    #    It is not an authorization bypass. `mindpalace_allowed_corpus` runs first
    #    and is unchanged: a caller that does not hold the corpus never reaches the
    #    ownership lookup, so this function only ever answers "within a corpus you
    #    already hold, which documents are yours?" -- a question the corpus policy
    #    cannot answer on its own.
    #
    #    A missing document row is FALSE, not TRUE. If the row is absent, hidden,
    #    or malformed, nothing is authorized.
    op.execute(f"""
        CREATE OR REPLACE FUNCTION mindpalace_allowed_document(
            target_corpus CHAR(64),
            target_document CHAR(64)
        ) RETURNS BOOLEAN
        LANGUAGE sql STABLE
        SECURITY DEFINER
        SET search_path = {_path} AS $$
            SELECT CASE
                WHEN NOT mindpalace_allowed_corpus(target_corpus) THEN FALSE
                WHEN target_document IS NULL THEN FALSE
                ELSE COALESCE((
                    SELECT d.owner_principal_id IS NULL
                        OR d.owner_principal_id = mindpalace_acting_principal()
                        OR EXISTS (
                            SELECT 1 FROM memory_resource_grants g
                            WHERE g.principal_id = mindpalace_acting_principal()
                              AND g.corpus_id = target_corpus
                              AND g.memory_document_id = target_document
                        )
                    FROM memory_documents d
                    WHERE d.corpus_id = target_corpus
                      AND d.id = target_document
                ), FALSE)
            END
        $$
    """)

    # 5. Policies.
    #
    #    The corpus-only policies from `008` are dropped first, and that is not
    #    housekeeping -- it is the whole difference between this migration working
    #    and doing nothing. PostgreSQL ORs *permissive* policies together, so
    #    leaving `mindpalace_tenant` in place alongside the new one means a caller
    #    passes whenever *either* is satisfied. The first run of these tests failed
    #    exactly that way: the new policy was installed, correct, and inert.
    #
    #    A second layer would be `ALTER POLICY ... AS RESTRICTIVE`, but that would
    #    mean rewriting migration 008's policies rather than superseding them, and
    #    008 is the shipped, byte-stable definition of the corpus boundary. Replacing
    #    the policy outright is the same guarantee with one fewer concept.
    for table in (
        ARCHIVE_ROOT,
        "memory_versions",
        "memory_claims",
        "memory_chunks",
        "memory_evidence",
        "memory_claim_embeddings",
        "memory_snapshot_versions",
        "memory_snapshot_seals",
    ):
        op.execute(f"DROP POLICY IF EXISTS mindpalace_tenant ON {table}")

    # Each inherits through its parent, and the parent's own policy has already
    # enforced the whole chain -- the same trick `chunks` has used against
    # `documents` since migration 008.
    #
    # Both roots also get a WITH CHECK, so a caller cannot *write* a document into
    # a corpus and claim it as someone else's private resource. `WITH CHECK` is
    # corpus-scoped rather than owner-scoped on purpose: ownership is set on
    # insert from the authenticated principal, never from the request body, and
    # the archive is append-only, so there is no later write to constrain.
    op.execute(f"""
        CREATE POLICY memory_documents_isolation ON {ARCHIVE_ROOT}
            USING (mindpalace_allowed_corpus(corpus_id)
                   AND mindpalace_allowed_document(corpus_id, id))
            WITH CHECK (mindpalace_allowed_corpus(corpus_id))
    """)
    # Direct parent column: the cheapest form, and the clearest to read.
    for table in ("memory_versions", "memory_claims"):
        op.execute(f"""
            CREATE POLICY {table}_resource_isolation ON {table}
                USING (mindpalace_allowed_document(corpus_id, memory_document_id))
        """)

    # Reached only through a version. The subquery is itself filtered by
    # `memory_versions`' policy, so one hop covers the whole chain to the root.
    for table, column in (
        ("memory_chunks", "version_id"),
        ("memory_evidence", "version_id"),
        ("memory_snapshot_versions", "version_id"),
    ):
        op.execute(f"""
            CREATE POLICY {table}_resource_isolation ON {table}
                USING (EXISTS (
                    SELECT 1 FROM memory_versions v WHERE v.id = {table}.{column}
                ))
        """)

    op.execute("""
        CREATE POLICY memory_claim_embeddings_resource_isolation
            ON memory_claim_embeddings
            USING (EXISTS (
                SELECT 1 FROM memory_claims c WHERE c.id = memory_claim_embeddings.claim_id
            ))
    """)

    # A seal names a snapshot, not a version, so it inherits through the
    # snapshot's own membership rows.
    op.execute("""
        CREATE POLICY memory_snapshot_seals_resource_isolation
            ON memory_snapshot_seals
            USING (EXISTS (
                SELECT 1 FROM memory_snapshot_versions sv
                WHERE sv.snapshot_id = memory_snapshot_seals.snapshot_id
            ))
    """)

    # The grants table: a principal reads its own shares; nothing else is readable
    # through ordinary queries. Granting is done by the administrative path, which
    # holds the maintenance role.
    op.execute(f"""
        CREATE POLICY {GRANTS}_own ON {GRANTS}
            USING (principal_id = mindpalace_acting_principal())
    """)

    op.execute("GRANT SELECT ON memory_resource_grants TO mindpalace_runtime")
    op.execute(
        "GRANT EXECUTE ON FUNCTION mindpalace_allowed_document(CHAR(64), CHAR(64)) "
        "TO mindpalace_runtime"
    )
    op.execute("GRANT EXECUTE ON FUNCTION mindpalace_acting_principal() TO mindpalace_runtime")
    # The predicate functions read ownership and grants directly, so the runtime
    # role needs SELECT on the two owner columns specifically. Column-level grants
    # rather than table-level, so this does not reopen credential enumeration.
    op.execute(f"GRANT SELECT (owner_principal_id) ON {ARCHIVE_ROOT} TO mindpalace_runtime")


def downgrade() -> None:
    for table, column in (
        ("memory_snapshot_seals", "snapshot_id"),
        ("memory_claim_embeddings", "claim_id"),
        ("memory_chunks", "version_id"),
        ("memory_evidence", "version_id"),
        ("memory_snapshot_versions", "version_id"),
    ):
        op.execute(f"DROP POLICY IF EXISTS {table}_resource_isolation ON {table}")
    for table in ("memory_versions", "memory_claims"):
        op.execute(f"DROP POLICY IF EXISTS {table}_resource_isolation ON {table}")
    op.execute(f"DROP POLICY IF EXISTS {GRANTS}_own ON {GRANTS}")
    op.execute(f"DROP POLICY IF EXISTS memory_documents_isolation ON {ARCHIVE_ROOT}")

    # Restore the corpus-only policies, or a downgraded database would have row
    # security *enabled* and no policy at all -- which denies everything, including
    # the owner reading their own document. A downgrade that breaks the local
    # single-user workflow is a downgrade nobody will run twice.
    #
    # Exactly the tables whose `mindpalace_tenant` the upgrade dropped, and no
    # others. `chunks` and `ingestion_manifest` keep the policies they have had
    # since `008` -- `chunks` inherits through `documents`, which is already
    # resource-scoped above -- and recreating a policy that was never dropped is a
    # `DuplicateObject` that aborts the whole downgrade.
    for table in (
        ARCHIVE_ROOT,
        "memory_versions",
        "memory_claims",
        "memory_chunks",
        "memory_evidence",
        "memory_claim_embeddings",
        "memory_snapshot_versions",
        "memory_snapshot_seals",
    ):
        op.execute(f"""
            CREATE POLICY mindpalace_tenant ON {table}
                USING (mindpalace_allowed_corpus(corpus_id))
        """)

    op.execute("DROP FUNCTION IF EXISTS mindpalace_allowed_document(CHAR(64), CHAR(64))")
    op.execute("DROP FUNCTION IF EXISTS mindpalace_acting_principal()")
    op.execute(f"DROP TABLE IF EXISTS {GRANTS}")
    op.execute(f"ALTER TABLE {ARCHIVE_ROOT} DROP COLUMN IF EXISTS owner_principal_id")
