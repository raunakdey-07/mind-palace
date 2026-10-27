"""Migration 015: fix RLS per-row trap and scope enforcement."""

from alembic import op

revision = "015_rls_scope_enforcement"
down_revision = "014_scope_token"

ENFORCEMENT_FLAG_NAME = "enforce_scope_token"


def upgrade() -> None:
    op.execute(
        "INSERT INTO memory_server_secrets (name, value) VALUES "
        f"('{ENFORCEMENT_FLAG_NAME}', 'off') "
        "ON CONFLICT (name) DO NOTHING"
    )

    op.execute("""
        CREATE OR REPLACE FUNCTION mp_verified_scope_corpora()
        RETURNS text[]
        LANGUAGE plpgsql STABLE SECURITY DEFINER
        SET search_path = public, pg_temp AS $fn$
        DECLARE
            scope text := coalesce(current_setting('mindpalace.tenant_scope', true), '');
            token text := coalesce(current_setting('mindpalace.scope_token', true), '');
            enforce text;
            secret text;
            expected text;
            restricted boolean;
            parts text[];
        BEGIN
            SELECT value INTO enforce FROM memory_server_secrets
                WHERE name = 'enforce_scope_token';
            IF enforce IS NULL THEN
                enforce := 'off';
            END IF;
            SELECT value INTO secret FROM memory_server_secrets
                WHERE name = 'scope_hmac_secret';
            IF secret IS NULL THEN
                secret := '';
            END IF;

            restricted := (
                current_setting('is_superuser', true) = 'on'
                OR pg_has_role(session_user,
                               pg_get_userbyid(
                                   (SELECT relowner FROM pg_class
                                    WHERE oid = 'corpora'::regclass)
                               ),
                               'USAGE')
            );

            IF scope = '' THEN
                RETURN ARRAY[]::text[];
            END IF;

            IF scope = '{*}' THEN
                IF restricted THEN
                    RETURN ARRAY['*']::text[];
                END IF;
                RETURN ARRAY[]::text[];
            END IF;

            parts := string_to_array(scope, ',');
            SELECT coalesce(array_agg(btrim(x)), ARRAY[]::text[])
                INTO parts
                FROM unnest(parts) AS x;

            IF token <> '' THEN
                expected := encode(
                    hmac(convert_to(scope, 'UTF8'), convert_to(secret, 'UTF8'), 'sha256'),
                    'hex'
                );
                IF token IS DISTINCT FROM expected THEN
                    RETURN ARRAY[]::text[];
                END IF;
                RETURN parts;
            END IF;

            IF enforce = 'on' THEN
                RETURN ARRAY[]::text[];
            END IF;
            RETURN parts;
        END
        $fn$
    """)

    op.execute("""
        CREATE OR REPLACE FUNCTION mindpalace_allowed_corpus(candidate CHAR(64))
        RETURNS BOOLEAN
        LANGUAGE sql STABLE AS $fn$
            SELECT btrim(candidate) = ANY (((SELECT mp_verified_scope_corpora()))::text[])
                OR '*' = ANY (((SELECT mp_verified_scope_corpora()))::text[])
        $fn$
    """)

    op.execute("""
        CREATE OR REPLACE FUNCTION mp_set_scope_token_enforcement(enabled BOOLEAN)
        RETURNS VOID
        LANGUAGE plpgsql SECURITY DEFINER
        SET search_path = public, pg_temp AS $fn$
        BEGIN
            INSERT INTO memory_server_secrets (name, value)
                VALUES ('enforce_scope_token',
                        CASE WHEN enabled THEN 'on' ELSE 'off' END)
            ON CONFLICT (name) DO UPDATE SET value = EXCLUDED.value;
        END
        $fn$
    """)

    op.execute("""
        CREATE OR REPLACE FUNCTION mp_scope_enforcement_status(
            probe_token TEXT, probe_scope TEXT
        )
        RETURNS TABLE(enforced BOOLEAN, using_default_secret BOOLEAN, secret_matches BOOLEAN)
        LANGUAGE plpgsql STABLE SECURITY DEFINER
        SET search_path = public, pg_temp AS $fn$
        DECLARE
            secret text;
            enforce text;
            expected text;
        BEGIN
            SELECT value INTO secret FROM memory_server_secrets
                WHERE name = 'scope_hmac_secret';
            SELECT value INTO enforce FROM memory_server_secrets
                WHERE name = 'enforce_scope_token';
            enforced := (coalesce(enforce, 'off') = 'on');
            using_default_secret := (coalesce(secret, '') = 'dev-scope-secret');
            expected := encode(
                hmac(convert_to(probe_scope, 'UTF8'), convert_to(coalesce(secret, ''), 'UTF8'), 'sha256'),
                'hex'
            );
            secret_matches := (expected = probe_token);
            RETURN NEXT;
        END
        $fn$
    """)

    op.execute("""
        DO $$
        BEGIN
            IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'mindpalace_owner') THEN
                EXECUTE 'ALTER TABLE memory_server_secrets OWNER TO mindpalace_owner';
                EXECUTE 'ALTER FUNCTION mp_set_scope_token_enforcement(BOOLEAN) OWNER TO mindpalace_owner';
            END IF;
            IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'mindpalace_runtime') THEN
                EXECUTE 'REVOKE ALL ON memory_server_secrets FROM mindpalace_runtime';
                EXECUTE 'GRANT EXECUTE ON FUNCTION mp_verified_scope_corpora() TO mindpalace_runtime';
                EXECUTE 'GRANT EXECUTE ON FUNCTION mindpalace_allowed_corpus(CHAR(64)) TO mindpalace_runtime';
                EXECUTE 'GRANT EXECUTE ON FUNCTION mp_scope_enforcement_status(TEXT, TEXT) TO mindpalace_runtime';
            END IF;
        END $$;
    """)
    op.execute("REVOKE ALL ON FUNCTION mp_set_scope_token_enforcement(BOOLEAN) FROM PUBLIC")
    op.execute("REVOKE ALL ON memory_server_secrets FROM PUBLIC")
    op.execute("GRANT EXECUTE ON FUNCTION mp_verified_scope_corpora() TO PUBLIC")
    op.execute("GRANT EXECUTE ON FUNCTION mindpalace_allowed_corpus(CHAR(64)) TO PUBLIC")
    op.execute("""
        DO $$
        BEGIN
            IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'mindpalace_owner') THEN
                EXECUTE 'GRANT EXECUTE ON FUNCTION mp_set_scope_token_enforcement(BOOLEAN) TO mindpalace_owner';
            END IF;
        END $$;
    """)


