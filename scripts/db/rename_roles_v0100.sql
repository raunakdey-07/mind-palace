-- Rename database roles from Verilore to Verilore.
--
-- Run this once, as a superuser, before `alembic upgrade head` when the
-- database already has the verilore_* roles:
--
--     psql "$ADMIN_DATABASE_URL" -v ON_ERROR_STOP=1 -f scripts/db/rename_roles_v0100.sql
--
-- Each rename is guarded by IF EXISTS, so re-running is safe.
-- Grants follow the role (ALTER ROLE preserves them).
--
-- IMPORTANT: PostgreSQL clears an MD5 password when a role is renamed (SCRAM
-- is unaffected). After renaming, re-run scripts/db/runtime_roles.sql with
-- the runtime password so verilore_runtime has a usable password.
--
-- The two roles are cluster-wide (not per-database), so this script must be
-- run against any database in the cluster.

DO $$
BEGIN
    IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'verilore_owner') AND
       NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'verilore_owner') THEN
        ALTER ROLE verilore_owner RENAME TO verilore_owner;
    END IF;

    IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'verilore_runtime') AND
       NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'verilore_runtime') THEN
        ALTER ROLE verilore_runtime RENAME TO verilore_runtime;
    END IF;
END
$$;

-- If the rename cleared an MD5 password, print a reminder so the operator
-- does not forget to re-run runtime_roles.sql to reset it.
SELECT 'If either role had an MD5 password, re-run runtime_roles.sql to reset it.' AS note;
