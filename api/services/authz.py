# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 Raunak Dey

"""The server-side authorization contract for tenant-owned memory.

Before this module, a corpus name in a request body was a routing hint and nothing
else: nothing authenticated the caller, and nothing stopped a caller naming a
corpus that belonged to somebody else. `docs/operations.md` already described the
intended contract -- one secret per client, bound to the corpora it may touch,
with a read-only or read-write role -- and this is that contract, implemented in
one place.

Three properties matter more than the shape of the API:

* **The corpus set is the decision.** A credential is issued for an explicit list
  of corpora, resolved from server-side rows. A corpus name arriving in a request
  is looked up in that set; it is never taken as proof of anything.
* **Deny by default.** `MIND_PALACE_AUTH_MODE=required` refuses a request with no
  credential, an unknown credential, a disabled or revoked credential, a corpus
  outside the credential's scope, and a write from a read-only credential. The
  only mode that does not is `off`, which is the documented single-user local
  workflow and is stated as unsafe on a shared network.
* **Out of scope reads as not found.** A corpus outside the scope is `404`, not
  `403`, because whether another tenant's corpus exists is not this caller's
  business.

The credential is an opaque random string. Only its SHA-256 hash is stored, the
hash is what the lookup index holds, and rotation is "insert a new row with the
same name, revoke the old one" -- which is why `name` is not unique. No token
format, signing scheme or expiry claim is defined here; none is needed, and
inventing one would add a protocol rather than a control.
"""

from __future__ import annotations

import hashlib
import hmac
import os
import secrets
from dataclasses import dataclass
from typing import Iterable, Literal

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

#: The two supported modes. Anything else is a configuration error and fails
#: closed rather than falling back to the permissive one.
MODE_OFF = "off"
MODE_REQUIRED = "required"
MODES = (MODE_OFF, MODE_REQUIRED)

#: Which kind of process this is. `server` is a promise, not a guess.
DEPLOYMENT_LOCAL = "local"
DEPLOYMENT_SERVER = "server"
DEPLOYMENTS = (DEPLOYMENT_LOCAL, DEPLOYMENT_SERVER)

#: The prefix of a presented credential. It carries no secret and exists so a
#: credential pasted into the wrong place is recognisable rather than mysterious.
SECRET_PREFIX = "mpk_"

#: What a credential may do, ordered by inclusion: each role is exactly the
#: union of the permissions below it and nothing else, so a role can be reasoned
#: about without reading every call site.
#:
#:   read memory        read any corpus it is granted
#:   write memory       record, ingest, sync, snapshot
#:   manage a corpus    create and erase the corpora it holds grants on
#:   manage credentials issue and revoke credentials
#:
#: Two roles rather than four, because a permission matrix is only useful if it
#: stays small enough to hold in one's head. `corpus_admin` is what a customer's
#: own service account needs; `principal_admin` is the deployment operator's,
#: and is deliberately the only role that can mint authority.
ROLE_READ_ONLY = "read_only"
ROLE_READ_WRITE = "read_write"
ROLE_CORPUS_ADMIN = "corpus_admin"
ROLE_PRINCIPAL_ADMIN = "principal_admin"

#: What each role may do. Anything not listed is denied.
PERMISSIONS = {
    ROLE_READ_ONLY: frozenset({"read"}),
    ROLE_READ_WRITE: frozenset({"read", "write"}),
    ROLE_CORPUS_ADMIN: frozenset({"read", "write", "manage_corpus"}),
    ROLE_PRINCIPAL_ADMIN: frozenset({"read", "write", "manage_corpus", "manage_principals"}),
}

Role = Literal["read_only", "read_write", "corpus_admin", "principal_admin"]


class AuthorizationError(Exception):
    """A refusal, carrying the public code and status the caller should see."""

    def __init__(self, code: str, message: str, status_code: int):
        self.code = code
        self.message = message
        self.status_code = status_code
        super().__init__(message)


def unauthenticated(message: str = "A valid bearer credential is required") -> AuthorizationError:
    return AuthorizationError("unauthenticated", message, 401)


