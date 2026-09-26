# Post-v0.6.0 Deep Architecture Report

## 1. Executive Decision

Keep the post-release follow-up small and concrete:

- Keep the lazy semantic boundary. `api.main` imports without Torch,
  `sentence_transformers`, or `transformers`; the model loads on first semantic
  use and remains resident.
- Keep the public-memory projection reuse and exact-key conflict grouping.
- Keep the new live corpus scope. Retrieval resolves a corpus before embedding,
  requires an explicit name when several corpora exist, and passes the resolved
  ID through SQL.
- Keep migration `006_snapshot_membership_seal`. A sealed snapshot cannot gain
  a new membership row through a later direct insert.

Do not create a new milestone, change the `v0.6.0` tag, or alter M009 evidence.
Do not add a graph database, Redis, a queue, autonomous extraction, or another
feed interface from this audit.

## 2. Current System Reality

Mind Palace is a durable, versioned, evidence-backed memory ledger and
retrieval layer for AI systems operating over evolving corpora.

The authoritative archive is PostgreSQL-backed. It stores immutable document
versions, authored claims, exact evidence, validity bounds, lifecycle events,
supersession, conflicts, and snapshot references. Live retrieval is a separate
path over indexed chunks. Memory operations use the archive and can reconstruct
state without relying on the live index.

The public product exposes REST, a Python SDK, a CLI, and MCP for ordinary
memory operations. The M009 feed is exposed through REST, SDK, and CLI. It is
not exposed through MCP because it has a separate cursor, pagination, and error
contract.

This audit started from `bb8ce0b` and covers the follow-up commits stacked on
top of it. The immutable release remains `v0.6.0` at
`033c1484dca53fbb40dbf82904aacf5f2834142a`.

## 3. Competitive Capability Matrix

The matrix in
[`docs/architecture/competitive-analysis.md`](../../architecture/competitive-analysis.md)
compares Mind Palace with Graphiti, Mem0 OSS, Letta/MemFS, MemPalace, and
conventional RAG or vector search.

The important boundary is authority. Mind Palace makes source claims and exact
evidence authoritative. Graphiti provides a temporal graph and incremental
derived updates. Mem0 provides scoped conversational or inferred memories.
Letta provides persisted agent state and shared memory blocks. Those systems
solve related problems, but the reviewed material does not show the same
quote-and-offset source ledger, authored conflict contract, and snapshot replay
boundary.

The comparison is architectural. No head-to-head latency, accuracy, or
competitive benchmark was run.

## 4. Mind Palace's Actual Differentiators

Mind Palace can demonstrate the following properties directly:

1. A returned claim points to an archived source version and one or more exact
   quote spans.
2. A historical query can be answered from captured archive rows rather than
   from the current live index.
3. A snapshot captures version references, and replay can be compared after
   later updates, deletions, or restorations.
4. Same-document replacements are represented as supersession. Different active
   documents with the same key and different values are represented as
   conflicts.
5. A bounded Memory Pack is deterministic for fixed inputs and makes truncation
   visible.
6. A corpus-scoped feed provides durable operational continuation with a signed
   cursor and deterministic ordering.
7. Retrieval and archive policy are separate. A model can rank text without
   becoming the authority for what the archive says.

These properties are documented in
[`docs/architecture/memory-model.md`](../../architecture/memory-model.md) and
[`docs/architecture/provenance-model.md`](../../architecture/provenance-model.md).

## 5. Architectural Gaps

The audit found several concrete gaps:

- Ordinary memory reads load the full time-scoped archive before applying
  selectors.
- The temporal model separates observation time from authored validity but does
  not implement a full transaction-time or correction-time database.
- Provenance does not yet carry writer identity, a trust class, or derivation
  metadata for future derived memories.
- Corpus namespaces are not an authorization system. The product has no
  authentication or per-corpus ACL.
- HTTP ingestion and synchronization accept server filesystem paths and remain
  deployment-sensitive.
- There is no archive export and import format. Stable interfaces are not the
  same thing as portable corpus interchange.
- Snapshot replay still loads and filters the archive in Python rather than
  joining directly from the captured reference set.
- Memory Pack accounting repeatedly serializes a growing candidate response.
- The feed is a polling continuation interface, not exactly-once delivery,
  CDC, or a broker.
- The current image keeps the semantic runtime and the durable API in one
  image. This is operationally simple but leaves the CPU Torch dependency in
  the image.

## 6. Temporal Semantics

