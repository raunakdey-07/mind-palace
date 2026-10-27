"""Harden tenant scope with HMAC-verified scope token.

Migration 010 bound the ``{*`` sentinel to roles that already read every row,
but a scoped session value naming another corpus was deliberately left forgeable
-- a role may set its own session settings, and PostgreSQL offers no way to
stop it. That residual forge is documented, not hidden.

This migration closes the *token* half: `mindpalace.scope_token` is no longer a
mere presence check. Any non-empty token is validated as an HMAC-SHA-256 over
the tenant scope, computed with a server secret held in `memory_server_secrets`
-- a table the runtime role cannot read. The validation runs inside
`mp_verify_scope_token`, a `SECURITY DEFINER` function owned by the maintenance
role, so the runtime role cannot recompute or bypass it.

A forged non-empty token (`scope_token = 'forged-...'`) therefore fails closed:
every row-level predicate returns FALSE and the query sees zero rows. An absent
or empty token falls back to the plain scope membership check, which keeps
callers that never set a token (raw SQL, older clients) on the membership
behaviour migration 008/010 already defined -- that residual scope forge is the
documented limit of this design and is measured by
`tests/test_scope_forgery.py`.
"""

from alembic import op

revision = "014_scope_token"
down_revision = "013_info_state"

SERVER_SECRET_NAME = "scope_hmac_secret"
DEFAULT_SERVER_SECRET = "dev-scope-secret"

#: Whether the connecting role may use the ``{*}`` sentinel. Mirror of migration
#: 010's check: true for a superuser or a role that may use the owner of
#: `corpora`. `session_user` is the connecting role, not the function owner.
_RESTRICTED_SCOPE_CHECK = """
                    current_setting('is_superuser', true) = 'on'
                    OR pg_has_role(session_user,
                                   pg_get_userbyid(
                                       (SELECT relowner FROM pg_class
                                        WHERE oid = 'corpora'::regclass)
                                   ),
                                   'USAGE')
"""


def _predicate() -> str:
    return f"""
        CREATE OR REPLACE FUNCTION mindpalace_allowed_corpus(candidate CHAR(64))
        RETURNS BOOLEAN
        LANGUAGE sql STABLE AS $$
            SELECT CASE
                WHEN (
                        (current_setting('mindpalace.tenant_scope', true) = '{{*}}'
                         AND ({_RESTRICTED_SCOPE_CHECK}))
                    ) THEN TRUE
                WHEN coalesce(current_setting('mindpalace.tenant_scope', true), '') = '' THEN FALSE
                WHEN coalesce(current_setting('mindpalace.scope_token', true), '') = '' THEN
                    -- No token presented: fall back to plain scope membership.
                    -- This keeps callers that never set a token on the
                    -- migration 008/010 behaviour; the residual scope forge
                    -- there is documented in docs/security.md and measured by
                    -- tests/test_scope_forgery.py.
                    btrim(candidate::text) = ANY (string_to_array(
                        current_setting('mindpalace.tenant_scope', true), ','
                    ))
                ELSE mp_verify_scope_token(
                    candidate, current_setting('mindpalace.scope_token', true)
                )
            END
        $$
    """