def forbidden(message: str) -> AuthorizationError:
    return AuthorizationError("forbidden", message, 403)


def not_found(message: str = "Corpus not found") -> AuthorizationError:
    return AuthorizationError("corpus_not_found", message, 404)


@dataclass(frozen=True)
class Principal:
    """Who is asking, and which corpora that identity may reach.

    ``unrestricted`` is true only in `off` mode, and for the explicitly
    administrative principal. It is a distinct field rather than a wildcard in
    ``corpora`` so that "this request was never authenticated" stays visible at
    every call site instead of being folded into a very large allowed set.

    Both names and ids are carried. Names are what a request names and what the
    authorization check compares; ids are what the row-level policy compares. The
    two are resolved once, here, from the same join, so a policy can never be
    scoped to a corpus id that a differently-spelled name would select.

    The vocabulary is deliberately not interchangeable. An ``Identity`` is a
    human or service in the *hosting application*; Mind Palace never sees one. A
    ``Principal`` is this credential. A ``Tenant`` is the host application's word
    for a customer, realised here as a set of granted corpora. A ``Corpus`` is
    one memory collection. A ``Resource`` is a document or claim inside one, and
    carries no ownership of its own -- see `docs/security.md`.
    """

    id: str
    name: str
    role: Role
    corpora: frozenset[str]
    corpus_ids: frozenset[str] = frozenset()
    unrestricted: bool = False

    @property
    def permissions(self) -> frozenset[str]:
        """What this principal may do, independent of any particular corpus."""
        if self.unrestricted:
            return frozenset().union(*PERMISSIONS.values())
        return PERMISSIONS.get(self.role, frozenset())

    def can(self, permission: str) -> bool:
        return permission in self.permissions

    @property
    def can_write(self) -> bool:
        return self.can("write")

    @property
    def can_manage_corpora(self) -> bool:
        return self.can("manage_corpus")

    @property
    def can_manage_principals(self) -> bool:
        return self.can("manage_principals")

    def allows(self, corpus: str) -> bool:
        return self.unrestricted or corpus in self.corpora

    def scope(self) -> str:
        """The value the row-level policy reads for this principal.

        Empty when the principal is unrestricted, because the unrestricted value
        is a constant and does not have to be derived from a table: there is no
        credential in that mode, and inventing one would be a second way to be
        wrong.
        """
        return UNRESTRICTED if self.unrestricted else ",".join(sorted(self.corpus_ids))


#: The value that matches every corpus. Written by the application in the modes
#: where that is intended, and never derived from a request.
UNRESTRICTED = "{*}"

#: The principal used by the documented single-user local workflow. It is not an
#: identity and must never be treated as one; it exists so that the same code
#: path serves both modes.
LOCAL_PRINCIPAL = Principal(
    id="local",
    name="local",
    role=ROLE_READ_WRITE,
    corpora=frozenset(),
    unrestricted=True,
)

#: The principal used by credential administration, which by definition reaches
#: every corpus: it has to, to grant or revoke access to any of them. Reaching
#: it requires database access, which is already a stronger boundary than this
#: module, so it is modelled as an explicit administrative identity rather than
#: as a hidden bypass.
ADMIN_PRINCIPAL = Principal(
    id="admin",
    name="admin",
    role=ROLE_PRINCIPAL_ADMIN,
    corpora=frozenset(),
    unrestricted=True,
)


def require_permission(principal: Principal, permission: str, what: str) -> None:
    """Refuse an action this identity may not perform, whatever its corpus scope.

    Separate from `require_corpus` on purpose. Corpus scope answers "may this
    identity reach that corpus"; this answers "may this identity perform this
    kind of action at all". A credential granted every corpus of an organisation
    still cannot mint another one, which is the distinction that stops a
    compromised tenant credential from becoming a foothold on the next tenant.
    """
    if principal.unrestricted or principal.can(permission):
        return
    raise forbidden(f"This credential may not {what}")


def require_role(role: str, allowed: Iterable[str], what: str) -> None:
    """Refuse an administrative action the role does not carry.

    Used on the paths that run outside a request -- the operator CLI -- where
    there is no resolved principal to check and the role is the whole of the
    caller's authority.
    """
    if role in set(allowed):
        return
    raise forbidden(f"This credential may not {what}")


