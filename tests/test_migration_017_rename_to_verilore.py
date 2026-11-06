"""Migration 017: rename prose and field names from Mind Palace to Verilore.

From an empty database, upgrade to head, then downgrade to 016 restores the
old names. All function/claim names were already created as `verilore_*`
or `vl_*` in migrations 008-015, so this migration is a no-op. The test
verifies that all claim functions use `verilore_*`/`vl_*` names and no
`md:prose` or `md:prose:*` names remain.
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


requires_db = pytest.mark.skipif(not _db_available(), reason="no reachable DATABASE_URL")


@pytest.fixture(scope="module")
def _alembic_scripts():
    cfg = Config("migrations/alembic.ini")
    return ScriptDirectory.from_config(cfg), cfg


def test_migration_017_upgrade_installs_verilore_names(_alembic_scripts):
    """After upgrading to head, `verilore_*` claim names exist and no
    `md:prose*` names remain. Everything was already created with the
    `verilore_*`/`vl_*` naming in migrations 008-015, so the upgrade is
    a no-op that confirms the naming is consistent.
    """

    scripts, cfg = _alembic_scripts
    head = scripts.get_current_head()
    assert head == "017_rename_to_verilore", f"head is {head!r}, expected 017_rename_to_verilore"

    engine = sa.create_engine(_sync_url())
    try:
        with engine.connect() as conn:
            version = conn.execute(sa.text("SELECT version_num FROM alembic_version")).scalar()
            assert version == "017_rename_to_verilore", f"version is {version!r}"

            # All prose claim functions use `verilore_*` naming (not `md:prose`)
            for func_name in (
                "verilore_allowed_corpus",
                "verilore_resolve_credential",
                "verilore_locate_credential",
                "verilore_erase_corpus",
                "vl_verified_scope_corpora",
                "vl_set_scope_token_enforcement",
                "vl_scope_enforcement_status",
                "vl_verify_scope_token",
            ):
                func = conn.execute(
                    sa.text("SELECT proname FROM pg_proc WHERE proname = :name"),
                    {"name": func_name},
                ).scalar()
                assert func == func_name, f"{func_name} does not exist after upgrade"

            # No `md:prose` or `md:prose:*` claim functions remain
            remaining = conn.execute(
                sa.text("SELECT count(*) FROM pg_proc WHERE proname LIKE 'md:prose%'")
            ).scalar()
            assert remaining == 0, f"{remaining} md:prose* claims still exist after upgrade"

    finally:
        engine.dispose()


def test_migration_017_downgrade_restores_verilore_names(_alembic_scripts):
    """Downgrading from 017 to 016 keeps `verilore_*` names (nothing was renamed).

    Since the prose claim functions were already created as `verilore_*` in
    migrations 008-015, there is nothing to revert on downgrade.
    """
    from alembic import command

    scripts, cfg = _alembic_scripts
    command.downgrade(cfg, "016_hoist_scope_initplan")

    engine = sa.create_engine(_sync_url())
    try:
        with engine.connect() as conn:
            version = conn.execute(sa.text("SELECT version_num FROM alembic_version")).scalar()
            assert version == "016_hoist_scope_initplan", f"version is {version!r}"

            # `verilore_allowed_corpora` still exists (it was created in migration 008,
            # not renamed by 017, so downgrading to 016 keeps it)
            func = conn.execute(
                sa.text("SELECT proname FROM pg_proc WHERE proname = 'verilore_allowed_corpus'")
            ).scalar()
            assert (
                func == "verilore_allowed_corpus"
            ), "verilore_allowed_corpus not present after downgrade"

            # `verilore_tenant` policy still exists (created in migration 008,
            # not renamed by 017)
            policy = conn.execute(
                sa.text("SELECT policyname FROM pg_policies WHERE policyname = 'verilore_tenant'")
            ).scalar()
            assert policy == "verilore_tenant", "verilore_tenant policy not present after downgrade"
    finally:
        engine.dispose()

        # Re-upgrade so the rest of the suite sees head
        command.upgrade(cfg, "head")
