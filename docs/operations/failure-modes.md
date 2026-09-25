# Failure modes

Each entry names a failure, how it shows up, why it happens, and what the
repository already does about it. Sources: `api/services/memory_feed.py`,
`api/services/memory_public.py`, `api/main.py`, `api/services/ingestion.py`,
[`docs/operations.md`](../operations.md), and
[`docs/m009/RELEASE_READINESS.md`](../m009/RELEASE_READINESS.md).

## Cursor and feed

**Cursor signing is not configured.** A missing or blank
`MIND_PALACE_CURSOR_SECRET` raises `503 cursor_unavailable`; a secret shorter
than 32 bytes raises the same code with a configuration message. There is no
predictable fallback. Compose requires the variable explicitly
(`docker-compose.yml` uses `${MIND_PALACE_CURSOR_SECRET:?...}`) rather than
picking a development value. Detection: the `503` code and the
`mindpalace.ops` error field.

**The signing secret changed.** Outstanding cursors fail signature validation
and come back as `422 invalid_cursor`. Clients must restart a traversal. Detect
it by watching for `invalid_cursor` rate after a configuration change.

**A cursor is replayed against another corpus.** The signed payload carries the
corpus name and `decode_cursor` compares it to the request, returning
`422 cursor_corpus_mismatch`. The live wrong-corpus check is recorded in
`docs/m009/live-interface-validation.json`, where REST and SDK both return that
code.

**A row committed behind an issued boundary is missed.** This is the documented
feed limitation, not a bug. A row whose `(observed_at, version_id)` is at or
before an issued cursor is not returned by that continuation, though it appears
in a fresh traversal. The recorded concurrency run inserted exactly such a row:
28 of 34 target-corpus rows came back in the continuation and the remaining row
was present only in the fresh traversal. Mitigation is periodic full
reconciliation, which is a client responsibility.

**A client reaches the tail and keeps polling.** The final page has
`has_more=false` and `next_cursor=null`, so a polling client must restart and
deduplicate or hold its own boundary policy. M009 deliberately does not invent a
hidden process-local tail cursor.

**Feed errors leak internals.** They do not. `translate_database_error` maps
SQLSTATE `42P01` and `42703` to `memory_unavailable` with "apply migrations" and
everything else to `database_unavailable`. Bodies carry only a stable code and
message, never SQL, credentials, connection strings, tracebacks, or filesystem
paths. Cursors and signing material are never logged; the release artifacts
contain redacted cursor metadata only.

## Database availability

**PostgreSQL is down at startup.** Application startup does not query the
database, so the process starts and answers `GET /health/live` with
`{"status":"alive"}`. A container or orchestrator that probes liveness keeps a
running process; one that probes readiness removes it from service. The legacy
`/health` route always answers `200` and is not the dependency-aware signal, so
`docker-compose.yml` and `mindpalace doctor` use `/health/ready`.

**Readiness is checked against a missing schema.** `GET /health/ready` requires
connectivity plus `corpora`, `memory_documents`, and `memory_versions`. Missing
relations, connection refusal, timeout, and other failures return a sanitized
`503` with no exception detail. The timeout is
`MIND_PALACE_READINESS_TIMEOUT_SECONDS`, default 2 seconds.

**A backend outage looks like an empty index.** The search router converts
`OperationalError` to `503` rather than an empty result set, and the comment in
`api/routers/search.py` says why: a client must be able to tell "nothing
matched" from "the store is unreachable". The same distinction holds for the
context route.

**A claim is returned without evidence.** `memory_public.project` raises
`503 memory_unavailable` with "Archived claim has incomplete provenance" rather
than emitting an unattributed assertion. The database should make this
unreachable, since a deferred constraint trigger requires evidence before commit.

**The semantic model is missing or fails to load.**
`memory_query.query` runs inference on a worker thread and converts
`OSError`, `RuntimeError`, and `ValueError` to
`503 memory_unavailable`. Set `HF_HUB_OFFLINE=1` and
`TRANSFORMERS_OFFLINE=1` and provision the model cache before using the query
operation in a closed environment.

## Ingestion

**A document fails mid-ingest.** Ingestion runs parse, claim validation,
embedding, document upsert, chunk replacement, archive append, and manifest
update in one caller-owned transaction under the corpus advisory lock. A failure
rolls back live rows and archive rows together, so a failed document never
leaves a false "successfully indexed" state. Sync reports the failure count.

**An authored claim is invalid.** `ClaimValidationError` carries the path, the
version fingerprint, and the reason, and is raised before embedding or any live
mutation. The reasons include evidence that is not an exact substring of an
archived chunk, claim text absent from the supporting chunk, a duplicate key
within one version, duplicate quotes, or `valid_until` before `valid_from`.
Validation errors escape the transaction context so the live write rolls back
too.

**A re-ingest looks like a change.** Change detection hashes raw content plus
all normalized metadata, including claims, so identical input returns the
existing version with `skipped=True` and produces no feed item. A prose-only
edit does create a version, and it is reported with `memory_changed=false`: the
source changed, the assertion did not.