Mind Palace records two clocks:

- `observed_at` is when the archive observed a version.
- `valid_from` and `valid_until` are authored validity bounds.

`as_of` filters `observed_at` inclusively. `valid_at` evaluates the authored
window `[valid_from, valid_until)`. A null bound is unknown, not an infinite
bound. A snapshot fixes both clocks to the saved cutoff.

This separation prevents a newly observed correction from becoming current
solely because it is newer. It also preserves the difference between “the
system saw this” and “the source says this was true.”

The model is not fully bi-temporal. It does not independently store transaction
time, correction time, or a complete event-time history. The exact limits and
feed ordering are documented in
[`docs/architecture/temporal-semantics.md`](../../architecture/temporal-semantics.md).

## 7. Provenance / Evidence Model

Each evidence row records the claim, version, chunk, quote, and character
offsets. PostgreSQL triggers enforce exact substring containment, offset
arithmetic, and the requirement that every claim have evidence before commit.
The service also validates authored claims before writing.

A response attaches sources only from retained evidence. A claim without
provenance is not returned as an ordinary memory result. A bounded pack keeps
all evidence for retained claims and keeps conflict alternatives together.

Provenance establishes lineage, not truth. It does not prove that a source is
reliable, that a claim is entailed, or that a downstream model will treat
untrusted text safely. Source text remains data, including text that looks like
an instruction.

## 8. Retrieval Evaluation

The repository keeps three evaluations separate:

- live retrieval over labelled chunks;
- M006 memory semantics over an evolving authored corpus;
- M006.75 natural-language memory retrieval over a frozen held-out set.

M006.75 remains **47/60** exact/full. Its safety checks report zero
false-current promotions, provenance failures, temporal failures, conflict
failures, budget failures, and execution failures on the frozen set. This is a
project-specific result, not a claim of superiority or production readiness.

M007 and M008 remain separate research programs. M007.1 is adjudication-ready,
but the repository records no independent decisions. No M007 or M008 scientific
result is promoted into the product report.

The full evaluation boundary is in
[`docs/research/retrieval-evaluation.md`](../../research/retrieval-evaluation.md).

## 9. Performance Baseline

The controlled performance artifacts use the existing
`scripts/performance_probe.py` harness. They record the source revision,
environment, dependency versions, fixture identity, warmups, and iterations.

The startup comparison is:

| Probe | Before | After |
|---|---:|---:|
| `api.main` import wall time | 5,534.272 ms | 589.137 ms |
| Process wall time | 6,783.009 ms | 798.901 ms |
| Imported modules | 4,221 | 682 |
| Peak RSS | 861,428 KB | 80,696 KB |

Semantic cost remains explicit:

```text
Embedder construction:       0.015 ms
first embedding:           5,110.624 ms
warm embedding:               10.388 ms
```

The model is lazy, single-flight, and resident after first use. DB-only paths do
not pay that model load.

## 10. Database Scaling

The released feed remains the strongest measured scaling path. At 10,000
versions and page size 50, full traversal measured p50 **808.565 ms** and p95
**834.543 ms**. Query plans show the `(observed_at, version_id)` keyset
predicate and the relevant index. The feed does not use `OFFSET`.

The ordinary public memory path has a different cost shape. At 1,000 versions,
the post-change measurements were:

| Operation | After |
|---|---:|
| current | 722.755 ms |
| history | 670.126 ms |
| changes | 674.490 ms |
| evidence | 655.854 ms |
| as-of | 663.686 ms |
| snapshot | 710.233 ms |
| replay | 680.227 ms |
| pack | 927.356 ms |
| feed | 39.963 ms |

The archive loader still performs broad reads. The response budget does not
bound database or Python work. A 100,000-version feed attempt exceeded the
20-minute harness bound and is recorded as deferred, not as a result.

The follow-up corpus-scope change does not alter these archive timings. The
snapshot seal tests passed against PostgreSQL 15.4, and the broader archive,
replay, pack, and evidence suite passed with 117 tests against the local
PostgreSQL service.

## 11. Semantic Runtime

The semantic extension is internal and lazy. `Embedder()` construction is
model-free. First use imports `sentence_transformers`, loads the configured
model, and computes an embedding. A model lock prevents duplicate first-use
initialization, and the loaded model remains available for later requests.

The durable routes resolve corpus scope before calling the embedder. An empty
corpus therefore returns an empty result without loading a model. Multiple
corpora require an explicit name rather than selecting the first row.