def auth_mode() -> str:
    """The configured mode. Defaults to the documented single-user workflow."""
    mode = os.getenv("MIND_PALACE_AUTH_MODE", MODE_OFF).strip().lower()
    return mode if mode in MODES else MODE_REQUIRED


def auth_required() -> bool:
    return auth_mode() == MODE_REQUIRED


def describe() -> dict:
    """What `/health/ready` reports, so a deployment cannot be tenant-safe by
    accident: an operator can see which mode answered."""
    return {"mode": auth_mode(), "required": auth_required()}


def deployment_mode() -> str:
    """`local` (default) or `server`, from an explicit setting.

    Not inferred. A heuristic -- "is this a socket?", "is there a DATABASE_URL?"
    -- is wrong in both directions: a container on a private network is still a
    server, and a developer on a laptop is still local. The operator states which
    one they are running, and `server` is a promise the code holds them to.

    An unrecognised value raises rather than being reinterpreted. The previous
    version silently mapped anything unknown to `server`, which fails closed for
    *authentication* but not for the whole configuration: a typo like `prod` left
    the ingest-root and database-posture checks unenforced while the service ran
    normally. A value nobody recognises is a mistake, and mistakes are refused.
    """
    raw = os.getenv("MIND_PALACE_DEPLOYMENT", DEPLOYMENT_LOCAL).strip().lower()
    if raw in DEPLOYMENTS:
        return raw
    raise ValueError(
        f"MIND_PALACE_DEPLOYMENT={raw!r} is not a known deployment profile; "
        f"expected one of {', '.join(DEPLOYMENTS)}"
    )


def server_mode_errors() -> list[str]:
    """Reasons this configuration must not serve, empty if it may.

    The point is that a deployment configured as a server cannot come up
    answering unauthenticated requests. `docker compose up` and a bare
    `uvicorn api.main:app` both bind a published port and neither sets this, so
    the failure has to be loud rather than a warning nobody reads.
    """
    problems = []
    try:
        mode = deployment_mode()
    except ValueError as exc:
        # An unknown profile is refused outright. It is not treated as `local`,
        # because that would apply local-only relaxations to a deployment nobody
        # has described.
        return [str(exc)]
    if mode != DEPLOYMENT_SERVER:
        return problems
    if not auth_required():
        problems.append(
            "MIND_PALACE_DEPLOYMENT=server requires MIND_PALACE_AUTH_MODE=required; "
            "an unauthenticated service on a network is not tenant-scoped"
        )
    # Imported here because the module imports this one; a top-level import would
    # be circular.
    from api.services.ingest_paths import server_mode_errors as ingest_problems

    problems.extend(ingest_problems())
    return problems


def assert_server_configuration() -> None:
    """Refuse to serve a configuration that claims to be a server and is not.

    Called from the application factory and from `serve`. Raising here is
    deliberate: a process that has already printed a banner and accepted
    connections is harder to notice than one that exits with a reason.
    """
    problems = server_mode_errors()
    if problems:
        raise RuntimeError(
            "refusing to start: "
            + "; ".join(problems)
            + ". See docs/security.md for what a network deployment must set."
        )


def _hash(secret: str) -> str:
    return hashlib.sha256(secret.encode("utf-8")).hexdigest()


def new_principal_id() -> str:
    """A fresh id per issuance, not one per name.

    Rotation needs the old and the new credential to coexist for the length of a
    deploy: the new one is issued and deployed first, and only then is the old
    one revoked. An id derived from the name would make the second issuance
    collide with the first, and rotation would become "replace", which takes the
    old secret away before the new one is in place. `name` is therefore a label
    an operator reads, and is deliberately not unique.
    """
    return secrets.token_hex(32)


def _parse_bearer(header: str | None) -> str:
    if not header:
        raise unauthenticated("A bearer credential is required")
    scheme, _, value = header.partition(" ")
    if scheme.lower() != "bearer" or not value.strip():
        raise unauthenticated("Expected an `Authorization: Bearer <credential>` header")
    return value.strip()


