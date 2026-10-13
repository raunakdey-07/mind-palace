-- Least-privilege roles for a shared, tenant-scoped deployment.
--
-- Mind Palace keeps two database identities, and the separation is the point:
--
--   mindpalace_owner    owns the schema. Runs migrations. Has BYPASSRLS, because
--                       a migration legitimately has to read and write across
--                       every corpus -- that is what backfilling a new table
--                       means. It is a maintenance credential: anyone holding it
--                       can read every tenant's rows and is outside the tenant
--                       boundary entirely.
--
--   mindpalace_runtime  what the application connects as. NOT a superuser, NOT
--                       BYPASSRLS, and owns nothing, so the row-level policies
--                       in migration 008 apply to it. This is the role the
--                       isolation tests run as, because a policy proved against
--                       a superuser proves nothing.
--
-- Run this once, as a superuser, before starting a tenant-scoped deployment:
--
--     psql "$ADMIN_DATABASE_URL" -v ON_ERROR_STOP=1 -f scripts/db/runtime_roles.sql
--
-- The password is set here from a psql variable, so this file contains no
-- credential of its own:
--
--     psql "$ADMIN_DATABASE_URL" -v ON_ERROR_STOP=1 \
--         -v runtime_password='<the password>' \
--         -f scripts/db/runtime_roles.sql
--
-- Rotation is running that command again with the new value. Existing
-- connections keep working until they are closed, so drain the application pool
-- before relying on the new password.

DO $$
BEGIN
    IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'mindpalace_owner') THEN
        -- NOLOGIN: the owner is a role privileges are attached to, not an
        -- account anyone signs in as.
        CREATE ROLE mindpalace_owner NOLOGIN BYPASSRLS;
    END IF;
    IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'mindpalace_runtime') THEN
        CREATE ROLE mindpalace_runtime LOGIN NOSUPERUSER NOBYPASSRLS
            NOCREATEDB NOCREATEROLE NOINHERIT;
    END IF;
END
$$;

-- Set the password from the psql variable. `:'runtime_password'` is psql's
-- quoting for a literal, so a password containing a quote or a semicolon cannot
-- break out of the statement -- an unquoted one would.
--
-- Hand the password to plpgsql through a session setting rather than by
-- substituting it into SQL text. `format('%L')` then quotes it as a literal, so
-- a password containing a quote, a semicolon or a `--` cannot end the statement
-- early, and an empty value can be rejected before it clears the password.
--
-- `runtime_password_skip` provisions the role without a password, for a
-- deployment that sets it through a secret manager afterwards.
-- psql leaves `:'name'` as literal syntax when the variable was never supplied,
-- which is a syntax error rather than a diagnosis. Give both variables a
-- default first so an absent one arrives as an empty string the check can name.
\if :{?runtime_password}
\else
    \set runtime_password ''
\endif
\if :{?runtime_password_skip}
\else
    \set runtime_password_skip 'off'
\endif

-- The password has to reach the DO block below. This used to be a bare
-- `SELECT set_config(...)`, which printed the value: `set_config` returns what
-- it was given and psql prints every result set. On a CI runner whose log is
-- archived and readable by anyone with repo access, that put the runtime role's
-- password in the job log on every run.
--
-- `\g /dev/null` executes the statement and throws the result set away. The
-- `:'runtime_password'` interpolation stays outside any dollar-quoted body
-- because psql does not expand variables inside one -- an earlier attempt to
-- wrap this in a DO block failed with a syntax error at the colon.
--
-- A plain SQL driver has no `\g`, so `tests/tenant_harness.load_provision_sql`
-- strips it for the same reason it strips `\if` and `\set`. Dropping it is also
-- harmless there: a driver was never going to print a result set.
SELECT set_config('mindpalace.runtime_password', :'runtime_password', false) \g /dev/null
SELECT set_config('mindpalace.skip_password', :'runtime_password_skip', false) \g /dev/null

DO $$
DECLARE
    supplied text := current_setting('mindpalace.runtime_password', true);
    skipped   boolean := coalesce(
        current_setting('mindpalace.skip_password', true), 'off') = 'on';
BEGIN
    IF skipped THEN
        RETURN;
    END IF;
    IF supplied IS NULL OR supplied = '' THEN
        RAISE EXCEPTION
            'runtime_password is missing or empty. Re-run with '
            '-v runtime_password=<password>, or -v runtime_password_skip=on to '
            'provision the role and set its password through a secret manager.';
    END IF;
    EXECUTE format('ALTER ROLE mindpalace_runtime PASSWORD %L', supplied);
END
$$;

-- Confirm the role can actually authenticate, rather than trusting that the
-- statement above did what it looked like it did.
DO $$
BEGIN
    IF NOT EXISTS (SELECT 1 FROM pg_shadow WHERE usename = 'mindpalace_runtime') THEN
        RAISE EXCEPTION
            'mindpalace_runtime has no password and cannot log in. Re-run with '
            '-v runtime_password=<password>.';
    END IF;
END
$$;

-- Ownership moves to the maintenance role, so `FORCE ROW LEVEL SECURITY`
-- reaches it too and a migration that forgets to set a tenant scope fails
-- closed rather than silently succeeding.
DO $$
DECLARE
    target text;
