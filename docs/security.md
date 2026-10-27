# Security and multi-tenant integration

What Mind Palace protects, what it deliberately does not, and what has to be
configured before real customer data goes in.

This document replaces the older claim that a corpus name is a tenancy
boundary. It is not. A corpus is a *namespace*; whether a caller may address one
is a separate decision, and **before the 0.9.x train** there was no code making
that decision at all.

> **Which release this describes.** Everything below describes the code at
> `main` on the 0.9.x train — that is, `v0.9.0` plus migrations `008`, `009` and
> `010`, none of which have shipped in a tag. Two things below are therefore
> *not* true of `v0.9.0` itself: the tenant scope binding that `010` introduces,
> and the `{*}` handling that `008` introduced. If you are on `v0.9.0`, you do
> not have either. Read this document against the commit you are running, not
> against the tag name.

## Summary

| Profile | Configuration | Safe for |
| --- | --- | --- |
| `local` (default) | unset | a local single-user machine, a trusted network segment |
| `server` | `MIND_PALACE_DEPLOYMENT=server` + `MIND_PALACE_AUTH_MODE=required` + `MIND_PALACE_INGEST_ROOTS` + the least-privileged database role | a shared, multi-user deployment |
| anything else | — | **refused** |

`local` with authentication off is the default because the documented five-minute
path is a local single-user workflow and changing that default would break every
existing installation. It is **not** a shared deployment: it answers anyone who
can reach the port, for every corpus the database user can see.

The `server` profile is what stops that from happening by accident. It refuses to
start — in the application lifespan, before the process accepts a request — if
authentication is off, if the connected database role is a superuser or has
`BYPASSRLS`, if row-level security is missing or unforced on a tenant table, if
the role can enumerate credential hashes, or if it holds `DELETE` on the archive.
`/health/ready` reports the *verdict* — `503` when those conditions are not met —
so a deployment pipeline sees them too, without the endpoint publishing the
database's defences to whatever probes it.

For an integrator the short version is: **one credential per application, scoped
to the corpora it needs, running as a database role that cannot see the other
tenants — and a `404` whenever it asks for one that is not its own.**

## The boundary, and where each layer sits

Three layers, each independently sufficient to stop a cross-tenant read, and
each provably load-bearing (`tests/test_tenant_control_effectiveness.py`
removes them one at a time).

1. **Authentication — `api/services/authz.py`.** A credential is an opaque
   random string, presented as `Authorization: Bearer <secret>`. Only its
   SHA-256 hash is stored; the hash is what the lookup index holds, so lookup is
   an indexed equality rather than a comparison over every secret. A credential
   is bound to a role (`read_only` / `read_write`) and to an explicit set of
   corpora. Missing, unknown, disabled, revoked, or corpus-less credentials are
   refused in `required` mode.

2. **Authorization — one choke point.** `corpora.get_corpus_by_name` is the only
   function that turns a corpus name into a corpus id, and it is where the
   decision is made. Every route that reaches tenant-owned rows goes through it:
   the nine memory operations, the operational feed, `remember`, search, context,
   the query routes, corpus administration and ingestion. A corpus outside the
   caller's scope is `404`, not `403`, because whether another tenant's corpus
   exists is not the caller's business. Creating a corpus and granting a
   credential are administrative operations and need an unrestricted identity.

3. **Row-level security — migrations `008`, `010` and `014`.** Every tenant-owned
   table carries a policy keyed on `corpus_id`, with `ENABLE` and `FORCE`. `chunks`
   has no corpus column, so its policy reaches the corpus through the document it
   belongs to, whose own policy already applies inside the sub-query. The
   predicate reads one transaction-local setting and returns `false` when it is
   absent, so a query that runs without a tenant scope sees no rows and writes
   none. Migration `010` binds the unrestricted `{*}` sentinel to roles that
   already read every row, so it cannot be self-asserted by the runtime role.
   Migration `014` adds a companion `mindpalace.scope_token` setting: the
   application sets it to an HMAC-SHA-256 of the tenant scope under
   `MIND_PALACE_SCOPE_SECRET` (default `dev-scope-secret`), and the row-level
   policy verifies it inside `mp_verify_scope_token`, a `SECURITY DEFINER`
   function owned by the maintenance role. The server secret lives in
   `memory_server_secrets`, a table the runtime role cannot read. A non-empty but
   forged token therefore fails closed and returns zero rows; an absent or empty
   token falls back to plain scope membership (the residual scope forge remains
   documented below).

## Every surface reaches the same boundary

The layers above are only as good as the number of doors into them. There are
five, and each is covered:

| Surface | How it establishes the principal |
| --- | --- |
| REST (`api/routers/`) | The `Authorization` header, resolved by middleware |
| Python SDK, local | `api_key=` at construction, resolved before any operation |
| Python SDK, HTTP | The `Authorization` header on each request |
| Local runtime socket | `MIND_PALACE_RUNTIME_CREDENTIAL` on the serving process |
| MCP | `MIND_PALACE_MCP_CREDENTIAL` on the server process |

The last two are configured by the *process*, never by the caller. An MCP tool
argument or a socket request that named its own credential would not be a
boundary at all, so neither exists; `tests/test_credential_management.py` asserts
the absence.

Two of these were bypasses when this document was written, and both are now
pinned by tests:

* The runtime socket authenticated nobody. Its token proved the caller was a
  local peer of the same user and decided nothing about which corpora that peer
  could reach, so it dispatched any corpus named on the wire — read or write — in
  every mode. It now resolves a principal per dispatch and scopes each operation.
* An SDK client constructed without a corpus name — the exact shape the CLI
  builds — skipped authentication entirely and ran unrestricted, even under
  `MIND_PALACE_AUTH_MODE=required`.

A boundary that holds on REST and not on the socket is not a boundary.

## Tenant scope and connection reuse

The scope is set on SQLAlchemy's `after_begin`, from a `ContextVar` holding the
request's principal. Two properties follow and both are load-bearing:

* **Transaction-local.** `set_config(..., is_local := true)` is discarded when
  the transaction ends, so a connection returned to the pool cannot carry a
  tenant scope into the next request. Connection reuse cannot change an
  authorization decision.
* **Derived from the request, not the connection.** The same connection serves
  different tenants correctly.

`tests/test_tenant_connection_isolation.py` reuses one pooled connection across
Alpha → Beta → Alpha, across an exception, across a rollback, across a
cancellation and across a protocol error, and asserts the backend process id is
genuinely the same in both tenants. A test that silently stopped sharing a
connection would fail rather than pass for the wrong reason.

