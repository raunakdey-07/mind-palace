"""Migration 017: rename prose and field names from Mind Palace to Verilore.

All object renames use ALTER FUNCTION / ALTER POLICY ... RENAME TO so OIDs,
grants, and policy dependencies are preserved (RLS never has a gap). CREATE
OR REPLACE FUNCTION only for functions whose bodies name a renamed function,
a renamed setting (mindpalace.* -> verilore.*), or a role.

Downgrade reverses identically.

Roles (mindpalace_runtime/mindpalace_owner) are cluster-wide and are NOT
renamed here -- they are handled by scripts/db/rename_roles_v0100.sql, run by
an operator before `alembic upgrade head`.

NOTE: All functions were created as ``verilore_*`` or ``vl_*`` in migrations
008-015, and all policies were created as ``verilore_tenant`` in migration
008. There are no ``mindpalace_*`` or ``mp_*`` functions to rename, so this
migration is a no-op. It exists to document the naming convention that was
already applied in earlier migrations.
"""

from alembic import op

revision = "017_rename_to_verilore"
down_revision = "016_hoist_scope_initplan"
branch_labels = None
depends_on = None


# ---------------------------------------------------------------------------
# SQL functions (mp_* -> vl_*, mindpalace_* -> verilore_*)
#
# All functions were already created as ``verilore_*`` or ``vl_*`` in
# migrations 008-015, so there are no ``mindpalace_*`` or ``mp_*`` functions
# to rename. This list is kept empty for documentation purposes.
# ---------------------------------------------------------------------------

SQL_FUNCTIONS: list[tuple[str, str]] = []


# New name pattern for each function: mp_ -> vl_, mindpalace_ -> verilore_
def _new_func_name(old: str) -> str:
    if old.startswith("mp_"):
        return "vl_" + old[3:]
    if old.startswith("mindpalace_"):
        return "verilore_" + old[11:]
    return old


# ---------------------------------------------------------------------------
# RLS policies (verilore_tenant -> verilore_tenant)
#
# All policies were created as ``verilore_tenant`` in migration 008, not as
# ``mindpalace_tenant``, so there is nothing to rename. This list is kept
# empty for documentation purposes.
# ---------------------------------------------------------------------------


def upgrade() -> None:
    # 1. Rename SQL functions in place (preserves OIDs and grants).
    for old, _sig in SQL_FUNCTIONS:
        new = _new_func_name(old)
        op.execute(f"ALTER FUNCTION {old} RENAME TO {new}")

    # 2. Rename RLS policies in place (preserves dependencies).
    # No policies to rename -- all were created as ``verilore_tenant`` in
    # migration 008, not as ``mindpalace_tenant``.
    pass


def downgrade() -> None:
    # Reverse the renames identically.
    for old, _sig in SQL_FUNCTIONS:
        new = _new_func_name(old)
        op.execute(f"ALTER FUNCTION {new} RENAME TO {old}")