BEGIN
    FOREACH target IN ARRAY ARRAY[
        'corpora', 'documents', 'chunks', 'ingestion_manifest',
        'memory_documents', 'memory_versions', 'memory_chunks', 'memory_claims',
        'memory_evidence', 'memory_snapshots', 'memory_snapshot_versions',
        'memory_snapshot_seals', 'memory_claim_embeddings'
    ] LOOP
        EXECUTE format('ALTER TABLE %I OWNER TO mindpalace_owner', target);
    END LOOP;
    -- Authorization state, not tenant content. Readable by both roles so
    -- authentication can run before any tenant scope exists.
    EXECUTE 'ALTER TABLE memory_principals OWNER TO mindpalace_owner';
    EXECUTE 'ALTER TABLE memory_principal_corpora OWNER TO mindpalace_owner';
END
$$;

GRANT USAGE ON SCHEMA public TO mindpalace_runtime;

-- DML only. No CREATE, no DROP, no TRUNCATE, no REFERENCES, no TRIGGER, and no
-- ownership: the runtime role cannot change the policies that constrain it, so a
-- compromised application process cannot widen its own access, and it cannot
-- empty a table without touching a row.
GRANT SELECT, INSERT, UPDATE, DELETE ON ALL TABLES IN SCHEMA public
    TO mindpalace_runtime;
GRANT USAGE, SELECT ON ALL SEQUENCES IN SCHEMA public TO mindpalace_runtime;

-- Functions are granted by name, never by `ALL`. `GRANT EXECUTE ON ALL
-- FUNCTIONS` would hand the runtime role every SECURITY DEFINER function in the
-- schema, which is precisely the set that runs with more authority than the
-- caller.
--
-- `FROM PUBLIC` matters as much as `FROM mindpalace_runtime`. PostgreSQL grants
-- EXECUTE to PUBLIC on every new function, so revoking only from this role
-- removed nothing: any *other* role that could connect could still call them,
-- and did -- including `mindpalace_erase_corpus`. Migration 010 revokes from
-- PUBLIC too; this keeps a role provisioned before that migration in the same
-- shape as one provisioned after it.
REVOKE ALL ON ALL FUNCTIONS IN SCHEMA public FROM mindpalace_runtime;
REVOKE EXECUTE ON FUNCTION mindpalace_erase_corpus(CHAR(64)) FROM PUBLIC;
REVOKE EXECUTE ON FUNCTION mindpalace_locate_credential(TEXT) FROM PUBLIC;
REVOKE EXECUTE ON FUNCTION mindpalace_resolve_credential(CHAR(64)) FROM PUBLIC;

GRANT EXECUTE ON FUNCTION mindpalace_allowed_corpus(CHAR(64)) TO mindpalace_runtime;
GRANT EXECUTE ON FUNCTION mindpalace_resolve_credential(CHAR(64)) TO mindpalace_runtime;
GRANT EXECUTE ON FUNCTION mindpalace_locate_credential(TEXT) TO mindpalace_runtime;
-- Erasure runs as the table owner and re-checks the *connecting* role's own
-- tenant scope, so the runtime role is allowed to ask for it without being able
-- to bypass it.
GRANT EXECUTE ON FUNCTION mindpalace_erase_corpus(CHAR(64)) TO mindpalace_runtime;

-- Credential state is not readable with ordinary queries. The application needs
-- exactly one thing from it -- given a presented hash, the identity it belongs
-- to -- and that is `mindpalace_resolve_credential`, granted above. Without this
-- revocation a single query returns every tenant's credential hash, role and
-- corpus grant, which is not something a memory service has any reason to be
-- able to do and which the runtime role does not need.
REVOKE SELECT, INSERT, UPDATE, DELETE ON memory_principals FROM mindpalace_runtime;
REVOKE SELECT, INSERT, UPDATE, DELETE ON memory_principal_corpora FROM mindpalace_runtime;

-- No DELETE on the archive. Erasure goes through `mindpalace_erase_corpus`,
-- which is SECURITY DEFINER, re-checks the caller's tenant scope, and is the
-- only thing the append-only triggers permit to delete. This is what makes the
-- archive append-only against the *application role* rather than only against
-- ordinary statements.
REVOKE DELETE ON memory_documents, memory_versions, memory_chunks, memory_claims,
    memory_evidence, memory_snapshots, memory_snapshot_versions,
    memory_snapshot_seals, memory_claim_embeddings FROM mindpalace_runtime;

-- Default privileges, so a table added by a later migration is reachable by the
-- runtime role without an extra manual step.
--
-- Note what is deliberately *not* here: `ALTER DEFAULT PRIVILEGES` cannot name an
-- individual table, so the exclusions above have to be restated by hand if a new
-- archive or credential table is ever added. `mindpalace posture` checks the
-- result rather than trusting this file to stay in step -- see
-- `api/services/posture.py`, which verifies that the role can neither read the
-- credential table nor delete from any archive table.
ALTER DEFAULT PRIVILEGES FOR ROLE mindpalace_owner IN SCHEMA public
    GRANT SELECT, INSERT, UPDATE, DELETE ON TABLES TO mindpalace_runtime;
ALTER DEFAULT PRIVILEGES FOR ROLE mindpalace_owner IN SCHEMA public
    GRANT USAGE, SELECT ON SEQUENCES TO mindpalace_runtime;