**Memory archiving appears to turn off.** It cannot, per corpus. A corpus with
any archived version keeps archiving on later writes, and a document authoring
`claims:` is archived even when the flag is off.

**A stale manifest reports a deleted file as unchanged.** `_delete_stale_paths`
purges orphaned manifest rows whose document no longer exists, so a manifest
entry cannot make a file look indexed when it is not.

**Sync is not a directory transaction.** It is per-document atomic. A failure
partway through leaves earlier documents committed. Deletions require an
explicit sync, and renames are new identities rather than moves.

## Snapshot and replay

**A snapshot cutoff is in the future.** Rejected with
`422 invalid_timestamp`; a snapshot cannot contain changes that have not been
observed.

**Snapshot and time selectors are combined.** `snapshot_id` with `as_of` or
`valid_at` is `422 invalid_request`, because the observation and validity clocks
would be ambiguous.

**Late membership could change a replay.** Under 004 and 005 this is prevented
for evidence: `005_multiple_evidence` refuses evidence inserts into a
snapshotted version under the same advisory lock that `snapshot()` takes. The
post-release follow-up adds
`migrations/versions/006_snapshot_membership_seal.py`, which extends the same
protection to snapshot membership itself, with a seal row, a statement trigger
that rejects new membership for a sealed snapshot, and a deferred trigger
requiring the seal before a snapshot commits. It is not part of the immutable
`v0.6.0` release. Its database-backed tests passed against the local PostgreSQL
15.4 service. Without that migration, membership rows remain append-only but a
direct writer can add another membership row for an existing snapshot.

**A pack is silently incomplete.** `truncated=true` marks it. A pack is
`truncated=false` only when nothing was dropped, and `422 invalid_budget` is
returned when the empty envelope does not fit. Documentation states plainly that
truncation is not evidence of absence.

**Small budgets drop relevant claims.** Measured in
[`retrieval-evaluation.md`](../research/retrieval-evaluation.md): exact sets at
1,000 characters were 3/40 while 8,000 characters gave 30/40. Budget correctness
and retrieval completeness are different properties.

## Trust boundary

**Corpus content is treated as instructions.** It is data. Archive and retrieval
treat it as inert text, and `tests/test_security.py` covers injection-shaped
content being stored and returned unchanged. The memory-mode `/ask` prompt
states the same rule to the model and requires abstention when evidence is
missing, but prompt-level instruction is not a verified boundary.

**Attribution is read as truth.** It is not. A quote proves the archived chunk
contained that text at those offsets. It does not prove the claim is true and
does not establish which side of a conflict is correct.

**Namespaces are treated as authorization.** They are not. There is no built-in
authentication and no per-corpus access control. Deploy on a trusted interface
and protect the corpus namespace at the network layer. The SDK's `headers`
parameter can carry a proxy's own authentication, but the product does not
enable it.

**Archive rows are treated as erasable.** They are not, through ordinary DML.
Statement-level triggers reject `UPDATE`, `DELETE`, and `TRUNCATE` on the seven
archive tables created by `004_memory.py`, so corpus deletion fails while archive
rows exist. This is not a retention or erasure solution, and the database owner
is not a tamper-proof boundary. Archive retention and erasure remain an open
item.

**Renames are treated as moves.** They are not. A rename produces a new document
identity, and no MOVED state is inferred.

## Operational configuration

| Condition | Result |
|---|---|
| `MIND_PALACE_CURSOR_SECRET` unset or under 32 bytes | `503 cursor_unavailable`; feed unavailable |
| Cursor from another corpus | `422 cursor_corpus_mismatch` |
| Altered cursor, wrong shape, non-canonical base64, wrong version or feed | `422 invalid_cursor` |
| `page_size` outside 1 to 500 | `422 invalid_page_size` |
| Unknown corpus | `404 corpus_not_found` |
| Missing archive schema | `503 memory_unavailable` |
| Database unreachable | `503 database_unavailable` |
| Snapshot id not in corpus | `404 snapshot_not_found` |
| Claim id not in corpus or state | `404 claim_not_found` |
| Path not in corpus or state | `404 document_not_found` |
| Budget below 512 or above 128000 | `422 invalid_request` at request validation |
| In-range budget too small for the empty envelope | `422 invalid_budget` |
| Empty `query` for the `query` operation | `422 invalid_query` |
| Temporal intent without a cutoff, or an unresolvable stage name | `422 invalid_timestamp` |

The SDK preserves the server's code, message, and status for remote failures and
adds `timeout` (504), `transport_error` (503), and `invalid_response` (502) for
its own conditions. The CLI prints `code: message` on stderr and exits non-zero.

## Observability limits

One structured `mindpalace.ops` record is emitted per feed request with bounded,
non-content fields: operation, corpus name, requested page size, returned count,
`has_more`, whether a cursor was supplied, latency, and error category. Context
and search operations emit stage timings, candidate and returned counts, token
estimate, and the truncation flag. Neither record contains cursor contents,
signatures, secrets, database URLs, or document contents, and there is no new
metrics platform beyond the existing Prometheus HTTP instrumentation.