def upgrade() -> None:
    # Restricted server-secret table. Owned by the maintenance role; the runtime
    # role holds no privileges on it, so it cannot read or forge the secret.
    op.execute("""
        CREATE TABLE memory_server_secrets (
            name TEXT PRIMARY KEY,
            value TEXT NOT NULL
        )
    """)
    op.execute(
        "INSERT INTO memory_server_secrets (name, value) VALUES "
        f"('{SERVER_SECRET_NAME}', '{DEFAULT_SERVER_SECRET}')"
    )
    # `mindpalace_owner` and `mindpalace_runtime` are provisioned by
    # scripts/db/runtime_roles.sql, which runs after migrations in CI, so
    # guard both on existence here.
    op.execute(
        "DO $$\n"
        "BEGIN\n"
        "    IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'mindpalace_owner') THEN\n"
        "        EXECUTE 'ALTER TABLE memory_server_secrets OWNER TO mindpalace_owner';\n"
        "    END IF;\n"
        "    IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'mindpalace_runtime') THEN\n"
        "        REVOKE ALL ON memory_server_secrets FROM mindpalace_runtime;\n"
        "        GRANT EXECUTE ON FUNCTION mp_verify_scope_token(CHAR(64), TEXT)\n"
        "            TO mindpalace_runtime;\n"
        "    END IF;\n"
        "END $$;\n"
    )
    op.execute("REVOKE ALL ON memory_server_secrets FROM PUBLIC")
    op.execute("REVOKE ALL ON memory_server_secrets FROM PUBLIC")

    op.execute("""
        CREATE OR REPLACE FUNCTION mp_verify_scope_token(corpus_id CHAR(64), scope_token TEXT)
        RETURNS BOOLEAN
        LANGUAGE plpgsql SECURITY DEFINER
        SET search_path = public, pg_temp AS $$
        DECLARE
            scope text := coalesce(current_setting('mindpalace.tenant_scope', true), '');
            secret text;
            expected text;
        BEGIN
            SELECT value INTO secret FROM memory_server_secrets WHERE name = 'scope_hmac_secret';
            IF secret IS NULL THEN
                RETURN FALSE;
            END IF;
            -- A principal may only verify tokens for corpora in its own scope.
            IF scope = '{*}' THEN
                expected := encode(
                    hmac(convert_to(scope, 'UTF8'), convert_to(secret, 'UTF8'), 'sha256'), 'hex'
                );
                RETURN scope_token IS NOT NULL AND scope_token = expected;
            END IF;
            IF NOT (corpus_id = ANY(string_to_array(scope, ','))) THEN
                RETURN FALSE;
            END IF;
            expected := encode(
                hmac(convert_to(scope, 'UTF8'), convert_to(secret, 'UTF8'), 'sha256'), 'hex'
            );
            RETURN scope_token IS NOT NULL AND scope_token = expected;
        END
        $$
    """)

    # `mindpalace_owner` and `mindpalace_runtime` are provisioned by
    # scripts/db/runtime_roles.sql, which runs after migrations in CI, so
    # guard both on existence here.
    op.execute(
        "DO $$\n"
        "BEGIN\n"
        "    IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'mindpalace_owner') THEN\n"
        "        EXECUTE 'ALTER TABLE memory_server_secrets OWNER TO mindpalace_owner';\n"
        "    END IF;\n"
        "    IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'mindpalace_runtime') THEN\n"
        "        REVOKE ALL ON memory_server_secrets FROM mindpalace_runtime;\n"
        "        GRANT EXECUTE ON FUNCTION mp_verify_scope_token(CHAR(64), TEXT)\n"
        "            TO mindpalace_runtime;\n"
        "    END IF;\n"
        "END $$;\n"
    )
    op.execute("REVOKE ALL ON memory_server_secrets FROM PUBLIC")

    op.execute(_predicate())


def downgrade() -> None:
    predicate = """
        CREATE OR REPLACE FUNCTION mindpalace_allowed_corpus(candidate CHAR(64))
        RETURNS BOOLEAN
        LANGUAGE sql STABLE AS $$
            SELECT CASE
                WHEN (
                        (current_setting('mindpalace.tenant_scope', true) = '{*}'
                         AND ({RESTRICTED_SCOPE_CHECK}))
                    ) THEN TRUE
                WHEN coalesce(current_setting('mindpalace.tenant_scope', true), '') = '' THEN FALSE
                ELSE btrim(candidate::text) = ANY (string_to_array(
                    current_setting('mindpalace.tenant_scope', true), ','
                ))
            END
        $$
    """.replace("{RESTRICTED_SCOPE_CHECK}", _RESTRICTED_SCOPE_CHECK)
    op.execute(predicate)
    op.execute("DROP FUNCTION IF EXISTS mp_verify_scope_token(CHAR(64), TEXT)")
    op.execute("DROP TABLE IF EXISTS memory_server_secrets")