def downgrade() -> None:
    op.execute("DROP FUNCTION IF EXISTS mp_scope_enforcement_status(TEXT, TEXT)")
    op.execute("DROP FUNCTION IF EXISTS mp_set_scope_token_enforcement(BOOLEAN)")
    op.execute("DROP FUNCTION IF EXISTS mp_verified_scope_corpora()")
    op.execute("""
        CREATE OR REPLACE FUNCTION mindpalace_allowed_corpus(candidate CHAR(64))
        RETURNS BOOLEAN
        LANGUAGE sql STABLE AS $fn$
            SELECT CASE
                WHEN (
                        (current_setting('mindpalace.tenant_scope', true) = '{*}'
                         AND (
                    current_setting('is_superuser', true) = 'on'
                    OR pg_has_role(session_user,
                                   pg_get_userbyid(
                                       (SELECT relowner FROM pg_class
                                        WHERE oid = 'corpora'::regclass)
                                   ),
                                   'USAGE')
                         ))
                    ) THEN TRUE
                WHEN coalesce(current_setting('mindpalace.tenant_scope', true), '') = '' THEN FALSE
                WHEN coalesce(current_setting('mindpalace.scope_token', true), '') = '' THEN
                    btrim(candidate::text) = ANY (string_to_array(
                        current_setting('mindpalace.tenant_scope', true), ','
                    ))
                ELSE mp_verify_scope_token(
                    candidate, current_setting('mindpalace.scope_token', true)
                )
            END
        $fn$
    """)
    op.execute("DELETE FROM memory_server_secrets WHERE name = 'enforce_scope_token'")
