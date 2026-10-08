# M009 operational memory feed

M009 provides a durable, corpus-scoped read interface over immutable
`memory_versions` rows. It is intended for synchronization and reconciliation
clients, not for message-broker workloads.

## Release contract

### Ordering

Rows are strictly ordered by:

```text
(observed_at ASC, version_id ASC)
```

`version_id` is the deterministic tie-breaker. The feed emits archive versions
with lifecycle events `NEW`, `MODIFIED`, `DELETED`, and `RESTORED`; an unchanged
ingestion does not create a version and therefore does not create a feed item.

### Cursor

A cursor is an opaque, URL-safe token containing an HMAC-SHA256-signed, versioned
boundary. Clients must treat it as opaque. It is not encrypted: the signed
boundary is not confidential.

A valid cursor is:

- integrity-protected and rejected when its signature is changed;
- bound to one corpus and rejected for another corpus;
- independent of process memory and usable across database sessions and service
  restarts when the same signing secret is configured;
- versioned and bound to the `memory_versions` feed, so a cursor for an
  incompatible endpoint/payload is rejected;
- restricted to a bounded length and strict base64/JSON/types.

The first request omits `cursor`. A page with `has_more=true` returns
`next_cursor`; pass it unchanged to the next request. A final page currently has
`has_more=false` and `next_cursor=null`. Therefore a client that reaches the
current tail and wants to poll later must start a new traversal and deduplicate
against its previously observed version IDs, or retain a separately obtained
boundary according to its application policy. M009 does not invent a hidden
process-local tail cursor.

### Pagination

The service uses a parameterized keyset predicate:

```sql
(v.observed_at, v.id) > (:observed_at, :version_id)
```

It requests `page_size + 1` rows, trims the extra row after determining
`has_more`, and never uses `OFFSET`, Python-side history loading, or a
process-local cursor store. Valid page sizes are 1 through 500; the default is
50.

### Consistency under concurrent writes

Each request is a PostgreSQL statement/MVCC read. Successive requests may see
successive committed states; M009 does not hold one snapshot across independent
HTTP requests.

During an active traversal, a committed row whose `(observed_at, version_id)`
is strictly after the issued boundary can be returned in a later page. Equal
timestamps are ordered by `version_id`. A row committed after a cursor was
issued but whose observation key is at or before that cursor is not returned by
that continuation; clients needing a complete late-arrival guarantee must
perform periodic full reconciliation. This is the measured M009 guarantee, not
an exactly-once or broker guarantee.

M009 does **not** guarantee:

- exactly-once delivery or processing;
- Kafka, CDC, or message-broker semantics;
- transactional event delivery;
- a globally consistent snapshot across independently requested pages;
- absence of late commits behind an already-issued keyset boundary.

## Interfaces

All three public interfaces use the same service query and `FeedResponse`
contract. Incidental JSON whitespace is not part of the contract; clients should
compare typed values or the SDK model.

### REST

```text
GET /api/memory/feed?corpus=project&page_size=50&cursor=<opaque>
```

`corpus` is required and uses the existing 1–128 character corpus-name rules.
`page_size` is an integer from 1 through 500. A successful response has this
shape:

```json
{
  "schema_version": 1,
  "corpus": "project",
  "items": [
    {
      "version_id": "opaque-version-id",
      "document_id": "document-id",
      "corpus": "project",
      "path": "docs/decision.md",
      "version_number": 1,
      "status": "NEW",
      "observed_at": "2026-01-01T00:00:00Z",
      "predecessor_id": null
    }
  ],
  "has_more": true,
  "next_cursor": "<opaque>",
  "page_size": 50
}
```

`document_id` is the authored/source document identifier when present and falls
back to the stable archive document identity for legacy rows. The
`memory_document_id` relationship remains internal to the service.

Invalid requests and cursors return `422`; a missing corpus returns `404`;
database, schema, and transport failures return a sanitized `503`. Error bodies
use:

```json
{"detail":{"code":"invalid_cursor","message":"Invalid change-feed cursor"}}
```

They do not include SQL, credentials, connection strings, tracebacks, or
filesystem paths.

### Python SDK

```python
from mindpalace_sdk import MindPalace

client = MindPalace(base_url="http://127.0.0.1:8000")
page = client.memory.feed(corpus="project", page_size=50)
while page.has_more:
    page = client.memory.feed(
        corpus="project", page_size=50, cursor=page.next_cursor
    )
```

Remote SDK errors preserve the server's `code`, `message`, and HTTP status.
Timeouts, transport failures, and invalid successful responses use the SDK's
`timeout`, `transport_error`, and `invalid_response` codes respectively.

### CLI