async def resolve_principal(db: AsyncSession, authorization: str | None = None) -> Principal:
    """The authenticated caller, or a refusal.

    In `off` mode there is no caller to authenticate and the local principal is
    returned. In `required` mode the credential must be presented, must exist,
    must not be disabled or revoked, and must still resolve to at least one
    corpus: a credential with an empty scope is a credential that can do nothing,
    and letting it through would turn a configuration mistake into a silent
    allow.
    """
    if not auth_required():
        return LOCAL_PRINCIPAL

    presented = _parse_bearer(authorization)
    # Through `mindpalace_resolve_credential`, not a direct `SELECT`. The function
    # is `SECURITY DEFINER`, so the runtime role can call it without holding
    # `SELECT` on `memory_principals` or `memory_principal_corpora` -- which is
    # what stops a query bug, a compromised endpoint or a curious operator with
    # the application role from enumerating every tenant's credential hashes,
    # roles and grants at once.
    #
    # One call, not two: the identity and its scope arrive together. It also used
    # to be one round trip fewer per authenticated request.
    rows = (
        await db.execute(
            text(
                "SELECT id, name, role, corpus_name, corpus_id "
                "FROM mindpalace_resolve_credential(:digest)"
            ),
            {"digest": _hash(presented)},
        )
    ).fetchall()
    if not rows:
        # One message for "no such credential", "revoked", and "bound to nothing":
        # the difference between them is only useful to someone enumerating
        # credentials.
        raise unauthenticated("The bearer credential is not recognised")

    first = rows[0]
    return Principal(
        id=first[0].rstrip(),
        name=first[1],
        role=first[2],
        corpora=frozenset(row[3] for row in rows),
        corpus_ids=frozenset(row[4].rstrip() for row in rows),
    )


def require_corpus(principal: Principal, corpus: str, *, write: bool = False) -> None:
    """Refuse a corpus this identity may not use. The single enforcement point.

    Called from the one place a corpus name is turned into a corpus id, so every
    path that reaches tenant-owned rows passes through it: the nine memory
    operations, the feed, `remember`, the corpora routes, search, context and the
    query routes. Adding a route that resolves a corpus any other way is a gap,
    and `tests/test_tenant_isolation.py` asserts the coverage rather than
    trusting this comment.
    """
    if not principal.allows(corpus):
        raise not_found()
    if write and not principal.can_write:
        raise forbidden("This credential is read-only")


async def issue_credential(
    db: AsyncSession,
    name: str,
    corpus_names: Iterable[str],
    role: Role = ROLE_READ_WRITE,
    *,
    principal: Principal | None = None,
) -> dict:
    """Create a credential and return its secret exactly once.

    Only the hash is stored, so this is the only moment the secret exists
    outside the caller. Calling it twice with the same name issues two distinct
    credentials, which is what makes rotation possible: deploy the new secret,
    then `revoke_credential` the old id. Both rows remain visible in
    `list_credentials`, so an operator can see that a rotation happened.

    Issuing is a `manage_principals` operation and is checked against the *caller*
    rather than assumed. The corpora are then resolved under that same caller, so
    a credential that holds `manage_principals` over its own corpora can grant
    those and cannot grant anything else -- an operator cannot use this to widen
    its own reach, which is the whole point of checking.
    """
    from api.services.corpora import get_corpus_by_name
    from api.services.db import current_principal

    if role not in PERMISSIONS:
        raise ValueError(f"role must be one of {', '.join(sorted(PERMISSIONS))}; got {role!r}")
    who = principal or current_principal()
    require_permission(who, "manage_principals", "issue credentials")

    corpora = []
    for corpus in dict.fromkeys(corpus_names):
        # Resolved under the caller, not under an administrative scope: a
        # principal that may manage credentials may still only hand out corpora it
        # already reaches.
        row = await get_corpus_by_name(db, corpus)
        if row is None:
            raise ValueError(f"corpus '{corpus}' is not available to you")
        corpora.append((corpus, row["id"]))

    principal_id = new_principal_id()
    secret = SECRET_PREFIX + secrets.token_urlsafe(32)
    await db.execute(
        text(
            "INSERT INTO memory_principals (id, name, key_hash, role) "
            "VALUES (:id, :name, :digest, :role)"
        ),
        {"id": principal_id, "name": name, "digest": _hash(secret), "role": role},
    )
    stored = principal_id
    # The grants are replaced rather than merged: issuing a credential with
    # `--corpus a --corpus b` means exactly those two. An older scope cannot
    # survive by omission, which is what makes "revoke then reissue" a reliable
    # way to narrow a credential's reach.
    await db.execute(
        text("DELETE FROM memory_principal_corpora WHERE principal_id = :pid"),
        {"pid": stored},
    )
    for corpus, corpus_id in corpora:
        await db.execute(
            text(
                "INSERT INTO memory_principal_corpora (principal_id, corpus_id) "
                "VALUES (:pid, :cid)"
            ),
            {"pid": stored, "cid": corpus_id},
        )
    await db.commit()
    return {
        "id": stored,
        "name": name,
        "role": role,
        "secret": secret,
        "corpora": [corpus for corpus, _ in corpora],
    }


