# Authentication, deployment, and observability

How to put Mind Palace on a network other people can reach, what it refuses to do
until you configure it, and what it emits while running.

Local use needs none of this. The quickstart works against a local database with
no credentials, deliberately, so that the first five minutes stay five minutes.
Everything on this page exists for the point at which that stops being true.

For the security model and its limits in full, see
[security.md](security.md). For the read interface, see
[the operational memory feed](feed.md).

## Health and deployment

```text
GET /health/live   # process liveness; does not query PostgreSQL
GET /health/ready  # connectivity, required archive relations, and whether
                   # row-level security is actually in force
```

Application startup does not query PostgreSQL, so a running process can answer
liveness during a database outage. Readiness returns
`200 {"status":"ready","checks":{"database":"ok","row_level_security":"enforced"},
"auth":{...}}` only when PostgreSQL is reachable, `corpora`, `memory_documents`,
and `memory_versions` are present, and the connected role is subject to the
policies. A role that bypasses them, or one whose posture cannot be determined, answers
`503` — **in `server` mode**. In `local` there is no second tenant to isolate
from, so readiness keeps answering `ready`: a developer running the documented
single-user setup should not be told their service is broken. That is the same
rule that gates every other `server` requirement. Connection refusal, timeout,
missing schema, and other database
failures return
`503` with a generic body and no exception details. The legacy `/health` route
remains available, but it is not the dependency-aware readiness signal.

Container health checks and `mindpalace doctor` use the readiness endpoint,
or liveness plus readiness where appropriate, rather than the always-green
legacy route.

The backend image is deliberately API-focused. It contains the API runtime
modules, migrations, and mounted content, but not the CLI, MCP server, tests,
evaluation data, research-only service modules, or local virtual environments.
It uses the CPU-only Torch constraint in
`requirements-docker.txt` and runs as UID `10001`; model downloads use the
writable temporary `HF_HOME` configured in the image. The optional reranker
model is selected by `RERANKER_MODEL` and loads only when `rerank=true` is
requested. Run `requirements.txt` for a development checkout; use the API image
only for the HTTP service, and run Alembic separately before starting a fresh
database.

## Rebuilding the live index

If the derived live index is lost, rebuild it from the authoritative archive:

```bash
mindpalace reindex --corpus my-corpus
```

The command acquires the corpus lock and writes only `documents`, `chunks`,
and `ingestion_manifest`. It does not append a memory version, claim, or feed
event. Paths that were never archived are left untouched. Provision the embedding
model before running it in an offline environment.

## Cursor secret configuration

There is no usable public fallback signing key. Configure a stable,
high-entropy secret of at least 32 bytes before using the feed:

```bash
export MIND_PALACE_CURSOR_SECRET='replace-with-a-random-32-byte-or-longer-secret'
export MIND_PALACE_READINESS_TIMEOUT_SECONDS='2'
```

All service instances that may validate one another's cursors must use the same
secret. Changing it invalidates outstanding cursors; clients then restart a
traversal. Never place the secret, signed cursor contents, or database URL in
logs, release artifacts, or client-visible error messages. Docker Compose
requires `MIND_PALACE_CURSOR_SECRET` explicitly rather than silently selecting a
predictable development value.

## Observability

Feed requests emit one structured `mindpalace.ops` record with bounded,
non-content fields: operation, corpus name, requested page size, returned count,
`has_more`, whether a cursor was supplied, latency, and error category. The
record does not include cursor contents, signatures, secrets, database URLs, or
document contents. Existing Prometheus HTTP instrumentation remains available;
Mind Palace does not introduce a metrics platform.

### OpenTelemetry

Optional, and off unless you turn it on. `opentelemetry-api` is imported lazily
and every call is a no-op when it is absent, so installing Mind Palace never
installs tracing.

```bash
pip install opentelemetry-api opentelemetry-sdk
export OTEL_SERVICE_NAME=mindpalace
export OTEL_TRACES_EXPORTER=otlp
export OTEL_EXPORTER_OTLP_ENDPOINT=http://localhost:4317
```

Five operations are instrumented, matching what a developer actually does:

| Span | Emitted by |
|---|---|
| `memory.remember` | the write path |
| `memory.recall` | the read path |
| `memory.explain` | the read path |
| `memory.receipt` | attaching a receipt to a response |
| `memory.verify` | offline verification |