The current image keeps this extension available in the API runtime. That
preserves one operational image and one dependency contract. A separate
DB-only image could reduce footprint, but it would add deployment and release
complexity. The audit defers that split until a deployment requirement makes
it worthwhile.

## 12. Memory Pack

A Memory Pack is a bounded, machine-readable projection of selected archive
claims, evidence, sources, conflicts, changes, state, and constraints. Its
budget is measured in Unicode characters of the complete canonical JSON
envelope, not tokens or bytes.

The pack preserves exact evidence and conflict closure. It marks truncation and
does not present a truncated result as proof of absence. Repeated candidate
serialization remains a measured cost and is deferred until an incremental
accounting method can prove byte-for-byte compatibility with the frozen pack
oracle.

## 13. Feed / Synchronization

The M009 feed is a corpus-scoped read over immutable `memory_versions` rows.
Its contract is:

```text
(observed_at ASC, version_id ASC)
```

The cursor is opaque, HMAC-signed, corpus-bound, bounded, and usable across
process restarts when the same secret is configured. Page size is 1 through
500. The service requests one extra row to determine `has_more`.

The feed does not promise exactly-once delivery, CDC, broker semantics, a
global snapshot, or lossless late-arrival continuation. A client that needs
complete reconciliation must periodically start a fresh traversal and
deduplicate according to its own boundary policy.

The feed remains separate from MCP. Adding it to MCP would create a second
unversioned operational contract on an unauthenticated tool surface.

## 14. Portability

Mind Palace has portable interfaces, not a complete portable archive format.
The schema, stable identifiers, canonical Memory Pack output, and feed contract
provide useful interchange boundaries. A future export/import format must also
define schema compatibility, content integrity, source references, migration
behavior, and preservation of snapshot membership.

The current product does not claim that a PostgreSQL database can be copied
between deployments without migration and operational checks. Portability is
therefore an explicit future engineering area, not a current guarantee.

## 15. Multi-Agent Memory

A corpus is independent of a particular agent, so several agents can use the
same durable corpus. That is useful for shared applications and offline
evaluation. It is not an access-control model.

Letta's shared memory blocks provide a useful product reference for making
shared state explicit and attaching it to multiple agents. Mind Palace can
apply that lesson while retaining immutable source versions and explicit claim
authority. A future shared-corpus design still needs writer identity,
permissions, conflict policy, and concurrent-write rules.

## 16. Security / Memory Poisoning

Corpus scoping closes a concrete live-retrieval isolation gap. It is not
authentication. The existing security boundary still requires deployment-level
network protection and does not provide per-corpus authorization.

Source documents are untrusted data. A document can contain text that looks
like a system instruction, a command to delete policy, or a false permanent
fact. The archive preserves that text and its lineage. It does not promote the
text to an instruction or silently resolve a contradiction.

Downstream generators must treat retrieved fields as untrusted data. The
memory-mode prompt states this rule, but prompt text is not a complete
security boundary. A future trust-class model should distinguish source,
authored, derived, inferred, and unverified material without rewriting the
authoritative archive.

## 17. Developer Experience

The product exposes a short path from a named corpus to sync, retrieval,
context, and memory operations. The README now distinguishes the current
product, released M009 capabilities, research status, and future work.

The live retrieval APIs accept an optional corpus for the one-corpus case and
require an explicit name when several corpora exist. The legacy CLI commands
now expose `--corpus` for search, ask, summarize, interview, related documents,
and timeline. The public memory CLI remains remote-only and has a separate
feed command.

The main developer-experience gaps are unauthenticated HTTP surfaces,
filesystem-based sync paths, no archive export/import, and no supported
`mindpalace` import alias. The repository documents the supported
`mindpalace_sdk` module rather than implying that `import mindpalace` works.

## 18. Reliability / Failure Modes

The failure matrix is in
[`docs/operations/failure-modes.md`](../../operations/failure-modes.md). The
important behaviors are:

- liveness checks the process and does not query PostgreSQL;
- readiness checks database connectivity and required archive relations;
- feed cursor failures are sanitized and stable;
- ingestion is per-document atomic, not a directory transaction;
- archive writes and live writes share a caller-owned transaction;
- corpus locks serialize writers per corpus;
- a missing or ambiguous live corpus cannot fall back to unscoped retrieval;
- a database outage during `/api/query/ask` returns `503`;
- snapshot membership is sealed by the post-release migration.