async def _resolve_credential_id(db: AsyncSession, needle: str) -> str | None:
    """The one live credential `needle` identifies, or None.

    Accepts an id, an id prefix, or a name -- because an operator types the name
    they gave a credential, and `list_credentials` prints a 12-character prefix.
    Names are deliberately not unique, because rotation issues a second
    credential under the same name while both are live.

    Ambiguity is refused rather than resolved arbitrarily: acting on the wrong
    credential is worse than an error, and silently picking the newest of two is
    exactly how a revoke ends up revoking the credential someone just deployed.

    Routed through `mindpalace_locate_credential` rather than a `SELECT`, because
    the runtime role deliberately cannot read `memory_principals` at all. Same
    reason `resolve_principal` uses a function: the credential table is not
    something an ordinary query should be able to enumerate.
    """
    trimmed = needle.strip()
    if not trimmed:
        return None
    found = (
        await db.execute(text("SELECT mindpalace_locate_credential(:n)"), {"n": trimmed})
    ).scalar_one_or_none()
    return found.rstrip() if found else None


async def grant_corpora(
    db: AsyncSession,
    principal_id: str,
    corpus_names: Iterable[str],
    *,
    principal: Principal | None = None,
) -> list[str]:
    """Add corpora to a credential that already exists.

    The alternative is reissuing, which changes the secret and therefore breaks
    every running deployment of the caller -- so it is only ever the rotation
    step, never the way access is widened. Adding a corpus does not expose the
    old secret, and does not require it.

    Returns the corpus names added, so a repeat call is visibly a no-op rather
    than an indistinguishable success.
    """
    from api.services.corpora import get_corpus_by_name
    from api.services.db import current_principal

    who = principal or current_principal()
    require_permission(who, "manage_principals", "grant access to a corpus")
    target = await _resolve_credential_id(db, principal_id)
    if target is None:
        raise ValueError(f"no live credential matches {principal_id!r}")

    added = []
    for corpus in dict.fromkeys(corpus_names):
        row = await get_corpus_by_name(db, corpus)
        if row is None:
            raise ValueError(f"corpus '{corpus}' is not available to you")
        result = await db.execute(
            text(
                "INSERT INTO memory_principal_corpora (principal_id, corpus_id) "
                "VALUES (:pid, :cid) ON CONFLICT DO NOTHING"
            ),
            {"pid": target, "cid": row["id"]},
        )
        if result.rowcount:
            added.append(corpus)
    await db.commit()
    return added