## Tenant-to-memory mapping

```
workspace   ──►  ws-<workspace>-brand            (brand memory)
campaign    ──►  ws-<workspace>-c-<campaign>     (campaign memory)
```

The mapping is a pure function of application-owned records. No Mind Palace
method accepts a corpus name, so a client-supplied one has nowhere to land.
Brand memory is deliberately shared *inside* a workspace; campaign memory is
not shared even between two campaigns of the same brand.

The implicit `default` corpus is not a tenant namespace. In `required` mode a
credential must be granted `default` explicitly to use it.

## Database roles

Row-level security is inert for a superuser, a `BYPASSRLS` role, or the table
owner. `scripts/db/runtime_roles.sql` creates the separation:

| Role | Privileges | Used by |
| --- | --- | --- |
| `mindpalace_owner` | owns the schema, `BYPASSRLS` | migrations and maintenance |
| `mindpalace_runtime` | DML only, `NOSUPERUSER NOBYPASSRLS`, owns nothing | the application |

```bash
psql "$ADMIN_DATABASE_URL" -v runtime_password="$MIND_PALACE_RUNTIME_PASSWORD" \
    -f scripts/db/runtime_roles.sql
```

The password is a `psql` variable rather than a line in the file, so the script
contains no credential, and a password with a quote or a semicolon in it is
quoted as a literal rather than ending the statement. Supplying no password is
refused, because a role that exists but cannot log in reads as an RLS problem and
is not one. If a secret manager holds the password, provision the role with
`-v runtime_password_skip=on` and set it there.

**The script also does not print the password.** It used to, on every run:
`SELECT set_config('mindpalace.runtime_password', ...)` returns the value it was
given, and `psql` prints every result set, so the runtime role's password landed
in the output — including in an archived CI log readable by anyone with repository
access. The assignment now ends in `\g /dev/null`, which runs the statement and
discards the result. Verified this session by running the shipped script with a
canary password against a migrated database: zero occurrences in stdout, zero in
stderr, exit 0, and the role still authenticates and is still `NOSUPERUSER
NOBYPASSRLS`.

Two channels remain visible to anyone who can already see the process, and the
script does not claim otherwise: the `-v runtime_password=...` on the command line
is visible in `ps`, and in shell history. Neither is a property of this script.
Use `-v runtime_password_skip=on` with a secret manager if that matters to you.

Run the application with `DATABASE_URL` pointing at `mindpalace_runtime`. A
deployment that runs the ordinary application as a superuser gets **no** tenant
isolation from migration `008`, whatever the mode says.

### Verifying it

Do not take that on trust — check the role the process would actually connect as:

```bash
mindpalace posture
```

```
role:            mindpalace_runtime
superuser:       False
BYPASSRLS:       False
RLS effective:   True
```

It exits non-zero and names the problem if the role would bypass the policies.

`/health/ready` reports the **verdict**, not the inventory:

```json
{
  "status": "ready",
  "checks": {"database": "ok", "row_level_security": "enforced"},
  "auth": {"mode": "required", "required": true}
}
```

and answers `503` with `"row_level_security": "not_enforced"` when the connected
role would bypass the policies — **in `server` mode**. In `local` it keeps
answering `ready`, because there is no second tenant to isolate from and the
documented workflow is a developer connecting as their own superuser; that is the
same rule that gates refusing to start. A role whose posture cannot be determined
is `not_ready` in `server`, not `ready` — an unverifiable guarantee is not a
guarantee.

The split is deliberate. Readiness is read by whatever probes the service: a
load balancer, a sidecar, a health dashboard, and in a misconfigured deployment
anything else that can reach the port. It used to answer with the connected
role's name, whether it was a superuser, whether it held `BYPASSRLS`, whether it
could enumerate credential hashes, and which archive tables it could `DELETE`.
None of that belongs in an anonymous response. The detail is what
`mindpalace posture` prints, for an operator who already holds database
credentials.

`auth.required: true` says the application checks credentials. It says nothing
about whether the database enforces anything, and the two answers were routinely
disagreeing before this check existed: a service could report itself
tenant-scoped while running as a superuser with migration `008` entirely inert.

`mindpalace_owner` holding `BYPASSRLS` is deliberate and is the honest limit of
this design: whoever holds it can read every tenant's rows. It is a maintenance
credential, not an application credential.

## Vocabulary

These are not interchangeable, and most isolation bugs are a confusion between
two of them.

| Term | Means | In this codebase |
| --- | --- | --- |
| **Identity** | a human, account or service in the *hosting application* | never seen. Mind Palace has no users. |
| **Principal** | a Mind Palace credential and the permissions attached to it | `authz.Principal` |
| **Tenant / workspace** | a customer's isolation domain | *not a table*. Realised as a set of granted corpora. |
| **Corpus** | one memory collection, with its own policy | `corpora.id`; the boundary RLS enforces |
| **Resource** | a document, chunk, claim or evidence row inside a corpus | **carries no ownership of its own** — see below |

There is deliberately no `tenants` table. A tenant is a set of corpora a
credential holds grants on, and the mapping is made by the hosting application
from rows it already owns. Adding a table would have meant a second place for the
same decision to be wrong.

### Why a resource has no owner

The isolation granularity is the **corpus**, and that is a deliberate ceiling, not
an unfinished feature. Within a shared corpus, every authorized principal sees
the same claims. There is no per-document or per-claim ownership, because:

* a `RESTORED` version, a superseding claim and a snapshot membership all
  reference rows across the whole corpus, so ownership would have to be decided
  for derived rows that no user ever authored;
* retrieval is corpus-scoped by construction — a forgotten predicate is contained
  by RLS, but a *mis-assigned* owner would silently hide a fact someone else
  authored, which is a worse failure than over-sharing within a boundary the
  customer chose;
* a half-implemented `owner_id` would leave every existing retrieval path
  unrestricted while looking like it filtered.

