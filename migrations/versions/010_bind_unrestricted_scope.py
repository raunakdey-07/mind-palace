"""Bind the unrestricted tenant scope to a role that already has every row.

An audit of the unreleased tenant work found that `{*}` -- the sentinel meaning
"this principal may reach every corpus" -- was an ordinary session setting. Any
role that could open a connection could therefore write:

    SET mindpalace.tenant_scope = '{*}';
    SELECT mindpalace_erase_corpus(<any corpus id>);

and erase a corpus it held no grant on, because the function is `SECURITY
DEFINER` and trusts the scope it is given. Demonstrated against the real
`mindpalace_runtime` role on a corpus with no grant: the corpus, its document,
claim, evidence row and version were all removed.

That is not a theoretical escalation. It is the difference between "a compromised
application can reach its own tenants" -- which is the documented limit of this
design -- and "a compromised application can delete everyone's". Erasure is the
destructive half of it; the read half is the same forgeable scope.

Two changes, both narrow:

1. **`{*}` is honoured only for a role that already has every row anyway** -- a
   superuser, or a role that may use the owner of `corpora`. Those roles are
   outside the tenant boundary by definition, so binding the sentinel to them
   removes no capability they had. The local single-user workflow runs on exactly
   such a role and is unaffected; the least-privileged runtime role is not one, so
   it can no longer manufacture the sentinel.

2. **`PUBLIC` loses `EXECUTE` on the three `SECURITY DEFINER` functions.**
   PostgreSQL grants `EXECUTE` to `PUBLIC` on every new function. The earlier
   provisioning script only revoked from `mindpalace_runtime`, which removed
   nothing: any *other* role that could connect could still call them. They are
   granted to the runtime role by name, which is who actually needs them.

What this does **not** fix, and cannot without moving authorization into the
database: a compromised application process can still forge a *scoped* value --
`mindpalace.tenant_scope = '<some other corpus id>'` -- because a role may set
its own session settings. Row-level security is therefore defence against a
forgotten predicate, not against a malicious scope, and `docs/security.md` says
so in exactly those words.

That residual forge widens the blast radius of an already-compromised process
rather than creating a new class of access: the runtime role already holds
`SELECT` on the tenant tables, and a corpus id is `sha256("corpus:" + name)`, so
a name is enough to compute one. It is now stated rather than assumed away.
"""

from alembic import op

revision = "010_bind_unrestricted_scope"
down_revision = "009_permission_and_erasure"
branch_labels = None
depends_on = None


#: The three functions that run with more authority than their caller. The
#: predicate function is deliberately absent: a row-level policy is evaluated as
#: the querying user, so that one has to stay executable by whoever queries.
SECURITY_DEFINER_FUNCTIONS = (
    ("mindpalace_erase_corpus", "CHAR(64)"),
    ("mindpalace_locate_credential", "TEXT"),
    ("mindpalace_resolve_credential", "CHAR(64)"),
)

#: Whether the connecting role may use the `{*}` sentinel.
#:
#: True for a superuser, and for a role that may use the owner of `corpora` --
#: which is the maintenance role that runs migrations and already reads every
#: row with `BYPASSRLS`. Anything else, including `mindpalace_runtime`, cannot
#: manufacture the sentinel even though it can set the session variable.
#:
#: `session_user`, not `current_user`, and that distinction is the whole thing.
#: Inside a `SECURITY DEFINER` function `current_user` is the *function owner* --
#: verified against this database, where it reported `mpadmin` for a call made by
#: `mindpalace_runtime` -- so checking it would have asked "is the definer trusted"
#: and answered yes for every caller. `session_user` is the role that opened the
#: connection, which is the one being constrained.
_RESTRICTED_SCOPE_CHECK = """
                    current_setting('is_superuser', true) = 'on'
                    OR pg_has_role(session_user,
                                   pg_get_userbyid(
                                       (SELECT relowner FROM pg_class
                                        WHERE oid = 'corpora'::regclass)
                                   ),
                                   'USAGE')
"""


def _predicate(*, trusted_scope: bool) -> str:
    """The row-level predicate, in one form or the other.

    `trusted_scope=False` is the whole point of this migration and it is why the
    two variants share one source rather than being edited apart: the sentinel
    check and the erasure check have to agree, and the only way to guarantee that
    is to keep them in one place.
    """
    sentinel = (
        "current_setting('mindpalace.tenant_scope', true) = '{*}'"
        if trusted_scope
        else f"""(
                (current_setting('mindpalace.tenant_scope', true) = '{{*}}'
                 AND ({_RESTRICTED_SCOPE_CHECK}))
            )"""
    )
    return f"""
        CREATE OR REPLACE FUNCTION mindpalace_allowed_corpus(candidate CHAR(64))
        RETURNS BOOLEAN
        LANGUAGE sql STABLE AS $$
            SELECT CASE
                WHEN {sentinel} THEN TRUE
                WHEN coalesce(
                         current_setting('mindpalace.tenant_scope', true), ''
                     ) = '' THEN FALSE
                ELSE btrim(candidate::text) = ANY (string_to_array(
                    current_setting('mindpalace.tenant_scope', true), ','
                ))
            END
        $$
    """


