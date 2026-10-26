"""Harden tenant scope with a companion scope token check."""

from alembic import op

revision = "014_scope_token"
down_revision = "013_info_state"


def upgrade() -> None:
    op.execute("""
        CREATE OR REPLACE FUNCTION mindpalace_allowed_corpus(candidate CHAR(64))
        RETURNS BOOLEAN
        LANGUAGE sql STABLE AS $$
            SELECT CASE
                WHEN current_setting('mindpalace.tenant_scope', true) = '{*}' THEN TRUE
                WHEN coalesce(current_setting('mindpalace.tenant_scope', true), '') = '' THEN FALSE
                WHEN coalesce(current_setting('mindpalace.scope_token', true), '') = '' THEN FALSE
                ELSE btrim(candidate::text) = ANY (string_to_array(
                    current_setting('mindpalace.tenant_scope', true), ','
                ))
            END
        $$
    """)


def downgrade() -> None:
    op.execute("""
        CREATE OR REPLACE FUNCTION mindpalace_allowed_corpus(candidate CHAR(64))
        RETURNS BOOLEAN
        LANGUAGE sql STABLE AS $$
            SELECT CASE
                WHEN current_setting('mindpalace.tenant_scope', true) = '{*}' THEN TRUE
                WHEN coalesce(current_setting('mindpalace.tenant_scope', true), '') = '' THEN FALSE
                ELSE btrim(candidate::text) = ANY (string_to_array(
                    current_setting('mindpalace.tenant_scope', true), ','
                ))
            END
        $$
    """)