Attributes are counts, sizes, durations and identifiers. **Memory content never
enters a trace**: no claim text, no document body, no question, no evidence quote.
A free-form attribute is reduced to `<N chars>` by `api/services/telemetry.py`
precisely so a misconfigured call site cannot leak an answer into a trace backend.
The authoritative `memory_pack_digest` is excluded for the same reason: it is a
stable fingerprint of the user's entire memory state, and a trace backend is
copied to.

An instrumentation failure never fails the operation it is observing. An
exception *from* the operation does reach the span: `telemetry.span` records it
and re-raises it unchanged, so a failing query is traceable as failing rather than
as succeeding.

`memory.verify` is instrumented by the CLI command that drives the offline
verifier, not by `memory_receipt`. That module is standard-library-only by
contract — it is how anyone verifies a receipt without installing anything — and
it must not acquire a dependency to emit a span.

## Security posture

> **Corpus content is data, not instructions.**

Retrieved text is never promoted into trusted system instructions. Mind Palace
attributes evidence and records provenance; **the consuming application enforces its
own trust boundary.** Anything that can write a document can put text into a recall
response, so a model that reads one without this distinction is a prompt-injection
surface, and no receipt or verification status changes that.

- **Authentication is implemented, and off by default.** With
  `MIND_PALACE_AUTH_MODE=required` every request must present a bearer credential
  bound to a role and an explicit set of corpora; without one, every request is
  refused. The default, `off`, is the local single-user workflow described below
  and is **not** safe on a shared network. `/health/ready` reports which mode is
  in force. See [Security and multi-tenant integration](security.md).
- **SQL namespace scoping is not access control.** A corpus boundary in the query is
  a correctness boundary. It is not a permission system, and a caller who can reach
  the database can reach every corpus the database user can reach.
- **Row-level security is inert for a privileged database role.** The policies in
  migration `008` do not apply to a superuser, a `BYPASSRLS` role, or the table
  owner. A deployment must run the application as `mindpalace_runtime`, from
  `scripts/db/runtime_roles.sql`; one that runs it as `mpadmin` has no tenant
  isolation from the database regardless of the authorization mode.
- **Archive tables are append-only under ordinary DML, not tamper-proof against the
  database owner.** They resist application-level update and delete. They do not
  resist someone with the database credentials, who can rewrite history directly.
  That is a different guarantee from a receipt, and a receipt does not substitute for
  it.