def _erasure(*, trusted_scope: bool) -> str:
    """The erasure function, with the same rule applied to its own check.

    Duplicated rather than factored, because the deletion order has to stay
    byte-identical to `009` and a shared helper would make a downgrade rewrite
    body it is supposed to leave alone.
    """
    guard = (
        "            -- `009` behaviour: the sentinel is taken at face value.\n"
        "            unrestricted := TRUE;"
        if trusted_scope
        else f"""            -- The sentinel is honoured only for a role that already reads
            -- every row. A session setting is not authority, and this function
            -- runs as the table owner, so a forgeable value would let any role
            -- that can connect erase any corpus.
            unrestricted := ({_RESTRICTED_SCOPE_CHECK});"""
    )
    return f"""
        CREATE OR REPLACE FUNCTION mindpalace_erase_corpus(target CHAR(64))
        RETURNS JSONB
        LANGUAGE plpgsql SECURITY DEFINER
        SET search_path = public, pg_temp AS $$
        DECLARE
            scope  text;
            found  int;
            report jsonb := '{{}}'::jsonb;
            one    text;
            removed int;
            unrestricted boolean;
        BEGIN
            target := btrim(target::text);

            IF target = '' THEN
                RAISE EXCEPTION 'no corpus named' USING ERRCODE = '22023';
            END IF;

{guard}

            scope := coalesce(current_setting('mindpalace.tenant_scope', true), '');
            IF scope <> '{{*}}' OR NOT unrestricted THEN
                IF scope = '' THEN
                    RAISE EXCEPTION 'no tenant scope' USING ERRCODE = '42501';
                END IF;
                IF NOT (target = ANY (string_to_array(scope, ','))) THEN
                    RAISE EXCEPTION 'corpus not found' USING ERRCODE = '42501';
                END IF;
            END IF;

            SELECT count(*) INTO found FROM corpora WHERE id = target;
            IF found = 0 THEN
                RETURN jsonb_build_object('corpus_id', target, 'deleted', false,
                                          'reason', 'corpus_not_found', 'removed', '{{}}'::jsonb);
            END IF;

            PERFORM pg_advisory_xact_lock(
                ('x' || substr(encode(sha256(convert_to('memory:' || target, 'UTF8')), 'hex'),
                               1, 16))::bit(64)::bigint
            );

            PERFORM set_config('mindpalace.erasure', 'on', true);

            FOREACH one IN ARRAY ARRAY[
                'memory_snapshot_seals', 'memory_snapshot_versions',
                'memory_snapshots', 'memory_evidence', 'memory_claims',
                'memory_chunks', 'memory_versions', 'memory_claim_embeddings',
                'memory_documents'
            ] LOOP
                EXECUTE format('DELETE FROM %I WHERE corpus_id = %L', one, target);
                GET DIAGNOSTICS removed = ROW_COUNT;
                report := report || jsonb_build_object(one, removed);
            END LOOP;

            EXECUTE 'DELETE FROM chunks WHERE doc_id IN '
                '(SELECT id FROM documents WHERE corpus_id = $1)'
                USING target;
            GET DIAGNOSTICS removed = ROW_COUNT;
            report := report || jsonb_build_object('chunks', removed);

            EXECUTE 'DELETE FROM documents WHERE corpus_id = $1' USING target;
            GET DIAGNOSTICS removed = ROW_COUNT;
            report := report || jsonb_build_object('documents', removed);

            EXECUTE 'DELETE FROM ingestion_manifest WHERE corpus_id = $1' USING target;
            GET DIAGNOSTICS removed = ROW_COUNT;
            report := report || jsonb_build_object('ingestion_manifest', removed);

            EXECUTE 'DELETE FROM memory_principal_corpora WHERE corpus_id = $1' USING target;
            GET DIAGNOSTICS removed = ROW_COUNT;
            report := report || jsonb_build_object('memory_principal_corpora', removed);

            EXECUTE 'DELETE FROM corpora WHERE id = $1' USING target;
            GET DIAGNOSTICS removed = ROW_COUNT;
            report := report || jsonb_build_object('corpora', removed);

            PERFORM set_config('mindpalace.erasure', 'off', true);

            RETURN jsonb_build_object(
                'corpus_id', target,
                'deleted', true,
                'reason', NULL,
                'removed', report
            );
        END
        $$
    """


def upgrade() -> None:
    op.execute(_predicate(trusted_scope=False))
    op.execute(_erasure(trusted_scope=False))
    for name, signature in SECURITY_DEFINER_FUNCTIONS:
        op.execute(f"REVOKE EXECUTE ON FUNCTION {name}({signature}) FROM PUBLIC")
    # The predicate stays executable by PUBLIC on purpose: a row-level policy is
    # evaluated as the querying user, so revoking it would break isolation for
    # every role that has not been granted it by name.
    op.execute("GRANT EXECUTE ON FUNCTION mindpalace_allowed_corpus(CHAR(64)) TO PUBLIC")


def downgrade() -> None:
    op.execute(_predicate(trusted_scope=True))
    op.execute(_erasure(trusted_scope=True))
    for name, signature in SECURITY_DEFINER_FUNCTIONS:
        op.execute(f"GRANT EXECUTE ON FUNCTION {name}({signature}) TO PUBLIC")