async def revoke_corpora(
    db: AsyncSession,
    principal_id: str,
    corpus_names: Iterable[str],
    *,
    principal: Principal | None = None,
) -> list[str]:
    """Remove corpora from a credential without revoking the credential.

    This is offboarding one collection rather than a whole identity: the secret
    keeps working for everything it was not removed from. Removing the last one
    leaves a credential that resolves to nothing, which `resolve_principal`
    refuses -- so an operator who wants the identity gone should revoke it.
    """
    from api.services.corpora import get_corpus_by_name
    from api.services.db import current_principal

    who = principal or current_principal()
    require_permission(who, "manage_principals", "remove access to a corpus")
    target = await _resolve_credential_id(db, principal_id)
    if target is None:
        raise ValueError(f"no live credential matches {principal_id!r}")

    removed = []
    for corpus in dict.fromkeys(corpus_names):
        row = await get_corpus_by_name(db, corpus)
        if row is None:
            # Already gone from the caller's view; nothing to remove and nothing
            # to disclose.
            continue
        result = await db.execute(
            text(
                "DELETE FROM memory_principal_corpora "
                "WHERE principal_id = :pid AND corpus_id = :cid"
            ),
            {"pid": target, "cid": row["id"]},
        )
        if result.rowcount:
            removed.append(corpus)
    await db.commit()
    return removed


async def revoke_credential(
    db: AsyncSession,
    principal_id: str,
    *,
    principal: Principal | None = None,
) -> bool:
    """Revoke immediately. Revocation is not a soft flag a request can outlive.

    Accepts a full id or a prefix, because `list_credentials` prints a prefix and
    an operator revoking by eye should not have to select the full 64 characters.

    Takes effect on the next request: the lookup excludes disabled and revoked
    rows, and nothing caches a resolved principal across requests in the HTTP
    path.
    """
    from api.services.db import current_principal

    who = principal or current_principal()
    require_permission(who, "manage_principals", "revoke credentials")
    matched = await _resolve_credential_id(db, principal_id)
    if matched is None:
        # Either no match, or an ambiguous one. Both are False, and both leave
        # every credential untouched -- revoking the wrong one is worse than
        # refusing.
        return False
    result = await db.execute(
        text(
            "UPDATE memory_principals SET disabled = TRUE, revoked_at = clock_timestamp() "
            "WHERE id = :pid AND NOT disabled"
        ),
        {"pid": matched},
    )
    await db.commit()
    return bool(result.rowcount)


async def list_credentials(db: AsyncSession) -> list[dict]:
    """Audit view: which credentials exist and what they may reach.

    Hashes are never returned. A caller that can read this can see that a
    credential exists and what it is scoped to, which is the same thing an
    operator needs in order to revoke it.

    Unlike `resolve_principal` and `revoke_credential`, this one enumerates
    rather than looks up, and there is no narrow version of it -- an audit view
    that answered "which of these hashes is this?" would be the enumeration
    again. So it requires the maintenance role, and says so plainly rather than
    failing with a permission error the operator has to decode.
    """
    try:
        await db.execute(text("SELECT id FROM memory_principals LIMIT 1"))
    except Exception:  # noqa: BLE001 - translated into an actionable message
        raise ValueError(
            "listing credentials needs the maintenance database role: "
            "`mindpalace credential list` reads every credential, and the runtime "
            "role is deliberately not permitted to do that. Connect with "
            "MIGRATION_DATABASE_URL or the owner role."
        ) from None
    rows = (
        await db.execute(
            text(
                "SELECT p.id, p.name, p.role, p.disabled, p.revoked_at, p.created_at, "
                "       coalesce(array_agg(c.name ORDER BY c.name) "
                "                FILTER (WHERE c.name IS NOT NULL), '{}') "
                "FROM memory_principals p "
                "LEFT JOIN memory_principal_corpora g ON g.principal_id = p.id "
                "LEFT JOIN corpora c ON c.id = g.corpus_id "
                "GROUP BY p.id, p.name, p.role, p.disabled, p.revoked_at, p.created_at "
                "ORDER BY p.name, p.created_at"
            )
        )
    ).fetchall()
    return [
        {
            "id": row[0].rstrip(),
            "name": row[1],
            "role": row[2],
            "disabled": row[3],
            "revoked_at": row[4],
            "created_at": row[5],
            "corpora": list(row[6]),
        }
        for row in rows
    ]


def constant_time_equals(left: str, right: str) -> bool:
    """Exposed for tests and for callers comparing a presented secret directly."""
    return hmac.compare_digest(left, right)