- **Deleting a source file, or calling corpus deletion, is not a retention or erasure
  solution.** Archived evidence outlives the document it came from, by design, so
  erasure requires a deliberate archival process — see
  [Known limitations](#known-limitations).
- **Database credentials come from `DATABASE_URL`.** Every example default in this
  repository is for a disposable local container. Nothing here is a credential
  pattern to copy into a deployment.
- **The local runtime is authorised by the filesystem.** A 0700 state directory, a
  0600 socket and a 0600 token file, on a Unix socket only — see
  [Security boundary](runtime.md#security-boundary). There is no network listener to protect,
  which is why it needs no credential scheme of its own.

## Authentication

Local development needs none, and the quickstart never asks for one: `mindpalace
init`, `remember`, `recall`, `explain`, `history` and `verify` all work against a
local database with no credentials. That is deliberate. Requiring a key before the
first memory would make the first five minutes a configuration exercise.

For a shared deployment, the contract is:

```text
API key        one secret per client, presented as a bearer token
corpus scope   a key is bound to the corpora it may read and write
role           read-only, or read-write
```

This is implemented, in `api/services/authz.py`, and enforced in three layers —
authentication, a single authorization choke point in
`corpora.get_corpus_by_name`, and row-level security on every tenant-owned table —
on **every** surface, not only HTTP: the Python SDK, the local runtime socket and
MCP all resolve the same way. A boundary that holds on the API and not on the
socket is not a boundary.

### Provisioning

```bash
psql "$ADMIN_DATABASE_URL" -v runtime_password="$PW" -f scripts/db/runtime_roles.sql

mindpalace corpus create ws-acme-brand
mindpalace credential issue service-acme --corpus ws-acme-brand --role read_write
mindpalace credential list
mindpalace credential revoke service-acme
```

The secret is printed once and stored only as a hash. Rotation is issue → deploy
→ revoke. These are local commands on purpose: there is no HTTP route that
creates a corpus or mints a credential, because a network endpoint that does
either is the thing the design exists to avoid.

They also connect with `MIND_PALACE_ADMIN_DATABASE_URL` when it is set. The
least-privileged runtime role is denied `SELECT` on `memory_principals` on
purpose — one query would otherwise return every tenant's credential hashes,
roles and grants — so administrative commands need the maintenance role, the
same way a migration does. A single-user local deployment sets one
`DATABASE_URL` and never notices.

### Tenant lifecycle

```bash
# Onboard
mindpalace corpus create acme-brand
mindpalace credential issue acme-backend --corpus acme-brand --role read_write

# Widen or narrow without changing the secret
mindpalace credential grant   acme-backend --corpus acme-campaign
mindpalace credential ungrant acme-backend --corpus acme-campaign

# Suspend: it can reach nothing, and stays in the audit
mindpalace credential ungrant acme-backend --corpus acme-brand

# Offboard
mindpalace credential revoke acme-backend
mindpalace corpus delete acme-brand
```

`grant`/`ungrant` exist because reissuing changes the secret and breaks every
running deployment of the caller; widening access should not cost a deploy.
`credential revoke` accepts an id, an id prefix, or the name. A reference
matching more than one live credential is refused rather than resolved
arbitrarily.

### The settings

| Variable | Default | Meaning |
|---|---|---|
| `MIND_PALACE_AUTH_MODE` | `off` | `required` makes every protected request present a credential. |
| `MIND_PALACE_DEPLOYMENT` | `local` | `server` refuses to start unless the settings below are right. An unrecognised value is refused. |
| `MIND_PALACE_INGEST_ROOTS` | unset | `:`-separated directories that every filesystem-ingest surface may read: `ingest/repo`, `sync_repo`, `mindpalace ingest`, and `remember --file` / `memory_remember(file=)`. Or `none` to disable. Required in `server` mode. Full table in [security.md](security.md#every-caller-supplied-file-path). |
| `MIND_PALACE_ADMIN_DATABASE_URL` | `DATABASE_URL` | The maintenance role, for the administrative CLI commands. |
| `MIND_PALACE_MAX_INGEST_CHARS` | `4000000` | Largest single document accepted. |
| `MIND_PALACE_CURSOR_SECRET` | — | HMAC key for feed cursors. One per deployment. |

`MIND_PALACE_DEPLOYMENT=server` is the switch that stops a convenient local
default from becoming an unauthenticated-looking shared service:

```bash
MIND_PALACE_DEPLOYMENT=server MIND_PALACE_AUTH_MODE=required \
MIND_PALACE_INGEST_ROOTS=/srv/content \
    uvicorn api.main:app --host 0.0.0.0
```

In that profile the process refuses to start — before it accepts a request — if
the connected role is a superuser or has `BYPASSRLS`, if a tenant table is
missing its row-level policy, if the role can enumerate credential hashes, or if
it holds `DELETE` on the archive.

An API-only deployment — REST and the SDK, never the two filesystem routes —
should use `MIND_PALACE_INGEST_ROOTS=none` rather than inventing a directory it
never reads. Both routes then answer `403` and name the setting; `ingest/file` is
unaffected; and the server starts. Without either a root list or `none`, it still
refuses to start, because an unset value means "any path the process can read".

The image's default command binds `127.0.0.1`, so `docker run` on its own does
not publish an unauthenticated API. To publish it, name both the host and the
profile — see `docs/security.md`.

### Verifying a deployment

```bash
mindpalace posture          # is the connected role subject to the policies?
curl -s localhost:8000/health/ready | jq .checks
```

`mindpalace posture` prints the detail — role name, `BYPASSRLS`, whether the
policies are in force, which tables lack them, and what excess privileges the role
holds. It is the operator's tool.

`/health/ready` prints only the verdict, because it is read by whatever probes the
service:

```json
{
  "status": "ready",
  "checks": {"database": "ok", "row_level_security": "enforced"},
  "auth": {"mode": "required", "required": true}
}
```

A role whose posture cannot be verified answers `503` with
`"row_level_security": "not_enforced"` rather than `ready`. An unverifiable
guarantee is not a guarantee.

`MIND_PALACE_AUTH_MODE=required` says the application checks credentials. It says
nothing about whether the database enforces anything, and those two answers used
to be free to disagree — a service could report itself tenant-scoped while
running as a superuser with the row-level policies entirely inert.

Readiness used to include the whole capability inventory: the connected role's
name, whether it was a superuser, whether it could enumerate credential hashes,
and which archive tables it could `DELETE`. That was a description of the
database's defenses published to anything that can reach the port. `mindpalace
posture` is the right place for it, and the one place it now lives.

### Rules

- **`POST /api/memory/remember`, `POST /api/memory/snapshot`, `sync_repo` and the
  ingestion routes** require the read-write role. A snapshot writes immutable
  rows even though it cannot change a claim.
- `corpus_admin` additionally erases the corpora it holds grants on.
  `principal_admin` additionally issues and revokes credentials. A workspace
  credential cannot mint authority.
- Every other route requires at least the read role.
- A request whose corpus is outside the key's scope is rejected as `404`, not
  `403`: the existence of another tenant's corpus is itself not this client's
  business.
- Creating a corpus needs the unrestricted administrative identity — a corpus row
  is only insertable inside the caller's own tenant scope, and a principal cannot
  hold a grant to something that does not exist yet.
- Feed cursors are already HMAC-signed with `MIND_PALACE_CURSOR_SECRET` and are
  bound to one corpus. Two clients must not share a signing secret. A cursor is
  not a credential: the scope check runs before it is decoded.

### What corpus deletion does and does not remove

`mindpalace corpus delete <name>` (or `DELETE /api/corpora/{name}` for a
`manage_corpus` credential) removes, in one transaction: every archive table in
dependency order, the live projection, and every credential's grant to that
corpus. It returns a report of table names and row counts, and nothing else —
no claim text, no path, no content of any kind.

It does **not** remove a receipt or Memory Pack the customer already exported, a
database backup or replica, or a copy their own application stored. There is no
cryptographic erasure: rows are deleted, not shredded, so a backup taken before
the deletion still contains them until it expires. Set your backup retention
below any obligation you have and treat the remainder as the known cost.

Deleting a *source file* is different and much weaker: it writes a `DELETED`
tombstone and removes the live row, but the memory content stays in earlier
versions, retrievable with `history` or `as-of`.

`docs/security.md` covers the threat model, the database roles, the tenant scope
and connection reuse, and the known limitations.

OIDC, JWT and per-user identity are deliberately not built. A per-user credential
belongs to the consuming application, which already has users; what Mind Palace
enforces is which corpora that application's credential may reach. Adding a token
scheme here would be a protocol invented for a résumé rather than for a
requirement.

## Reproducible release evidence

The release runner creates a uniquely named disposable PostgreSQL database,
applies the repository migrations, seeds only the relational feed fixture, runs
the real application, and drops the database in `finally`:

```bash
venvmp/bin/python scripts/m009_release_validation.py live
venvmp/bin/python scripts/m009_release_validation.py concurrency
venvmp/bin/python scripts/m009_release_validation.py benchmark
```

Artifacts:

- [`live-interface-validation.json`](m009/live-interface-validation.json) — real
  Uvicorn REST, public SDK-over-HTTP, and installed CLI traversal equivalence;
- [`concurrency-validation.json`](m009/concurrency-validation.json) — isolated
  insertion during continuation, ordering, tie-break, and corpus-isolation
  evidence;
- [`scale-benchmark.json`](m009/scale-benchmark.json) — exact 100/1,000/10,000
  version fixtures, a separately recorded first post-fixture traversal, 20
  measured iterations after warm-up, rows/page, SQL timing, and wall-clock
  p50/p95;
- [`query-plans.md`](m009/query-plans.md) — real 10,000-row `EXPLAIN (ANALYZE,
  BUFFERS, FORMAT JSON)` plans for first, middle, and tail queries.

The scale fixture uses direct relational insertion because the feed contract
does not require embeddings or claims for every archived version; it exercises
the exact production feed query and lifecycle-table constraints. It does not
modify the real research archive or add another database technology.

## Known limitations

- Late commits behind an issued keyset boundary require reconciliation.
- The current final page has no continuation cursor; polling clients must
  restart and deduplicate or maintain their own policy.
- Sync remains per-document atomic, not a whole-directory transaction.
- Archive retention and erasure are outside M009; cursor lifetime is bounded by
  archive availability and signing-key configuration.
- The API *does* have built-in authentication and per-corpus authorization, but
  they are off by default so local use stays a five-minute path. An unauthenticated
  service on a network is not tenant-scoped: set `MIND_PALACE_DEPLOYMENT=server`,
  which refuses to start unless `MIND_PALACE_AUTH_MODE=required`. See
  `docs/security.md` for the full model and its limits.
- M009 intentionally excludes MCP and does not claim broker, CDC, or