```bash
mindpalace memory feed --corpus project --page-size 50
mindpalace memory feed --corpus project --page-size 50 --cursor "$CURSOR"
```

The CLI prints the compact canonical `FeedResponse` JSON and exits nonzero for a
remote service error. `--base-url` defaults to `http://127.0.0.1:8000`.

## MCP decision: deliberately excluded (Option B)

M009 does **not** add `memory_feed` to MCP. The existing MCP tools are ordinary
memory operations over the `MemoryRequest`/`MemoryResponse` contract. The feed
has a different cursor, page-size, error, and operational synchronization
contract. Exposing it would create a second MCP feed schema and would broaden
an unauthenticated tool surface without a demonstrated agent use case.

REST, SDK, and CLI are the supported M009 feed interfaces. A future MCP feed
would require a separately versioned contract, authorization review, telemetry,
and parity tests; it is not an unfinished M009 implementation.

## Health and deployment

```text
GET /health/live   # process liveness; does not query PostgreSQL
GET /health/ready  # connectivity plus required archive relations
```

Application startup does not query PostgreSQL, so a running process can answer
liveness during a database outage. Readiness returns
`200 {"status":"ready","checks":{"database":"ok"}}` only when PostgreSQL is
reachable and `corpora`, `memory_documents`, and `memory_versions` are present.
Connection refusal, timeout, missing schema, and other database failures return
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
M009 does not introduce a new metrics platform.

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

## The local runtime

Optional, and invisible in normal use. It exists because of one measured fact: on
mains power a one-shot `mindpalace recall` from a fresh process costs 6.53 s, of which
61 ms is memory work and the rest is `import torch`, `import transformers` and
`import sentence_transformers` (5.32 s together) plus model construction (0.49 s).

The runtime is a transport over the existing `memory_public.execute` and `remember`.
It runs the same code in a longer-lived process, so what it returns is byte-identical
to the in-process path: same claims, evidence, temporal state, conflicts,
abstention, receipt digest and Memory Pack digest. That is
`tests/test_m016_runtime_equivalence.py`.

| | before | after |
|---|---|---|
| `mindpalace recall` | 6.17 s | 0.63 s |
| `mindpalace explain` | 6.23 s | 0.67 s |
| `mindpalace receipt` | 6.21 s | 0.62 s |
| SDK `recall`, `runtime=True` | 5.11 s | 0.51 s |

MCP is unchanged and correctly so: an MCP server is a long-lived process that pays
the import once.

### Commands

```console
$ mindpalace runtime status
Runtime is not running.
  commands still work; each one starts its own runtime if you want a warm one
  start it with: mindpalace runtime start

$ mindpalace runtime start --wait
Runtime starting. It loads the model in the background.
  runtime: ready  model: loaded  up: 6s

$ mindpalace runtime status --json
{ "state": "ready", "pid": 1234, "model": "loaded", "protocol": 1, ... }
```

A command that finds no runtime runs in-process exactly as before and starts one in
the background. The warm-up is triggered by `init` and `remember`, which are already
fast, so by the time the user reaches for `recall` the model is resident.

### Security boundary

- A Unix domain socket, never a TCP port. `$XDG_RUNTIME_DIR/mindpalace`, or
  `~/.cache/mindpalace`. The directory is `0700` and the socket is `0600`, so the
  filesystem is the authorisation boundary.
- Every request carries a token read from a `0600` file next to the socket. Never a
  command-line argument, which any local user could read out of `ps`. The token is a
  second check on top of the socket permissions, so a socket that ends up somewhere
  more permissive does not become an open door.
- Loopback only in principle and Unix-socket-only in practice. There is no code path
  that binds a TCP port.
- Nothing about memory content is logged. The runtime's own output is discarded; the
  five product spans go through the product's telemetry, which never carries content.
- `MIND_PALACE_STATE_DIR` overrides the location, which is how the test suite gets a
  runtime of its own without touching a developer's.

### Lifecycle

| state | meaning |
|---|---|
| `starting` | bound, model not yet loaded |
| `loading` | importing the model |
| `ready` | serving |
| `stopping` | finishing an in-flight request |
| `failed` | could not start; `status` carries the reason |

- **Idle exit.** 15 minutes with no requests. It does not outlive the session that
  created it.
- **Stale sockets.** A runtime that was killed leaves its socket file. The next
  command sees `ECONNREFUSED`, removes it and starts a fresh one. A user never sees a
  stale socket as an error.
- **Single instance.** `bind()` refuses to take another live runtime's socket.
- **Protocol version.** A runtime speaks `PROTOCOL = 1` and refuses a request that
  asks for a different one, with an instruction to restart it. This is what stops a
  runtime from an older build serving a newer build's request with the old semantics.