The archive remains append-only under ordinary DML, not tamper-proof against a
database owner. Retention, erasure, authentication, and full portability are
open operational policies.

## 19. Changes Implemented

### Live corpus scope

`api/services/corpora.py` now resolves omitted scopes safely. Search, context,
default RAG ask, summarize, interview, related-document, and timeline routes
resolve before semantic work. Retrieval document queries bind `corpus_id`.
Legacy CLI commands expose the same selection.

The route tests cover explicit names, one corpus, multiple corpora, unknown
names, empty databases, ambiguous scope, empty results, corpus propagation, and
the ask backend outage path.

### Snapshot membership seal

`migrations/versions/006_snapshot_membership_seal.py` adds a seal table,
backfills existing snapshots, rejects later membership inserts, and requires a
seal before a new snapshot commits. `api/services/memory.py` writes the seal
inside the existing transaction.

The migration is the single Alembic head. Offline SQL generation succeeds.
The five database-backed seal tests pass on PostgreSQL 15.4.

### Frozen benchmark boundary

`scripts/benchmark/m00675.py` now hashes migrations `001` through `005` for
the frozen M006.75 fixture identity. Post-release migration `006` cannot
silently change the released research artifact. The existing result and
manifest files were not edited, and the integrity test passes.

### Documentation

The audit adds architecture, competitive, retrieval-evaluation, failure-mode,
scaling, and performance comparison documents. Existing performance artifacts
remain in their original paths.

## 20. Before / After Evidence

| Change | Before | After | Evidence |
|---|---:|---:|---|
| API import | 5,534.272 ms | 589.137 ms | `startup-baseline.json`, `startup-after.json` |
| Imported modules | 4,221 | 682 | startup artifacts |
| Peak RSS | 861,428 KB | 80,696 KB | startup artifacts |
| 1,000-version current | 1,831.301 ms | 722.755 ms | operations artifacts |
| First embedding | process import path | 5,110.624 ms on first use | `semantic-after.json` |
| Live corpus scope | omitted scope could reach unscoped retrieval | empty, single, or explicit scope only | 19 corpus-scope tests |
| Snapshot membership | append-only rows, no membership seal | sealed membership set | 5 live PostgreSQL tests |
| Full regression | prior committed baseline | **994 passed, 153 skipped, 10 warnings** | final test run |
| Image | prior audit result | 1.294 GB, 10 layers, UID/GID 10001 | rebuilt image audit |

The image import check reported `torch-loaded False` and
`sentence-transformers-loaded False`. The image filesystem contained only the
API, migrations, content, and runtime requirement files under `/app`; Git,
tests, docs, evaluation data, reviewer files, virtualenvs, and caches were
absent.

## 21. Deferred Work

The following items are deliberately deferred:

- A projection-specific archive loader. It must preserve conflict closure,
  provenance, snapshots, and historical semantics.
- Snapshot replay through a direct reference join.
- Incremental exact Memory Pack accounting.
- Bounded RRF candidates, with retrieval-quality measurements first.
- A complete dependency lock and a base-image digest policy.
- A reproducible 100,000-version feed benchmark with a faster disposable
  fixture loader.
- Event-loop isolation beyond the current semantic worker-thread boundary,
  after concurrency profiling demonstrates a need.
- Archive export/import, writer identity, trust classes, and ACLs.
- A separate DB-only runtime image, only if operational demand justifies the
  added deployment complexity.

None of these items changes the M009 feed contract or converts research status
into a product guarantee.

## 22. Architectural Position

Mind Palace should be described as:

> A durable, versioned, evidence-backed memory ledger and retrieval layer for
> AI systems operating over evolving corpora.

The phrase “memory layer” describes the role. The ledger, provenance,
temporal, snapshot, and feed properties explain why the role is different from
a generic vector store or a conversational agent-memory helper.

The durable core remains model-free at import and for DB-only operations. The
semantic extension is optional at runtime and resident only after first use.
The current single-package boundary is justified. A package split can be
revisited if dependency isolation or deployment evidence requires it.

## 23. Next Concrete Engineering Objective

Use a production-shaped corpus fixture to measure a projection-specific archive
loader. The first gate is not speed. It is byte-level semantic equivalence for
current, history, evidence, conflicts, snapshots, and bounded packs at multiple
budgets. Keep the existing full-archive loader until that gate passes.

The next objective is a measured decision about whether the current archive read
shape is worth replacing. It does not require a new infrastructure layer.
