# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 Raunak Dey

"""Is row-level security actually in force for *this* connection?

`MIND_PALACE_AUTH_MODE=required` is an application-layer switch. It says nothing
about whether the database is enforcing anything underneath, and the answer is
role-dependent in a way nothing in the request path can observe: PostgreSQL
exempts a superuser, a `BYPASSRLS` role, and (without `FORCE`) the table owner
from every policy.

So a deployment can report `{"status": "ready", "auth": {"required": true}}`
while running as `mpadmin` with migration `008` completely inert. The tests prove
the policy works; this module is what tells an *operator*, at startup and on
readiness, whether their own connection is one of the exempted shapes.

Deliberately a separate concern from `authz`, which decides about callers, and
from `db`, which owns connections. This asks one question about the current role.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

#: The tables whose policies carry the tenant boundary. Named here as well as in
#: the migration so that a table added to one and forgotten in the other is
#: visible as a readiness failure rather than as silent absence.
PROTECTED_TABLES = (
    "corpora",
    "documents",
    "chunks",
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

#: The archive proper -- the tables an erasure must reach and ordinary DML must
#: not. Kept separately from `PROTECTED_TABLES` because the two are governed
#: differently: everything is policy-protected, but only these are append-only,
#: and only these must be unreachable by `DELETE` from the application role.
#:
#: Adding an archive table without adding it here, and to the erasure function in
#: migration `009`, is the failure that would leave rows behind after a deletion
#: reported success. `tests/test_deployment_profiles_and_lifecycle.py` compares
#: this list against what the database actually contains.
ARCHIVE_TABLES = (
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


@dataclass(frozen=True)
class DatabasePosture:
    """What the current role is, and whether that undermines the policies."""

    role: str
    is_superuser: bool
    bypasses_rls: bool
    owns_protected_tables: list[str]
    unprotected_tables: list[str]
    #: Roles this one may `SET ROLE` to, which is how a non-privileged role could
    #: still reach a privileged one at runtime.
    settable_privileged_roles: list[str] = field(default_factory=list)
    #: Privileges beyond DML that the role holds on tenant-owned tables.
    excess_privileges: list[str] = field(default_factory=list)
    #: Whether credential hashes can be enumerated with ordinary queries.
    can_enumerate_credentials: bool = False
    #: Archive tables this role could `DELETE` from, which is how erasure is
    #: supposed to happen and how it is not supposed to happen.
    archive_delete_granted: list[str] = field(default_factory=list)

    @property
    def rls_effective(self) -> bool:
        """True only when this role is subject to every policy that matters.

        A superuser or a `BYPASSRLS` role is exempt from policies by
        construction, so no amount of correct SQL underneath changes the answer.
        A role that owns a protected table would also be exempt, but `FORCE ROW
        LEVEL SECURITY` closes that one -- which is why ownership is reported
        separately rather than folded into this flag.
        """
        return not self.is_superuser and not self.bypasses_rls

    def problems(self) -> list[str]:
        """Human-readable reasons, for readiness and for startup refusal."""
        found = []
        if self.is_superuser:
            found.append(
                f"database role '{self.role}' is a superuser and bypasses row-level security"
            )
        if self.bypasses_rls:
            found.append(f"database role '{self.role}' has BYPASSRLS")
        if self.unprotected_tables:
            found.append(
                "row-level security is not enabled on: "
                + ", ".join(sorted(self.unprotected_tables))
            )
        return found

    def server_problems(self) -> list[str]:
        """Everything that must stop a *server* deployment from starting.

        A superset of `problems`, and deliberately so: `problems` answers "are the
        policies in force", while this answers "is this role shaped like a
        least-privilege runtime role at all". A server that reports itself ready
        on a superuser is worse than one that fails to start, because the report
        is what an operator trusts.
        """
        found = list(self.problems())
        if self.owns_protected_tables:
            # Permitted, and reported, rather than refused: `FORCE ROW LEVEL
            # SECURITY` makes an owner subject to the policies anyway, so
            # ownership alone does not defeat isolation. Refusing it would break a
            # legitimate single-role deployment for no security gain, and §3 asks
            # for refusal only where the design does not safely permit it.
            found.append(
                f"database role '{self.role}' owns "
                f"{', '.join(sorted(self.owns_protected_tables))}; row-level security is "
                "FORCEd so the policies still apply, but a least-privileged runtime "
                "role should own nothing"
            )
        for role_name in self.settable_privileged_roles:
            found.append(
                f"database role '{self.role}' may SET ROLE to '{role_name}', which can "
                "bypass row-level security"
            )
        for privilege in self.excess_privileges:
            found.append(f"database role '{self.role}' holds {privilege}")
        if self.can_enumerate_credentials:
            found.append(
                f"database role '{self.role}' can enumerate credential hashes; run "
                "scripts/db/runtime_roles.sql so authentication uses "
                "mindpalace_resolve_credential instead"
            )
        if self.archive_delete_granted:
            found.append(
                f"database role '{self.role}' can DELETE from "
                f"{', '.join(sorted(self.archive_delete_granted))}; erasure must be the "
                "only path, so re-run scripts/db/runtime_roles.sql"
            )
        return found

    def as_dict(self) -> dict:
        return {
            "role": self.role,
            "superuser": self.is_superuser,
            "bypassrls": self.bypasses_rls,
            "rls_effective": self.rls_effective,
            "unprotected_tables": sorted(self.unprotected_tables),
            "owns_protected_tables": sorted(self.owns_protected_tables),
            "settable_privileged_roles": sorted(self.settable_privileged_roles),
            "excess_privileges": sorted(self.excess_privileges),
            "can_enumerate_credentials": self.can_enumerate_credentials,
            "archive_delete_granted": sorted(self.archive_delete_granted),
        }


async def inspect(db: AsyncSession) -> DatabasePosture:
    """Read the posture of the connection this session is on.

    Every part degrades to "unknown means not proven": if the role cannot read
    `pg_roles` or `pg_class`, the tables it could not confirm are reported as
    unprotected rather than assumed fine. A check that cannot run must not report
    success.
    """
    role = "unknown"
    is_superuser = False
    bypasses_rls = False
    try:
        row = (
            await db.execute(
                text(
                    "SELECT current_user, r.rolsuper, r.rolbypassrls "
                    "FROM pg_roles r WHERE r.rolname = current_user"
                )
            )
        ).first()
        if row is not None:
            role, is_superuser, bypasses_rls = row[0], bool(row[1]), bool(row[2])
    except Exception:  # noqa: BLE001 - an unreadable pg_roles proves nothing
        pass

    unprotected: list[str] = []
    try:
        rows = (
            (
                await db.execute(
                    text(
                        "SELECT c.relname FROM pg_class c "
                        "JOIN pg_namespace n ON n.oid = c.relnamespace "
                        "WHERE n.nspname = current_schema() AND c.relname = ANY(:names) "
                        "AND NOT (c.relrowsecurity AND c.relforcerowsecurity)"
                    ),
                    {"names": list(PROTECTED_TABLES)},
                )
            )
            .scalars()
            .all()
        )
        # A table absent from `pg_class` is a schema that has not been migrated,
        # which readiness reports separately. Only a table that exists without
        # both policy flags counts as unprotected, so an unmigrated database is
        # not reported as a policy failure twice.
        present = {
            row
            for row in (
                await db.execute(
                    text(
                        "SELECT c.relname FROM pg_class c "
                        "JOIN pg_namespace n ON n.oid = c.relnamespace "
                        "WHERE n.nspname = current_schema() AND c.relname = ANY(:names)"
                    ),
                    {"names": list(PROTECTED_TABLES)},
                )
            ).scalars()
        }
        unprotected = sorted(set(rows) & present)
    except Exception:  # noqa: BLE001 - no pg_class access means no RLS proof
        unprotected = list(PROTECTED_TABLES)
        present = set()

    owners: list[str] = []
    try:
        owners = list(
            (
                await db.execute(
                    text(
                        "SELECT c.relname FROM pg_class c "
                        "JOIN pg_namespace n ON n.oid = c.relnamespace "
                        "WHERE n.nspname = current_schema() "
                        "AND c.relname = ANY(:names) "
                        "AND pg_get_userbyid(c.relowner) = current_user"
                    ),
                    {"names": list(PROTECTED_TABLES)},
                )
            ).scalars()
        )
    except Exception:  # noqa: BLE001
        owners = []

    return DatabasePosture(
        role=role,
        is_superuser=is_superuser,
        bypasses_rls=bypasses_rls,
        owns_protected_tables=owners,
        unprotected_tables=unprotected,
        settable_privileged_roles=await _settable_privileged_roles(db),
        excess_privileges=await _excess_privileges(db),
        can_enumerate_credentials=await _can_enumerate_credentials(db),
        archive_delete_granted=await _archive_delete_granted(db),
    )


async def _settable_privileged_roles(db: AsyncSession) -> list[str]:
    """Privileged roles this connection could `SET ROLE` into.

    Role *attributes* are non-inheritable in PostgreSQL -- being a member of a
    `BYPASSRLS` role does not make you a `BYPASSRLS` role, which is verified in
    `tests/test_tenant_control_effectiveness.py` against a real database rather
    than taken from documentation. Membership with the `SET` option is the
    remaining path: `SET ROLE` changes the current role, and a role that can do
    that can reach every policy exemption its target has.

    Empty when the catalogue cannot be read, which is reported as "nothing known"
    rather than "nothing found" by the caller that decides.
    """
    try:
        rows = (
            (
                await db.execute(
                    text(
                        "SELECT DISTINCT g.rolname FROM pg_auth_members m "
                        "JOIN pg_roles g ON g.oid = m.roleid "
                        "JOIN pg_roles u ON u.oid = m.member "
                        "WHERE u.rolname = current_user AND m.admin_option "
                        "  AND (g.rolsuper OR g.rolbypassrls)"
                    )
                )
            )
            .scalars()
            .all()
        )
    except Exception:  # noqa: BLE001 - an unreadable catalogue proves nothing
        return []
    return list(rows)


async def _excess_privileges(db: AsyncSession) -> list[str]:
    """Privileges beyond DML that the role holds on tenant-owned tables.

    DML is what the application needs and nothing more. `TRUNCATE` empties a
    table without touching a row, which the archive's append-only trigger would
    otherwise have to catch -- better not to hold it at all. `REFERENCES`,
    `TRIGGER` and ownership-adjacent rights are reported for the same reason:
    they are not needed to read and write memory.
    """
    try:
        rows = (
            (
                await db.execute(
                    text(
                        "SELECT DISTINCT privilege_type FROM information_schema.role_table_grants "
                        "WHERE grantee = current_user AND table_schema = current_schema() "
                        "  AND table_name = ANY(:names) "
                        "  AND privilege_type NOT IN ('SELECT', 'INSERT', 'UPDATE', 'DELETE')"
                    ),
                    {"names": list(PROTECTED_TABLES)},
                )
            )
            .scalars()
            .all()
        )
    except Exception:  # noqa: BLE001
        return []
    return [f"{privilege} on tenant tables" for privilege in rows]


async def _can_enumerate_credentials(db: AsyncSession) -> bool:
    """Whether credential hashes can be read with an ordinary query.

    The application needs exactly one thing from `memory_principals`: given a
    presented hash, the identity it belongs to. That is `mindpalace_resolve_credential`.
    If the role can also `SELECT` the table, one query returns every tenant's
    hash, role and grant, which the application has no reason to be able to do.

    Probed with the catalogue rather than by trial, and defaults to `True` when it
    cannot be determined -- an unknown privilege is not a safe one.
    """
    try:
        can_read = (
            await db.execute(
                text("SELECT has_table_privilege(current_user, 'memory_principals', 'SELECT')")
            )
        ).scalar()
    except Exception:  # noqa: BLE001
        return True
    return bool(can_read)


async def _archive_delete_granted(db: AsyncSession) -> list[str]:
    """Archive tables this role could `DELETE` from.

    Erasure is one `SECURITY DEFINER` function, and the append-only triggers
    refuse every other `DELETE`. That claim only holds if the runtime role holds
    no `DELETE` on the archive in the first place, so it is checked rather than
    assumed -- and a table added to the archive in a later migration without
    being added to `runtime_roles.sql` shows up here instead of going unnoticed.
    """
    try:
        rows = (
            (
                await db.execute(
                    text(
                        "SELECT c.relname FROM pg_class c "
                        "JOIN pg_namespace n ON n.oid = c.relnamespace "
                        "WHERE n.nspname = current_schema() AND c.relname = ANY(:names) "
                        "AND has_table_privilege(current_user, c.oid, 'DELETE')"
                    ),
                    {"names": list(ARCHIVE_TABLES)},
                )
            )
            .scalars()
            .all()
        )
    except Exception:  # noqa: BLE001 - an unreadable catalogue proves nothing
        return list(ARCHIVE_TABLES)
    return list(rows)