- **No model, no silent different answer.** If the model cannot load, the runtime
  records `model: unavailable` and the reason, and keeps serving. A query then takes
  the same path it would have taken without a runtime, which for a genuinely missing
  model is the same `memory_unavailable` / 503 the CLI already produces. There is no
  new fallback and no lexical substitution.

### Turning it off

```bash
export MIND_PALACE_RUNTIME=0     # CLI, SDK and runtime command alike
mindpalace runtime stop          # just this one
```

The runtime is never required. On a platform without `AF_UNIX` it is not started at
all. `MindPalace(runtime=True)` is opt-in for the same reason: a long-running
application has the model resident, so a runtime would only add a socket hop and a
second copy of the model.

### A rejected alternative

The other way to remove the 5.3 s import from *every* command rather than from all
but the first — a lexical fast path, without a background process at all — was
measured and rejected. It is written up in full under
[model-free / lexical mode](#model-free-lexical-mode) below, because the reason it
failed is about relevance and abstention rather than about processes.

## Model-free / lexical mode

```bash
MIND_PALACE_LEXICAL=1 mindpalace recall "What datastore does production use?"
```

Means: **deliberately use the model-free lexical relevance path.**

This is not a second retriever. `memory_query` already falls back to lexical relevance
whenever the embedding model cannot be loaded, and this mode takes that branch on
purpose. One implementation, one set of acceptance gates, one set of authoritative
semantics. The mode is announced once on stderr, so it never contaminates an answer, a
JSON document, or a pipe:

```console
$ MIND_PALACE_LEXICAL=1 mindpalace recall "What datastore does production use?"
retrieval: lexical (model-free, MIND_PALACE_LEXICAL=1)
The production datastore changed from SQLite to PostgreSQL.
   source: memory/datastore-aa7e5b234e1d.md @ 5aae989d3134
   time:   true as of 2026-10-08 03:40 UTC
```

### Measured, not asserted

In-process, cold process, runtime disabled, same corpus:

| | semantic | `MIND_PALACE_LEXICAL=1` |
|---|---|---|
| `recall` | 6.17 s | **0.63 s** |
| `explain` | 6.23 s | **0.67 s** |
| `history` | 0.81 s | 0.63 s |
| `torch` / `transformers` / `sentence_transformers` imported | yes | **none** |

The speed comes entirely from not importing the model stack: `import torch` is
1.68 s, `import transformers` 1.20 s, and `sentence_transformers` adds 2.66 s on top
of those. `tests/test_m016_lexical_mode.py` asserts both halves — that the imports do
not happen, and that the command is measurably faster because of it — so the mode
cannot quietly become a slower way of doing the same thing.

### What it is good for

- A lightweight or offline machine where a 1.35 GB model install and a 6 s first
  recall are not acceptable.
- A container with no GPU and no outbound network.
- A CI job that needs authoritative memory but not semantic ranking.
- Anyone who wants predictable, explainable token-overlap ranking and can live with
  the vocabulary matching that implies.

### What it costs

**It does not always select the same keys as semantic mode, and that is not a bug to be
fixed.** Measured on a ten-fact corpus across sixteen question shapes, the two modes
agreed on fifteen. The difference was an abstention:

```text
"When did we last change the backup schedule?"
    semantic -> ['Backups run nightly at 02:00 UTC and are retained for 35 days.']
    lexical   -> [] ['NO_RELEVANT_MEMORY']
```

Semantic ranking answers from meaning; lexical ranking needs shared vocabulary. So
lexical mode is best understood as the model-free rung of the same relevance gate
rather than a replacement scorer. Nothing was weakened to make the two agree — an
answer that differs only in what it declines to say is still a wrong answer.

`tests/test_m016_lexical_mode.py` asserts this divergence deliberately. If a future
change makes the modes agree here, that test should be changed with a reason rather
than left to fail or quietly deleted.

#### The sharpest version of the same limitation

Ranking scores the terms that **discriminate between candidates** — a term every
candidate carries cannot tell them apart, so it is removed before scoring. That is
correct for ranking, and it has a consequence worth knowing before you rely on this
mode:

```console
$ MIND_PALACE_LEXICAL=1 mindpalace recall "What datastore does production use?"
No memory matched that question.
  NO_RELEVANT_MEMORY
```

Two `datastore` memories exist — one superseded, one current — so `datastore` is a term
*every* candidate carries, it is removed, and lexical ranking has nothing left to score.
Ask with a term that actually distinguishes them and it answers:

```console
$ MIND_PALACE_LEXICAL=1 mindpalace recall "When did we switch to CockroachDB?"
The production datastore is now distributed CockroachDB.
```

This is a property of lexical ranking that predates v0.9.0 and is unchanged by it;
`api/services/memory_relevance.py` scores on the reduced term set in both. It is also
why the CI step for this mode asserts that a *discriminating* question is answered,
rather than only that the command exits successfully — a mode that abstained on
everything would otherwise pass.

### Interacting with the runtime

A lexical read **bypasses the runtime**. A runtime exists to hold the model, and a
developer who asked for model-free reads does not want that process in the path.
Bypassing it needs no protocol change and no second implementation: the same
`execute` runs in the caller's own process. `mindpalace runtime status` continues to
report on the runtime honestly; it simply is not involved in that read.

### Why not a lexical fast path

An obvious alternative: rank lexically first, and load the semantic model only when
that looks insufficient. No background process, no second mode, and it would remove
the 5.3 s import from *every* command rather than from all but the first. It was
measured on a ten-fact corpus across sixteen question shapes, and on fifteen it was
byte-identical to semantic ranking. The sixteenth was not:

```text
"When did we last change the backup schedule?"
    semantic -> ['Backups run nightly at 02:00 UTC and are retained for 35 days.']
    lexical   -> [] ['NO_RELEVANT_MEMORY']
```

A question semantic ranking answers, lexical ranking abstains on — and shipping it
would have made `NO_RELEVANT_MEMORY` depend on whether the model happened to be
cached on the machine, which is the acceptance coupling the M013 work exists to
prevent, reappearing somewhere new. On 15 of 16 questions it looked safe, which is
precisely why it was worth writing down rather than discarding.

An explicit mode is honest about the trade-off; an automatic one hides it. This mode
is the same rung of the same gate, taken on purpose, so a user who wants model-free
reads opts in and knows exactly what they are getting.

### The model dependency itself

`sentence-transformers` remains a declared dependency, so `pip install mindpalace-os`
keeps ranking semantically by default. That was a deliberate decision not to move it
into an optional extra: doing so would mean an existing user upgrading a plain install
silently lost semantic ranking — a surprising behaviour change for the majority case.

What the dependency actually costs, measured on this machine:

| | installed size |
|---|---:|
| the Mind Palace wheel | 435 KB |
| `mindpalace-os` installed | 2.1 MB |
| `sentence-transformers` + `torch` + `transformers` + numpy/scipy/scikit-learn | **1,347 MB** |
| cached `all-MiniLM-L6-v2` weights | 88 MB |

The product is about 2 MB and the model stack is about 1.35 GB — a ratio of roughly
670 to 1. That is the whole reason a model-free mode is worth having, and also the
reason it is opt-in: semantic ranking is the better default, and most users should
have it without asking.

What *is* guaranteed, and tested, is that nothing in the package needs the model stack
merely to be imported or to run a command. `tests/test_m016_model_free_package.py`
blocks `torch`, `transformers` and `sentence_transformers` at the import hook and
checks that `--help`, `init`, `recall` and `explain` all still work. So the light path
is real today, and revisiting the dependency model later would be a packaging change
rather than a code change.

## Security posture

> **Corpus content is data, not instructions.**

Retrieved text is never promoted into trusted system instructions. Mind Palace
attributes evidence and records provenance; **the consuming application enforces its
own trust boundary.** Anything that can write a document can put text into a recall
response, so a model that reads one without this distinction is a prompt-injection
surface, and no receipt or verification status changes that.

- **Authentication is a documented contract, not implemented code.** See
  [Authentication](#authentication) below. Restrict REST and MCP to trusted callers
  and enforce it in front of the service before exposing anything sensitive.
- **SQL namespace scoping is not access control.** A corpus boundary in the query is
  a correctness boundary. It is not a permission system, and a caller who can reach
  the database can reach every corpus the database user can reach.
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
  [Security boundary](#security-boundary). There is no network listener to protect,
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

A production deployment should terminate TLS in front of the service and enforce
these in front of it, or as a FastAPI dependency keyed on the key:

- **`POST /api/memory/remember`** requires the read-write role.
- Every other `/api/memory/*` route requires at least the read role.
- A request whose corpus is outside the key's scope is rejected as `404`, not
  `403`: the existence of another tenant's corpus is itself not this client's
  business.
- Feed cursors are already HMAC-signed with `MIND_PALACE_CURSOR_SECRET` and are
  bound to one corpus. Two clients must not share a signing secret.

OIDC, JWT and per-user identity are deliberately not built. Nothing in the product
needs them, and adding them before a real deployment asks would be infrastructure
invented for a résumé rather than for a requirement.

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
- The API has no built-in authentication or per-corpus authorization. Deploy
  it only on a trusted interface and protect the corpus namespace accordingly.
- M009 intentionally excludes MCP and does not claim broker, CDC, or
  transactional-delivery semantics.
