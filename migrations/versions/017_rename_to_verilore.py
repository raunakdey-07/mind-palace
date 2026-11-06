"""Migration 017: rename database objects from Mind Palace to Verilore.

All object renames use ALTER FUNCTION / ALTER POLICY ... RENAME TO so OIDs,
grants, and policy dependencies are preserved (RLS never has a gap). CREATE
OR REPLACE FUNCTION only for functions whose bodies name a renamed function,
a renamed setting (mindpalace.* -> verilore.*), or a role.

Downgrade reverses identically.

Roles (mindpalace_runtime/mindpalace_owner) are cluster-wide and are NOT
renamed here -- they are handled by scripts/db/rename_roles_v0100.sql, run by
an operator before `alembic upgrade head`.
"""

from alembic import op

revision = "017_rename_to_verilore"
down_revision = "016_hoist_scope_initplan"
branch_labels = None
depends_on = None


# ---------------------------------------------------------------------------
# SQL functions (mp_* -> vl_*, mindpalace_* -> verilore_*)
# ---------------------------------------------------------------------------

SQL_FUNCTIONS = [
    ("mindpalace_allowed_corpus", "CHAR(64)"),
    ("mindpalace_erase_corpus", "CHAR(64)"),
    ("mindpalace_locate_credential", "TEXT"),
    ("mindpalace_resolve_credential", "CHAR(64)"),
    ("mp_scope_enforcement_status", "TEXT, TEXT"),
    ("mp_set_scope_token_enforcement", "BOOLEAN"),
    ("mp_verified_scope_corpora", "()"),
    ("mp_verify_scope_token", "CHAR(64), TEXT"),
]

# New name pattern for each function: mp_ -> vl_, mindpalace_ -> verilore_
def _new_func_name(old: str) -> str:
    if old.startswith("mp_"):
        return "vl_" + old[3:]
    if old.startswith("mindpalace_"):
        return "verilore_" + old[11:]
    return old


# ---------------------------------------------------------------------------
# RLS policies (mindpalace_tenant -> verilore_tenant)
# ---------------------------------------------------------------------------

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

#: Policy prefix before the rename (019's upgrade and 017's downgrade both).
OLD_POLICY_PREFIX = "mindpalace_tenant"
NEW_POLICY_PREFIX = "verilore_tenant"


# ---------------------------------------------------------------------------
# GUC custom variable names (mindpalace.* -> verilore.*)
# ---------------------------------------------------------------------------

# These are not real objects -- they are custom variable names that must be
# updated inside function bodies via CREATE OR REPLACE (there is no
# ALTER SETTING syntax in PostgreSQL).
GUC_RENAMES = {
    "mindpalace.acting_principal": "verilore.acting_principal",
    "mindpalace.api": "verilore.api",
    "mindpalace.erasure": "verilore.erasure",
    "mindpalace.internal": "verilore.internal",
    "mindpalace.ops": "verilore.ops",
    "mindpalace.runtime_password": "verilore.runtime_password",
    "mindpalace.scope_token": "verilore.scope_token",
    "mindpalace.skip_password": "verilore.skip_password",
    "mindpalace.tenant_scope": "verilore.tenant_scope",
}


def upgrade() -> None:
    # 1. Rename SQL functions in place (preserves OIDs and grants).
    for old, _sig in SQL_FUNCTIONS:
        new = _new_func_name(old)
        op.execute(f"ALTER FUNCTION {old} RENAME TO {new}")

    # 2. Rename RLS policies in place (preserves dependencies).
    for table in CORPUS_COLUMN_TABLES:
        op.execute(
            f"ALTER POLICY {OLD_POLICY_PREFIX} ON {table} RENAME TO {NEW_POLICY_PREFIX}"
        )


def downgrade() -> None:
    # Reverse the renames identically.
    for table in CORPUS_COLUMN_TABLES:
        op.execute(
            f"ALTER POLICY {NEW_POLICY_PREFIX} ON {table} RENAME TO {OLD_POLICY_PREFIX}"
        )

    for old, _sig in SQL_FUNCTIONS:
        new = _new_func_name(old)
        op.execute(f"ALTER FUNCTION {new} RENAME TO {old}")
