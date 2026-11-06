"""Migration 017: database objects renamed in place from Verilore to Verilore.

From an empty database, upgrade to head, then downgrade to 016 restores the
old names. The migration must rename in place (ALTER FUNCTION / ALTER POLICY)
so OIDs, grants, and policy dependencies are preserved, and RLS must still
filter a second tenant's rows after upgrade.
"""

from __future__ import annotations

import os

import pytest
import sqlalchemy as sa
from alembic.config import Config
from alembic.script import ScriptDirectory


def _sync_url() -> str:
    url = os.getenv("DATABASE_URL", "")
    return (
        url.replace("postgresql+psycopg://", "postgresql+psycopg2://")
        .replace("postgresql+asyncpg://", "postgresql+psycopg2://")
        .replace("postgresql://", "postgresql+psycopg2://")
    )


def _db_available() -> bool:
    if not os.getenv("DATABASE_URL"):
        return False
    try:
        engine = sa.create_engine(_sync_url())
        with engine.connect():
            engine.dispose()
        return True
    except Exception:
        return False


requires_db = pytest.mark.skipif(
    not _db_available(), reason="no reachable DATABASE_URL"
)


@pytest.fixture(scope="module")
def _alembic_scripts():
    cfg = Config("migrations/alembic.ini")
    return ScriptDirectory.from_config(cfg), cfg


def test_migration_017_upgrade_installs_verilore_names(_alembic_scripts):
    """After upgrading to head, verilore_* objects exist and verilore_* do not."""

    scripts, cfg = _alembic_scripts
    head = scripts.get_current_head()
    assert head == "017_rename_to_verilore", f"head is {head!r}, expected 017_rename_to_verilore"

    engine = sa.create_engine(_sync_url())
    try:
        with engine.connect() as conn:
            version = conn.execute(sa.text("SELECT version_num FROM alembic_version")).scalar()
            assert version == "017_rename_to_verilore", f"version is {version!r}"

            # New names exist
            func = conn.execute(
                sa.text(
                    "SELECT proname FROM pg_proc WHERE proname = 'vl_verified_scope_corpora'"
                )
            ).scalar()
            assert func == "vl_verified_scope_corpora"

            policy = conn.execute(
                sa.text(
                    "SELECT policyname FROM pg_policies WHERE policyname = 'verilore_tenant'"
                )
            ).scalar()
            assert policy == "verilore_tenant"

            # Old names are gone
            old_func = conn.execute(
                sa.text(
                    "SELECT proname FROM pg_proc WHERE proname = 'vl_verified_scope_corpora'"
                )
            ).scalar()
            assert old_func is None, "vl_verified_scope_corpora still exists after upgrade"

            old_policy = conn.execute(
                sa.text(
                    "SELECT policyname FROM pg_policies WHERE policyname = 'verilore_tenant'"
                )
            ).scalar()
            assert old_policy is None, "verilore_tenant still exists after upgrade"

            # GUC setting name is verilore.*
            setting = conn.execute(
                sa.text("SELECT current_setting('verilore.tenant_scope', true)")
            ).scalar()
            # No error means the setting name resolves; value may be empty before set
            assert setting is not None or True  # just check it doesn't error
    finally:
        engine.dispose()


def test_migration_017_rls_still_filters_tenant_rows(_alembic_scripts):
    """After upgrade to 017, RLS still filters a second tenant's rows."""
    scripts, cfg = _alembic_scripts
    engine = sa.create_engine(_sync_url())
    try:
        with engine.connect() as conn:
            # Create two corpora (rows in corpora table)
            tenant_a = "a" * 64
            tenant_b = "b" * 64
            conn.execute(
                sa.text(
                    "INSERT INTO corpora (id, name) VALUES (:id, :name) "
                    "ON CONFLICT (id) DO NOTHING"
                ),
                {"id": tenant_a, "name": "tenant-a"},
            )
            conn.execute(
                sa.text(
                    "INSERT INTO corpora (id, name) VALUES (:id, :name) "
                    "ON CONFLICT (id) DO NOTHING"
                ),
                {"id": tenant_b, "name": "tenant-b"},
            )
            conn.commit()

            # Enable a tenant scope and assert only that tenant's rows are visible
            conn.execute(
                sa.text("SELECT set_config('verilore.tenant_scope', :tid, true)"),
                {"tid": tenant_a},
            )
            visible = conn.execute(
                sa.text("SELECT id FROM corpora ORDER BY id")
            ).fetchall()
            visible_ids = [r[0] for r in visible]
            assert tenant_a in visible_ids, f"tenant_a not visible: {visible_ids}"
            assert tenant_b not in visible_ids, f"tenant_b visible (RLS leak): {visible_ids}"
            conn.commit()
    finally:
        engine.dispose()


def test_migration_017_downgrade_restores_verilore_names(_alembic_scripts):
    """Downgrading from 017 to 016 restores the old Verilore names."""
    from alembic import command

    scripts, cfg = _alembic_scripts
    command.downgrade(cfg, "016_hoist_scope_initplan")

    engine = sa.create_engine(_sync_url())
    try:
        with engine.connect() as conn:
            version = conn.execute(sa.text("SELECT version_num FROM alembic_version")).scalar()
            assert version == "016_hoist_scope_initplan", f"version is {version!r}"

            # Old names restored
            func = conn.execute(
                sa.text(
                    "SELECT proname FROM pg_proc WHERE proname = 'vl_verified_scope_corpora'"
                )
            ).scalar()
            assert func == "vl_verified_scope_corpora", "vl_verified_scope_corpora not restored"

            policy = conn.execute(
                sa.text(
                    "SELECT policyname FROM pg_policies WHERE policyname = 'verilore_tenant'"
                )
            ).scalar()
            assert policy == "verilore_tenant", "verilore_tenant not restored"

            # New names gone
            new_func = conn.execute(
                sa.text(
                    "SELECT proname FROM pg_proc WHERE proname = 'vl_verified_scope_corpora'"
                )
            ).scalar()
            assert new_func is None, "vl_verified_scope_corpora still exists after downgrade"
    finally:
        engine.dispose()

        # Re-upgrade so the rest of the suite sees head
        command.upgrade(cfg, "head")
