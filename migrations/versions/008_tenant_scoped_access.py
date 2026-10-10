"""Tenant-scoped access: service credentials and row-level security.

Additive only. Nothing here changes a query, a column or a key that the memory
contract depends on, so a deployment that never adopts a tenant-scoped runtime
role behaves exactly as before.

Two controls are installed, and they are deliberately independent of each other:

1. `memory_principals` / `memory_principal_corpora` hold server-side credentials
   for the HTTP and MCP surfaces. A credential is a secret the server stores only
   as a SHA-256 hash, bound to a read-only or read-write role and to an explicit
   set of corpora. The corpus set is what makes a corpus name an authorization
   decision instead of a routing hint.

2. Row-level security on every tenant-owned table. The archive was already
   `(corpus_id, ...)`-keyed with composite foreign keys, so a row-level policy
   keyed on `corpus_id` needs no schema change at all -- only `ENABLE`, `FORCE`
   and a `USING`/`WITH CHECK` predicate. `chunks` has no corpus column, so its
   policy reaches the corpus through the document it belongs to; that document is
   itself policy-protected, so the row is bounded twice.

Both fail closed. The policy predicate reads one transaction-local setting and
returns FALSE when it is absent, empty, or malformed, so a query that runs
without a tenant scope sees no rows and can write none. A request that authenticates
but resolves no corpus cannot read or write anything.

`{*}` is the single-user escape, and it is set by the application, not by a
caller. It keeps the documented local single-user workflow working while leaving
a shared deployment with no rows at all when the context is missing.

The runtime database role must be neither a superuser nor a `BYPASSRLS` role and
must not own these tables: PostgreSQL exempts all three from row-level security,
so those deployments get no protection from this migration. `FORCE` is set
because it extends the policy to the table owner as well; it is safe here because
every data backfill in this repository ran in an earlier revision, and the
maintenance role is expected to set the scope explicitly.
"""

from alembic import op

revision = "008_tenant_scoped_access"
down_revision = "007_claim_embedding_cache"
branch_labels = None
depends_on = None


#: Every table that holds tenant-owned rows, and how its policy reaches the corpus.
#: ``corpus`` is a direct column; ``document`` reaches it through ``documents``,
#: whose own policy already applies inside the sub-query.
CORPUS_COLUMN_TABLES = (
    "corpora",
    "documents",
    "ingestion_manifest",
    "memory_documents",
    "memory_versions",
    "memory_chunks",
    "memory_claims",
    "memory_evidence",
    "memory_snapshots",
    "memory_snapshot_versions",
    "memory_snapshot_seals",
    "memory_claim_embeddings",
)

DOCUMENT_SCOPED_TABLES = ("chunks",)

POLICY_PREFIX = "mindpalace_tenant"


def upgrade() -> None:
    op.execute("""
        CREATE TABLE memory_principals (
            id CHAR(64) PRIMARY KEY,
            name TEXT NOT NULL,
            key_hash CHAR(64) NOT NULL UNIQUE,
            role TEXT NOT NULL CHECK (role IN ('read_only', 'read_write')),
            disabled BOOLEAN NOT NULL DEFAULT FALSE,
            created_at TIMESTAMPTZ NOT NULL DEFAULT clock_timestamp(),
            revoked_at TIMESTAMPTZ
        )
    """)
    op.execute("CREATE INDEX idx_memory_principals_name ON memory_principals (name, disabled)")
    # A grant names a corpus that already exists. Binding to the corpus row rather
    # than to a free-text name means a credential cannot outlive the namespace it
    # was issued for, and cannot be pointed at one that does not exist yet.
    op.execute("""
        CREATE TABLE memory_principal_corpora (
            principal_id CHAR(64) NOT NULL
                REFERENCES memory_principals(id) ON DELETE CASCADE,
            corpus_id CHAR(64) NOT NULL
                REFERENCES corpora(id) ON DELETE CASCADE,
            PRIMARY KEY (principal_id, corpus_id)
        )
    """)
    op.execute("""
        CREATE INDEX idx_principal_corpora_corpus
            ON memory_principal_corpora (corpus_id)
    """)

    # Credentials are authorization state, not tenant content. They deliberately
    # carry no policy: the database role that can read them is already inside the
    # trust boundary, and hiding them behind row-level security would only make
    # the authentication lookup depend on the tenant context it exists to decide.
    op.execute("""
        CREATE OR REPLACE FUNCTION mindpalace_allowed_corpus(candidate CHAR(64))
        RETURNS BOOLEAN
        LANGUAGE sql STABLE AS $$
            SELECT CASE
                WHEN current_setting('mindpalace.tenant_scope', true) = '{*}' THEN TRUE
                WHEN coalesce(
                         current_setting('mindpalace.tenant_scope', true), ''
                     ) = '' THEN FALSE
                ELSE btrim(candidate::text) = ANY (string_to_array(
                    current_setting('mindpalace.tenant_scope', true), ','
                ))
            END
        $$
    """)

    for table in CORPUS_COLUMN_TABLES:
        column = "id" if table == "corpora" else "corpus_id"
        op.execute(f"ALTER TABLE {table} ENABLE ROW LEVEL SECURITY")
        op.execute(f"ALTER TABLE {table} FORCE ROW LEVEL SECURITY")
        op.execute(f"""
            CREATE POLICY {POLICY_PREFIX} ON {table}
                USING (mindpalace_allowed_corpus({column}))
                WITH CHECK (mindpalace_allowed_corpus({column}))
        """)

    for table in DOCUMENT_SCOPED_TABLES:
        op.execute(f"ALTER TABLE {table} ENABLE ROW LEVEL SECURITY")
        op.execute(f"ALTER TABLE {table} FORCE ROW LEVEL SECURITY")
        # `documents` carries its own policy above, so this sub-query is already
        # bounded by the tenant scope; no second corpus predicate is needed, and
        # adding one would only risk the two policies disagreeing.
        op.execute(f"""
            CREATE POLICY {POLICY_PREFIX} ON {table}
                USING (EXISTS (
                    SELECT 1 FROM documents d
                    WHERE d.id = {table}.doc_id
                ))
                WITH CHECK (EXISTS (
                    SELECT 1 FROM documents d
                    WHERE d.id = {table}.doc_id
                ))
        """)


def downgrade() -> None:
    for table in DOCUMENT_SCOPED_TABLES + CORPUS_COLUMN_TABLES:
        op.execute(f"DROP POLICY IF EXISTS {POLICY_PREFIX} ON {table}")
        op.execute(f"ALTER TABLE {table} NO FORCE ROW LEVEL SECURITY")
        op.execute(f"ALTER TABLE {table} DISABLE ROW LEVEL SECURITY")
    op.execute("DROP FUNCTION mindpalace_allowed_corpus(CHAR(64))")
    op.execute("DROP TABLE memory_principal_corpora")
    op.execute("DROP TABLE memory_principals")