**Use a personal corpus for personal data.** That is what the corpus boundary is
for, and it needs no schema change. The four supported patterns are in
[Integration patterns](#integration-patterns).

## Integration patterns

All four are supported by the same model. None requires a second Mind Palace.

**A — personal memory.** One corpus per person. Alice holds a grant on hers and
nothing of Bob's. Her credential cannot enumerate his corpus names, and a request
naming one is a `404`, not a `403`.

**B — shared workspace.** One corpus for the workspace, granted to each member's
credential explicitly. Everyone in the workspace sees the same memory, and
nobody outside it can reach it. Sharing is a grant, never inferred from a name or
an ownership label.

**C — separate customers.** Disjoint corpora and disjoint credentials. §10 of the
isolation suite.

**D — several applications, one tenant.** Several credentials over the same
corpora, each with the least role it needs: a read-only integration, a backend
that writes, an administrative one. Credentials are named for their purpose and
revoked individually.

```bash
mindpalace corpus create acme-brand
mindpalace credential issue acme-backend   --corpus acme-brand --role read_write
mindpalace credential issue acme-readonly  --corpus acme-brand --role read_only
mindpalace credential issue acme-worker    --corpus acme-brand --role corpus_admin
```

## Roles and permissions

Cumulative, and deny-by-default. Anything not listed is refused.

| Role | read | write | manage a corpus | manage credentials |
| --- | --- | --- | --- | --- |
| `read_only` | yes | — | — | — |
| `read_write` | yes | yes | — | — |
| `corpus_admin` | yes | yes | erase corpora it holds grants on | — |
| `principal_admin` | yes | yes | yes | issue and revoke credentials |

Two boundaries this draws that read/write alone did not:

* **A workspace credential cannot mint authority.** Holding every corpus of an
  organisation is not the same as being able to issue credentials, so a
  compromised tenant credential is not a foothold on the next tenant.
* **Creating a corpus still needs the unrestricted administrative identity**, and
  that is not an oversight. A corpus row is only insertable while its id is
  inside the caller's own tenant scope, and a principal cannot hold a grant to
  something that does not exist yet. A tenant-scoped credential can therefore
  never be the first to create one, however its role is spelled.

Permissions are checked against the *caller* in `require_permission`, and the
corpora are then resolved under that same caller — so a `principal_admin` can
hand out only the corpora it already reaches.

## Threat model

**The attacker controls:** an HTTP client, an MCP tool caller, and anything they
can put in a request body, query string, path or header.

**Credentials they may hold:** none, an expired or revoked one, a valid one
issued to a *different* workspace, or a valid one with read-only role. All four
are refused, and refusal is identical whether the named corpus exists or not.

**Shared resources:** the database, the connection pool, the process, the
operating system. Corpus contents are not shared; the trusted application code
is.

**Trusted:** the application process, the middleware and the authorization choke
point, the runtime database role, the operating system and the deployment
operator. Untrusted: everything a caller can send.

**Tenant identity derivation:** the credential, resolved to a membership, from
server-side rows. Never from `tenant_id`, `workspace_id`, `corpus_id`, a body
field or a header. The SDK has no parameter that accepts a corpus name.

**Enforcement:** `get_corpus_by_name` for ownership, the role column for
permissions, the row-level policy as a backstop. Neither is sufficient alone, and
the negative tests show each catching what the other would miss.

**A database administrator can still:** read everything, with `mindpalace_owner`.
Ordinary tenant isolation is not protection against a trusted superuser, and no
combination of row-level security and receipts changes that.

**Logs, backups, files and receipts:** operational logs carry sizes, counts and
timings, never memory content and never query text. Receipts are integrity
artefacts; a receipt
proves a document has not changed, not that its content is true, and not that
the caller was entitled to it — a receipt is only ever produced *after*
authorization. Backups and replicas inherit the database's own protection and
are outside this boundary.

**One exception, and it is opt-in and local.** The flight recorder at
`~/.mind-palace/logs/queries.jsonl` writes a row per call, and a row *does* carry
the caller's query text verbatim to 512 characters — grading real usage needs the
question, not a hash of it. So it is off unless `MIND_PALACE_QUERY_LOG=1` is set,
and it never leaves the machine that set it. The directory is created `0700` and
the file `0600`, both re-asserted on every write so a file left by an older
version is tightened rather than staying world-readable. A row holds the
timestamp, operation, corpus, query text, `match_status`, up to three scores,
returned claim ids and latency — never claim text, never a source path. See
`docs/mcp.md` for the setting that turns it on during local evaluation.

### Threat matrix

| Threat | Position | Where it is enforced and tested |
| --- | --- | --- |
| Missing authentication | denied in `required` and `server` | middleware; `server` will not start with auth off |
| Credential for another corpus | denied, as `404` | `get_corpus_by_name` |
| Accidental omitted corpus predicate | contained by RLS | migration `008`; the predicate returns false with no scope |
| Pooled-connection scope leakage | prevented and tested | transaction-local `set_config`; `test_tenant_connection_isolation.py` |
| Read-only credential attempting a write | denied | `require_corpus(write=True)` |
| Revoked credential | denied on the next request | the lookup excludes disabled and revoked rows |
| Runtime role is a superuser or `BYPASSRLS` | **startup refused** in `server` | `enforce_startup_posture` |
| Runtime role owns protected tables | permitted and reported; `FORCE` closes it | `server_problems` |
| Runtime role can enumerate credential hashes | **startup refused** in `server` | `mindpalace_resolve_credential`; `SELECT` revoked |
| Unknown deployment profile | refused, never reinterpreted | `deployment_mode` |
| Missing or incomplete RLS in `server` | **startup refused** | `posture.server_problems` |
| Malicious normal application request | limited to the principal's grants | every route resolves a corpus through the choke point |
| Runtime role forging the `{*}` scope | **refused** — the sentinel is bound to a role that already reads every row | migration `010`; `session_user`, not `current_user` |
| Another database role calling `mindpalace_erase_corpus` | **refused** — `PUBLIC` holds no `EXECUTE` | migration `010`; granted by name |
| Fully compromised application process | **not contained** for any corpus whose name it can derive | the scope is a session setting; see below |
| Database superuser or DBA | trusted infrastructure boundary; not contained by RLS | `mindpalace_owner` holds `BYPASSRLS` |
| Unauthorized corpus deletion | denied — `403` for the wrong role, `404` for another tenant | `delete_corpus`; `mindpalace_erase_corpus` re-checks the scope |
| Backup copy after live deletion | governed by the operator's backup retention | not removable from here; documented below |
| Receipt or Memory Pack already exported | outside Mind Palace's deletion control | a file on the customer's machine |

### What a compromised application process gets

Stated plainly because it is the limit of the whole design:

* An attacker who controls the application process **and** holds a credential
  with `read_write` on a corpus **can read and write that corpus.** Row-level
  security does not contain this: the process is the thing that sets the tenant
  scope, so a compromised process sets whatever scope it likes. RLS protects
  against a *forgotten predicate*, not against a *malicious one*.
* It can also **forge a scope naming any corpus whose name it knows.** A corpus
  id is `sha256("corpus:" + name)`, so a name is enough to compute one, and the
  row-level predicate accepts any corpus id the process writes into the session.
  Before migration `010` this was worse: the `{*}` sentinel was an ordinary
  session setting, so a compromised process could declare itself unrestricted and
  reach **every** corpus, including erasing them. `010` binds `{*}` to a role
  that already reads every row, so the runtime role can no longer manufacture it
  — but a *named* scope is still forgeable, and that is the residual limit.
* What the privilege model does buy: the process holds no privilege to create
  credentials, read the credential table, `DELETE` archive rows directly, or
  truncate anything. Erasure is the one destructive operation it can still
  reach, and only for a corpus id it can name.
* A process that also holds a `principal_admin` credential, or the maintenance
  database role, is outside this boundary entirely.

**Intentionally cross-tenant administrative operations:** creating a corpus,
granting or revoking a credential, changing table ownership, running
migrations. Each needs the unrestricted identity, which in practice means
database access.

**If that residual matters to you**, the fix is not configuration. It is moving
authorization into the database — a scope the application cannot write, bound to
the credential it presented — which is a different security model and a separate
design decision. Until then, treat a compromised application process as able to
read and write every corpus whose *name* it can obtain, and prefer separate
databases where that is unacceptable.

### Why the scope cannot be made unforgeable cheaply

`mindpalace.tenant_scope` is a custom GUC, and PostgreSQL provides no way to
stop a role from setting its own session settings. Any scheme that lets the
application write the scope therefore lets a compromised application write it.

The schemes that would close this all require the database to know *which
principal* is presenting:

* a per-transaction nonce, checked against a value only the credential lookup
  knows — which means authentication and authorization move into the database;
* a temporary table rather than a GUC, populated by a `SECURITY DEFINER` function
  that re-derives the caller's grants — which means the same thing, plus a
  rewrite of every policy.

Neither is a patch; both are the next data model. This document states the limit
rather than implying the boundary is closer than it is.

### When a shared database is not the right boundary

Shared-database row-level security is the right default for many applications and
the wrong choice for some. Use **a separate database or a separate deployment per
tenant** when any of these are true:

* a regulatory or contractual requirement states that tenants are not co-located;
* the application cannot keep the runtime role's credentials out of reach of the
  rest of its process — a shared credential store defeats the scoping;
* tenants are mutually distrusting *operators*, not merely distrusting users, and
  one of them may be able to reach the other's infrastructure;
* you need erasure from backups to be a provider-to-provider conversation rather
  than a query.

The cost is real — one migration cycle and one credential per tenant — so it is
the exception, not the default.

## Known limitations

* **`off` mode is unsafe on a shared network.** It is the default for local
  single-user use. A deployment that serves more than one user must set
  `required` *and* use the least-privileged database role; one without the other
  is not tenant-safe.
* **Row-level security protects against forgotten predicates, not against a
  compromised application.** An attacker who can execute arbitrary SQL as
  `mindpalace_runtime` can set the tenant scope. RLS is there to make a
  correctness bug harmless, not to contain an application-level compromise.
* **`memory_principals` is not itself policy-protected.** Reading credential
  hashes requires database access, which is already the stronger boundary.
* **The live index is not fully corpus-keyed.** `chunks` inherits its boundary
  through `documents`. A new corpus-free table would need its own policy; the
  migration's table list is the place to change.
* **Deletion is not erasure.** See above. Nothing in this system removes memory
  content from primary storage, and an integrator who needs that must supply it.
* **MCP inherits the same contract, from an environment variable.** An MCP
  process without `MIND_PALACE_MCP_CREDENTIAL` refuses every tool in `required`
  mode. It is not a way around the HTTP boundary, but it is a separate process
  with its own configuration to get right.
* **Receipts and packs are not authorization artefacts.** They establish
  integrity and recorded provenance for something already authorized.

## Rotating and revoking

Rotation is: issue a new credential, deploy it, revoke the old one. Both rows
are retained so the rotation is visible in an audit.

Issuance and revocation are administrative, and an administrative operation must
not be reachable by the grantees it manages — so they are a local CLI, not an
HTTP route. There is deliberately no network endpoint that creates a corpus or
mints a credential; `tests/test_credential_management.py` asserts none exists.

```bash
mindpalace corpus create ws-ws-alpha-brand          # the tenant boundary
mindpalace credential issue service-ws-alpha \
    --corpus ws-ws-alpha-brand --role read_write    # prints the secret once
mindpalace credential list                          # id prefix, role, scope, state
mindpalace credential revoke 5982381f5dd1           # by prefix; takes effect at once
```

The secret is printed exactly once and only its SHA-256 hash is stored. Rotation
is issuing a new one, deploying it, then revoking the old by id — `list` prints
a 12-character prefix and `revoke` accepts a prefix *or the name*, because an
operator working by eye should not have to select 64 characters or remember
which label they used. A reference matching more than one credential is refused
rather than resolved arbitrarily.

The underlying functions are `authz.issue_credential`, `grant_corpora`,
`revoke_corpora`, `revoke_credential` and `list_credentials`.
`list_credentials` returns every credential with its role and corpus scope and
never returns a hash — and it requires the maintenance database role, because
enumerating every credential is exactly what the application role is denied.
Revocation takes effect on the next request: nothing caches a resolved principal
across requests in the HTTP path, and the in-process SDK re-establishes its scope
per operation.

The scope-token secret (`MIND_PALACE_SCOPE_SECRET`, default `dev-scope-secret`)
is shared between the application and the database: the application computes
`HMAC-SHA-256(secret, tenant_scope)` as `mindpalace.scope_token`, and the row-level
policy verifies it against `memory_server_secrets`. Rotate it like a credential —
issue the new value, deploy it to both the application and
`memory_server_secrets`, then revoke the old — because any scope token issued
under the old secret stops verifying the moment the stored secret changes.

## Tenant onboarding and offboarding

Every step is an explicit command. Nothing here requires editing a table, and
nothing happens implicitly.

```bash
# Onboard a customer
mindpalace corpus create acme-brand                     # the boundary
mindpalace credential issue acme-backend --corpus acme-brand --role read_write

# Widen or narrow one credential without changing its secret
mindpalace credential grant  acme-backend --corpus acme-campaign
mindpalace credential ungrant acme-backend --corpus acme-campaign

# Suspend: stop it reaching anything, keep the credential for audit
mindpalace credential ungrant acme-backend --corpus acme-brand

# Offboard: revoke, then erase
mindpalace credential revoke acme-backend
mindpalace corpus delete acme-brand
```

`credential issue` replaces the grant set rather than merging it, so
`--corpus a --corpus b` means exactly those two and an older scope cannot
survive by omission. `grant` and `ungrant` exist because reissuing changes the
secret and breaks every running deployment of the caller; widening access should
not cost a deploy.

Removing the last corpus leaves a credential that resolves to nothing, and
`resolve_principal` refuses it — so `ungrant` alone is a safe suspension, and
`revoke` is the offboarding.

## Deployment modes

Two settings, and the second is what stops the first from being a trap:

| `MIND_PALACE_DEPLOYMENT` | `MIND_PALACE_AUTH_MODE` | Result |
| --- | --- | --- |
| `local` (default) | `off` (default) | The five-minute path. One user, one process, no network. |
| anything else | — | **Refused.** An unrecognised profile is a mistake, not a default. |
| `server` | `required` | Every request presents a credential; the database role is verified. |
| `server` | `off` | **Refuses to start.** |

```bash
MIND_PALACE_DEPLOYMENT=server MIND_PALACE_AUTH_MODE=required \
MIND_PALACE_INGEST_ROOTS=/srv/content \
    uvicorn api.main:app --host 0.0.0.0
```

`local` with `off` is the default because a developer's first five minutes should
not involve credentials. The hazard is that it is also what a bare
`uvicorn api.main:app` and a default `docker compose up` produce, and those bind
a published port. `server` is a promise the code holds the operator to: the
application factory exits with a reason rather than coming up answering anyone.
The mode is never inferred from a socket or a `DATABASE_URL`, because both
heuristics are wrong in both directions — a container on a private network is
still a server, and a laptop is still local.

An **unrecognised** value is refused outright. An earlier version mapped anything
unknown to `server`, which fails closed for authentication but silently skipped
every other server-only check; a typo like `prod` left the ingest-root and
database-posture checks unenforced while the service ran normally.

### What `server` additionally checks before it serves

The environment checks above need no database, so they run at import. The
database posture cannot, so it runs in the application lifespan — after the
process has its configuration and its connection, but **before** uvicorn reports
itself as accepting traffic. That ordering matters: refusing only at
`/health/ready` leaves a window where a load balancer is routing requests to a
service that was never safe.

`server` refuses to start when the connected role:

* is a superuser, or has `BYPASSRLS`;
* can `SET ROLE` into a role that is;
* can `SELECT` `memory_principals` — it could enumerate every tenant's credential
  hashes, which the application has no reason to be able to do;
* holds `DELETE` on any archive table — erasure is meant to be one function, not
  something an ordinary statement can reach;
* can reach a protected table whose row-level security is missing or not forced.

Owning a protected table is **reported, not refused**: `FORCE ROW LEVEL
SECURITY` makes an owner subject to the policies anyway, so ownership alone does
not defeat isolation, and refusing it would break a legitimate single-role
deployment for no security gain.

The check cannot be satisfied by a configuration variable that merely claims it.
`MIND_PALACE_AUTH_MODE=required` is only meaningful because the middleware that
reads the header is unconditionally installed and registered before every route;
a test asserts that wiring rather than trusting the variable.

### What this cannot check

Mind Palace does not know what address an external server binds, and cannot
verify a network boundary it does not own. A reverse proxy in front of it, a
`--host 0.0.0.0` it cannot see, or a `NetworkPolicy` applied elsewhere are all
outside this. `MIND_PALACE_DEPLOYMENT=server` is the operator's statement that
this process is shared, and the code's job is to make that statement *cost
something to get wrong*.

The shipped `docker-compose.yml` sets `server` and `required` and connects as
`mindpalace_runtime`, so the default compose deployment is the safe one rather
than the unsafe one.

The image's own default command binds `127.0.0.1`. It used to bind `0.0.0.0`,
which combined with `MIND_PALACE_AUTH_MODE=off` being the default to mean that a
bare `docker run` published an unauthenticated API to whatever network the
container landed on. Publishing it is now a deliberate act, and the two halves go
together:

```bash
docker run -p 8000:8000 \
  -e MIND_PALACE_DEPLOYMENT=server \
  -e MIND_PALACE_AUTH_MODE=required \
  -e MIND_PALACE_INGEST_ROOTS=/content \
  -e DATABASE_URL=... \
  mindpalace \
  uvicorn api.main:app --host 0.0.0.0 --port 8000
```

`server` refuses to start unless authentication is on and the database role
enforces row-level security, so the bind address and the profile cannot drift
apart unnoticed.

### `MIND_PALACE_INGEST_ROOTS`

`POST /api/ingest/repo` and `POST /api/corpora/{name}/sync` take a path on the
*server's* filesystem and read it. That is an operator capability, and on a
shared service it means a caller can ask the service to walk a directory it was
never given — a narrower exposure than it sounds (`*.md` only, and only what the
caller could already write) but not one a tenant should have.

`MIND_PALACE_INGEST_ROOTS` is a `:`-separated list of directories a client may
name. A path that does not resolve inside one of them is refused before a single
file is read. Unset, it means anywhere — which is what a single-operator local
deployment has always been able to do.

`MIND_PALACE_DEPLOYMENT=server` refuses to start without it, **unless you set it
to `none`**:

```bash
MIND_PALACE_INGEST_ROOTS=/srv/content          # these two routes may read here
MIND_PALACE_INGEST_ROOTS=none                  # these two routes are disabled
```

`none` exists because the requirement as originally written coupled *starting the
service* to *having a filesystem ingestion surface*, and a deployment that only
uses the REST API and the SDK never touches those routes. The risk belongs to the
routes, not to the process, so the control belongs on the routes: with `none`
both refuse with `403` and say why, `POST /api/ingest/file` is unaffected, and
the server starts. Without either setting it still refuses to start.

Resolution is by real path, so `..` and symlinks are followed before the check
rather than being compared as strings. The refusal does not name the allowed
roots, because a caller refused a path should not learn the layout from it.

## Data lifecycle: deletion, erasure, and what survives

Deleting a *source file* is not deleting memory. Deleting a *corpus* is. These
are three different things and the system keeps them apart.

### Deleting a source file

`sync` writes a `DELETED` tombstone version with no content and removes the live
row. **The memory content stays**, in earlier versions, retrievable with
`history` or `as-of`. Re-creating the file produces a `RESTORED` version with new
content — a new version, never the old one. This is deliberate: the archive
records what was true and when, and silently discarding history would make a
receipt meaningless.

### Erasing a corpus

```bash
mindpalace corpus delete acme-brand        # local, needs the maintenance role
DELETE /api/corpora/acme-brand             # for a `manage_corpus` credential
```

The previous behaviour was a `409` whenever the archive referenced the corpus,
which meant a corpus that had ever recorded a fact could never be removed. That
was an honest refusal of an unfinished feature; this is the feature.

**What it removes, in one transaction:** every archive table in dependency order
(`memory_snapshot_seals`, `memory_snapshot_versions`, `memory_snapshots`,
`memory_evidence`, `memory_claims`, `memory_chunks`, `memory_versions`,
`memory_claim_embeddings`, `memory_documents`), the live projection (`chunks`
through its documents, `documents`, `ingestion_manifest`), and every credential's
grant to the corpus. Nothing is left behind on disk — no corpus is ever keyed to a
filesystem path.

**How it is authorised.** Two independent checks:

1. `delete_corpus` resolves the name through `get_corpus_by_name`, so another
   tenant's corpus is a `404` — the refusal does not confirm it exists — and
   `manage_corpus` is required, so a `read_write` credential gets `403`.
2. `mindpalace_erase_corpus` re-reads the same transaction-local tenant scope the
   row-level policies read, and refuses anything outside it. The two cannot
   disagree, and a caller cannot pass a corpus id to bypass the first check.

**Why only one path.** The function is `SECURITY DEFINER`, and the runtime role
holds **no `DELETE` on any archive table** at all. The append-only triggers still
reject an ordinary `DELETE` and still reject `UPDATE` and `TRUNCATE`
unconditionally. So there is exactly one way to erase, it is this one, and a
compromised application role cannot reach the archive even if it decides to try.

**Concurrency.** Erasure takes the same advisory lock, derived identically, that
every writer takes before it inserts, and holds it to the end of the
transaction. A version write that is mid-flight completes first; one that has not
started waits and then finds the corpus gone. There is no window in which a
writer and an eraser interleave.

**What the result says.** A report of table names and row counts, and nothing
else:

```json
{
  "corpus": "acme-brand", "deleted": true, "reason": null,
  "removed": {"memory_claims": 412, "memory_versions": 88, "corpora": 1, "...": 0},
  "not_removed": "receipts and Memory Packs already exported by a customer, database
                  backups and replicas, and any copy the customer stored elsewhere"
}
```

No claim text, no quote, no path — an erasure audit trail that reproduced the
content it erased would defeat the erasure, so there is no field in which to put
one. Erasing an absent corpus returns `deleted: false`, not an error.

### What erasure does not remove

| | Removed by `corpus delete`? |
| --- | --- |
| Server-held live rows and archive rows | yes, in full |
| Credential grants to it | yes |
| A receipt or Memory Pack the customer exported | **no** — it is a file on their machine |
| A database backup or replica | **no** — governed by the operator's retention |
| A copy the customer's own application stored | **no** — outside this system entirely |
| Anything in the application database | **no** — that is the host application's to delete |

Mind Palace cannot remotely erase copies a customer has exported or stored
elsewhere, and does not claim to. **No cryptographic erasure is claimed**: rows
are deleted, not shredded, so a backup taken before the deletion still contains
them until it expires.

Backup retention is therefore an operator decision with a hard limit: a corpus
erased today is still present in any backup taken before today, for as long as
that backup lives. There is no key to destroy and no reaper, because building one
would be a claim this architecture cannot support. If your retention obligation
has a maximum window, set the backup lifetime below it and treat the gap as the
known cost.

Receipts live in caller-owned files, not in the database. Nothing here can
enumerate, revoke or delete them, and a receipt for erased memory keeps
verifying — which is correct for what it is: proof that a document has not
changed, not proof that anyone was entitled to it.

## Evidence

Every claim above is checked by a test that runs on real PostgreSQL with the
least-privileged role, because a policy proved against a superuser proves
nothing.

| File | What it covers |
| --- | --- |
| `tests/test_tenant_isolation.py` | two organisations, three roles each, canary content; every memory operation plus search, context, the query surface, the feed, ingestion, corpus administration, snapshots, replay, receipts; parent/child id mixing; cross-tenant writes; connection reuse; two identities on one client |
| `tests/test_tenant_connection_isolation.py` | one pooled connection serving Alpha → Beta → Alpha, across an exception, a rollback, a cancellation and a protocol error, with concurrent requests interleaved |
| `tests/test_tenant_control_effectiveness.py` | each control removed in turn, asserting what it was holding — including that either layer alone still refuses, and that only removing both opens the path |
| `tests/test_credential_management.py` | the four non-REST surfaces — runtime socket, unnamed SDK client, MCP, read-only credentials — plus the deployment guard and the readiness posture report |
| `tests/test_marketing_reference_integration.py` | the reference integration's claims against a **real Mind Palace on a real socket**, plus the workflow's role matrix; and a mechanical check that no file in the example imports `api`, `cli`, `migrations`, or the SDK — no exception list |
| `tests/test_deployment_profiles_and_lifecycle.py` | the configuration matrix; the runtime role's real privileges; erasure authorization, completeness, idempotency, neighbour-intactness and report content; permission granularity |
| `tests/test_operator_cli.py` | `mindpalace corpus` and `mindpalace credential` through their real entry point, including the issuance → grant → revoke chain |
| `tests/test_scope_forgery.py` | the `{*}` sentinel is not self-assertable; erasure is not reachable by forging a scope; `PUBLIC` holds no `EXECUTE` on the `SECURITY DEFINER` functions; and the residual named-scope forge is *pinned*, so a change in either direction is noticed |
| `tests/test_error_and_log_hygiene.py` | PostgreSQL's constraint `DETAIL` carries the row; an unhandled error therefore returns and logs neither the row nor the driver stack; `/health/ready` and `/health/live` expose no role name, connection string or capability inventory |

CI runs the eight as a separate step, so a leak cannot hide inside a green run,
and runs the example standalone against a clean checkout.

### The tests provision from the shipped script

`scripts/db/runtime_roles.sql` is not restated in the test harness. The harness
reads that file at import, resolves its two psql variables, and runs it — so the
least-privileged role every isolation assertion is made against is byte-for-byte
the role an operator gets from the deployment guide.

It used to be a hand-maintained copy, and the drift was not subtle: the copy was
correct and the shipped script had an unbalanced parenthesis in its
`REVOKE EXECUTE ... FROM PUBLIC` lines. Every test passed, because the tests were
provisioning a different role from the one being deployed. Deriving instead of
duplicating is what makes that class of defect impossible to hide.

### What a failure is allowed to say

Worth stating separately, because it was not obvious from the code that wrote it.

**PostgreSQL puts the offending row in the error detail.** Every constraint
violation returns

```
DETAIL:  Failing row contains (..., "a private fact about someone's appointment", ...)
```

and asyncpg passes that string through verbatim. SQLAlchemy's `hide_parameters`
does not help — it suppresses *bound parameters* in the rendered SQL, and this
text arrives from the server, not from the statement. Any traceback that prints
the exception writes memory content to the log.

Two changes close it. An ASGI middleware (`RedactFailures`) catches an unhandled
error, logs `method`, `path` and the exception **type**, and returns
`{"detail": {"code": "internal_error", ...}}`. A registered
`@app.exception_handler(Exception)` would *not* have been enough: Starlette's
`ServerErrorMiddleware` sends the handler's response and then re-raises anyway,
explicitly so the server can log it. Separately, the three embedding-cache
failure logs dropped `exc_info=True` for the same reason.

Two consequences, both accepted:

* **A full traceback no longer reaches the server log on a 500.** Debugging means
  reproducing the failure, where `echo=True` and a debugger still show
  everything. That is the right way round for a system whose purpose is storing
  what people told it in confidence.
* **A response that has already started is not covered.** Every response this
  service produces is built before it starts, so the path is unreachable today;
  it is stated rather than assumed away.

The middleware sits inside CORS and the Prometheus wrapper and outside everything
else, so the guarantee is exactly: errors raised while authenticating a request
or serving one are redacted. An exception from those two outer layers would still
reach the log, and they carry no request data.

### RLS coverage

Every tenant-bearing table, its scope source, and whether it fails closed. A
table absent from this list is either not tenant-bearing or a gap.

| Table | Scope source | Fails closed with no scope |
| --- | --- | --- |
| `corpora` | its own `id` | yes |
| `documents`, `ingestion_manifest` | `corpus_id` | yes |
| `chunks` | through `documents` | yes |
| `memory_documents`, `memory_versions`, `memory_chunks`, `memory_claims`, `memory_evidence` | `corpus_id` | yes |
| `memory_snapshots`, `memory_snapshot_versions`, `memory_snapshot_seals`, `memory_claim_embeddings` | `corpus_id` | yes |
| `memory_principals`, `memory_principal_corpora` | **no policy, by design** | see below |

The credential tables carry no policy deliberately. The authentication lookup
runs *before* any tenant scope exists — that is what it is for — so a policy
keyed on that scope would make authentication depend on the context it exists to
decide. They are unreachable by ordinary queries instead: `SELECT` is revoked
from the runtime role and authentication goes through a `SECURITY DEFINER`
function that returns one identity and its grants. Reading the hashes requires
the maintenance role, which is outside the boundary already.

### Measured cost of the security work

Not guessed, and not optimised away:

| Check | Cost | Note |
| --- | --- | --- |
| `resolve_principal` | one round trip | was two; the identity and its grants now arrive together |
| `require_corpus` | one indexed lookup by name | unchanged |
| transaction scope | one `set_config` per transaction | unchanged; it is a session GUC, not a query |
| RLS | one predicate call per row | unchanged |
| the `{*}` binding (`010`) | one string comparison per evaluated row | see below |
| erasure | one advisory lock + 14 statements | new, and only on an explicit administrative request |

The one change to a hot path is `resolve_principal`, which got cheaper.

### What `010` costs

Worth measuring rather than asserting, because binding the sentinel could plausibly
have been expensive: it adds a `session_user` read, a `pg_roles` lookup and a
catalog read per evaluated row.

Ten runs each, `SELECT count(*)` over 196 rows as the least-privileged role with
a scoped corpus:

| Predicate | Median | Min | Max |
| --- | --- | --- | --- |
| `009` | 0.997 ms | 0.941 ms | 1.378 ms |
| `010` | 1.007 ms | 0.933 ms | 1.369 ms |

No measurable difference, and the reason is structural rather than lucky: the new
check is `(scope = '{*}' AND <role check>)`, so on the normal path — a scoped
value — the role check is never reached at all. It costs one extra string
comparison per row. The role check is only evaluated when the sentinel is
present, and in that case the predicate returns `TRUE` on the same row anyway.

## Integrating: the recommended path

One path works for every mode above — local CLI, REST, Python SDK over HTTP, a
FastAPI application, a background worker, MCP.

```text
browser / client
      ↓
the host application's own authentication       ← not Mind Palace's job
      ↓
the host application's membership check          ← server-side rows it owns
      ↓
Mind Palace client, with a credential scoped to that workspace's corpora
      ↓
Mind Palace's authorization choke point
      ↓
row-level security, under the least-privileged runtime role
      ↓
tenant-scoped rows
```

Three rules that hold for all of them:

* **The browser never receives a Mind Palace credential** or a database URL.
  Tokens issued to a browser are the host application's own, expiring, and scoped
  to the host application's sessions.
* **A `tenant_id`, `workspace_id` or corpus name in a validated JSON body is not
  authority.** Those fields identify a requested resource. The caller's authority
  comes from the credential the trusted backend presents.
* **The mapping is the application's.** `ws-<workspace>-brand` is a naming
  convention the host application applies to its own records; Mind Palace only
  ever sees the corpus name that results, and checks it against the credential.

A working example of all of this — two tenants, one deliberately shared
workspace, draft/review/approval, cross-tenant refusal — is in
[`examples/marketing-campaign-memory/`](../examples/marketing-campaign-memory/).
It uses **only** the public HTTP contract: `httpx`, a bearer credential and a
JSON body, with no import of `api.services`, no repository, and no memory table.
A test fails if any file under that directory imports an internal, because the
claim is worthless if the example quietly depends on the implementation.

## What is tested, and what is not

Tested on real PostgreSQL with the least-privileged role: two organisations,
three roles each, canary content in brand and campaign memory, every memory
operation, search, context, the query surface, the feed, ingestion, corpus
administration, receipts, snapshots and replay, parent/child id mixing,
cross-tenant writes, connection reuse, concurrency, cancellation and rollback.

Tested on every surface, not just HTTP: the runtime socket, the local SDK with
and without a corpus name, and MCP. Each was a real bypass before it was a test.

Tested against the real role rather than the provisioning script: the runtime
role's actual privileges, the erasure path's authorization and completeness, and
the administrative CLI driven through its own entry point.

Not tested: behaviour under a real network partition, a rogue database
administrator, or an application process that has been fully compromised. Those
are outside the boundary described above rather than gaps in it.
---

## Resource-level authorization: not shipped

Corpus-level isolation answers "may this caller see this corpus?". It does not
answer "may this caller see *this document*, when the corpus is shared?" Two
principals holding the same corpus grant hold the same facts, with no way to keep
something private.

That gap is real and it is **not closed**. A migration implementing
per-document ownership — inheriting to versions, claims, evidence, chunks,
embeddings, snapshot membership and seals — was written, applied, and proved by an
adversarial suite with working negative controls. It was then withdrawn because it
made `POST /api/memory/remember` return `503` for a write that has always
succeeded, and the cause was not diagnosed to a conclusion.

The design, the three real findings it produced, the tests, and the steps to
reinstate it are in [`docs/withdrawn/`](withdrawn/README.md).

**What this means for you:** do not assume any same-corpus per-user isolation.
Two principals holding the same corpus hold the same facts. Use a separate corpus
per confidentiality boundary.

## Every caller-supplied file path

`MIND_PALACE_INGEST_ROOTS` is the deployment policy for filesystem reads. One
resolver enforces it, `api/services.ingest_paths.require_permitted`, which
resolves the candidate **before** comparing it, so a symlink that leaves a root
and a `..` that climbs out of one are both caught by the same check.

A resolver nothing calls is not a control. This is every input that reaches the
filesystem from a caller, and whether it goes through that one resolver.

| Surface | Reads | Enforced | Through `require_permitted` |
|---|---|---|---|
| `mindpalace ingest-repo <path>` | a directory of Markdown | yes | yes — `cli/main.py:1212` |
| `mindpalace ingest <path>` | one file, then POSTs its contents | yes | yes — `cli/main.py:1143` |
| `mindpalace remember --file <path>` | one file, as a statement | yes | yes — `api/services/remember.py:563` |
| `mindpalace reindex` | no caller path; the corpus's own documents | n/a | n/a |
| `POST /api/ingest/repo` | a directory | yes | yes — `api/routers/ingest.py:76` |
| `POST /api/ingest/file` | content in the request body, no path | n/a | n/a |
| `POST /api/corpora/{name}/sync` | a directory | yes | yes — `api/routers/corpora.py:115` |
| SDK `MindPalace.sync(path)` | a directory | yes | yes — `api/services/ingestion.py:694` |
| SDK `MindPalace.remember(file=…)` | one file | yes | yes — `api/services/remember.py:563` |
| MCP `sync(corpus, path)` | a directory | yes | yes — same `sync_repo` |
| MCP `memory_remember(file=…)` | one file | yes | yes — `mcp_server.py:262` |
| `mindpalace verify <receipt>` | a receipt the caller produced | **no, deliberately** | n/a |
| `mindpalace receipt -o <file>` | writes; not a read | n/a | n/a |
| benchmark `--baseline <file>` | a previous report, for comparison | **no, deliberately** | n/a |
| pack import | a pack file in the request body, no path | n/a | n/a |
| local runtime socket | no caller-supplied path is accepted | n/a | n/a |

The two rows marked *deliberately* are the ones that must stay unbounded.
`verify` is documented as working on a machine that has never heard of Mind
Palace, so bounding it to the ingest roots of some other deployment would defeat
it; a receipt is caller-supplied evidence, not deployment content. A benchmark
baseline is a report the caller produced.

**Three** surfaces were found reading without the resolver, and all three were
fixed:

1. `remember --file` — the service read any path.
2. `mindpalace ingest <path>` — a bare `open()` *before* its HTTP call, so the
   read happened whether or not a server was listening.
3. `sync_repo`, reached directly by `MindPalace.sync()` and by the MCP `sync`
   tool. The CLI and the two routes each checked the path themselves, which is
   how the audit table above came to list the SDK and MCP rows as "yes, via the
   service" when the service did not check at all. An independent review found
   it; the table was wrong, not just incomplete.

Each fix went in at the chokepoint rather than at the callers, because callers
are where the gaps came from.

## Write operations and the shared core

One table decides which memory operations write:
`api/services/memory_public.WRITE_OPERATIONS`. The REST adapter and the MCP
server each carried their own copy of that list and applied it themselves, so
the SDK and the local runtime — which call the shared core directly — resolved
every corpus as a reader. A `read_only` credential could therefore create a
snapshot through the SDK while the identical REST call returned **403 "This
credential is read-only"**. The shared core now applies the same flag, so the
answer cannot depend on which door the caller came through.

## Recorded, not fixed

Three findings that are real and are not exploitable today. Each is written down
because a finding nobody can reproduce is a rumour, and a rumour that turns out
to be load-bearing later is the expensive case.

**`delete_corpus` resolves its corpus with `write=False`.** Every other write
path passes `write=True` so a `read_only` credential is refused. This one does
not, and would hand the same class of answer the shared core used to. It is not
reachable: `manage_corpus` is in `authz.PERMISSIONS` for `corpus_admin` and
`principal_admin` only, so `read_only` and `read_write` are refused before the
resolution is used. If `delete_corpus` ever moves behind a weaker role, the
inconsistency becomes a bypass the day it does — hence written down here and not
fixed, since a fix would be a permission the read roles still do not have.

**`mindpalace eval retrieval --file` is unconfined.** It reads the benchmark
file with a bare `open()`, outside `MIND_PALACE_INGEST_ROOTS`. It is an operator
command that takes an operator-supplied benchmark definition, in the same class
as the documented `--baseline` exception above, and it reads no corpus content.
Bounded, it would break the documented way to point the evaluator at a
benchmark set kept outside the deployment.

**A warm local runtime did not know which database it was attached to.** The
handshake carried `protocol`, `build` and `token`; nothing said which
`DATABASE_URL` the running process was holding a pool to, so a command run later
against a different database was answered — confidently, from the wrong archive.
Fixed: the handshake now carries a digest of scheme, host, port, database and
user (never the password), and a runtime that disagrees is discarded and
replaced, exactly as one built from stale code already was.
