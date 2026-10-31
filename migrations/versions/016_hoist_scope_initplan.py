"""Migration 016: hoist per-row scope function out of RLS policies (InitPlan).

Recreates the `mindpalace_tenant` policy on every table in CORPUS_COLUMN_TABLES
with the scope check inlined directly in the USING / WITH CHECK expression,
instead of indirected through `mindpalace_allowed_corpus()`.

PostgreSQL cannot inline the wrapper function, so migration 015's policy caused
`mp_verified_scope_corpora()` to be invoked once per row (~50,000 calls on
50,000 `memory_claims`). Inlining puts `(SELECT mp_verified_scope_corpora())`
directly in the policy, where the planner can evaluate it once as an InitPlan
instead of per row.

Semantics are unchanged: exactly the same rows are visible to the same scopes.
`mindpalace_allowed_corpus` and its grants are left intact (the function remains
for compatibility); only the policy expression changes.

`chunks` is intentionally left untouched: its policy reaches the corpus through
`documents` (whose own policy is already scope-bounded), so hoisting its
sub-query would not add an InitPlan benefit. Revisit with EXPLAIN if that
assumption changes.
"""

from alembic import op

revision = "016_hoist_scope_initplan"
down_revision = "015_rls_scope_enforcement"
branch_labels = None
depends_on = None

#: Tables whose tenant policy is keyed on a corpus column (`id` for `corpora`,
#: `corpus_id` for everything else). Kept local so this migration is
#: self-contained and does not depend on import ordering across alembic
#: versions. Matches 008_tenant_scoped_access.CORPUS_COLUMN_TABLES.
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

POLICY_PREFIX = "mindpalace_tenant"


def _policy_column(table: str) -> str:
    return "id" if table == "corpora" else "corpus_id"


def _scope_expression(column: str) -> str:
    """Inlined scope predicate hoistable to an InitPlan.

    Equivalent to `mindpalace_allowed_corpus(<column>)` under migration 015:
    `btrim(candidate) = ANY ((SELECT mp_verified_scope_corpora()))::text[]`
    OR `'*' = ANY ((SELECT mp_verified_scope_corpora()))::text[]`.
    """
    return (
        f"btrim({column}::text) = ANY (((SELECT mp_verified_scope_corpora()))::text[]) "
        f"OR '*' = ANY (((SELECT mp_verified_scope_corpora()))::text[])"
    )


def upgrade() -> None:
    for table in CORPUS_COLUMN_TABLES:
        column = _policy_column(table)
        expr = _scope_expression(column)
        op.execute(f"ALTER TABLE {table} ENABLE ROW LEVEL SECURITY")
        op.execute(f"ALTER TABLE {table} FORCE ROW LEVEL SECURITY")
        op.execute(f"DROP POLICY IF EXISTS {POLICY_PREFIX} ON {table}")
        op.execute(f"""
            CREATE POLICY {POLICY_PREFIX} ON {table}
                USING ({expr})
                WITH CHECK ({expr})
        """)


def downgrade() -> None:
    """Restore the migration 015 policy form (indirect via
    `mindpalace_allowed_corpus()`), faithfully reversing upgrade()."""
    for table in CORPUS_COLUMN_TABLES:
        column = _policy_column(table)
        op.execute(f"ALTER TABLE {table} ENABLE ROW LEVEL SECURITY")
        op.execute(f"ALTER TABLE {table} FORCE ROW LEVEL SECURITY")
        op.execute(f"DROP POLICY IF EXISTS {POLICY_PREFIX} ON {table}")
        op.execute(f"""
            CREATE POLICY {POLICY_PREFIX} ON {table}
                USING (mindpalace_allowed_corpus({column}))
                WITH CHECK (mindpalace_allowed_corpus({column}))
        """)
