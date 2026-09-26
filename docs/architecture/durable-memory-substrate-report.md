# Mind Palace — Durable Memory Substrate Report

## 1. Thesis

Mind Palace is a durable, versioned, evidence-backed memory ledger and
retrieval layer for AI systems operating over evolving corpora.

The repository supports the thesis for corpora whose material has been
archived. It is not yet a universal memory system for every ingestion path, and
it does not yet provide archive export/import or authorization. Those limits are
part of the position, not reasons to hide the distinction between a projection
and an authority.

## 2. What Mind Palace Actually Is Today

The durable core is PostgreSQL-backed and append-only. It stores observed
source versions, authored claims, exact evidence, validity bounds, supersession,
conflicts, and snapshot references. Current state is a projection over that
history.

Live retrieval is a separate path over `documents` and `chunks`. It can use
embeddings and reranking, but memory operations do not require the live index.

The product exposes REST, a Python SDK, a CLI, and MCP for ordinary memory
operations. The M009 feed is exposed through REST, SDK, and CLI, and is
deliberately absent from MCP.

The authoritative path is model-free at import. Current, history, changes,
evidence, as-of, snapshot, replay, feed, pack, and provenance operations read
archive tables and do not require Torch, sentence-transformers, an LLM, Redis, or
MCP. The intent-resolved `query` operation reads the same tables and needs the
model only to rank candidates; when the model cannot load it falls back to
lexical ranking rather than failing. See
[`docs/research/memory-bakeoff.md`](../research/memory-bakeoff.md).

## 3. Competitive Landscape

The current capability matrix compares Mind Palace with Graphiti, Mem0 OSS,
Letta/MemFS, MemPalace, Laya, and conventional RAG or vector-memory systems.

The external review is pinned in
[`docs/competitive/capability-matrix.md`](../competitive/capability-matrix.md)
and [`docs/competitive/landscape.md`](../competitive/landscape.md). No external
system was run against the Mind Palace corpus. The comparison is architectural,
not a ranking.

The important overlap is larger than the earlier audit implied:

- MemPalace stores verbatim conversation drawers and is local-first by default.
- Letta's MemFS stores Markdown and YAML files in a git repository, with
  history and shared blocks.
- Graphiti uses a temporal graph with validity windows and episode lineage.
- Mem0 provides a simple add/search surface and supports extracted or raw
  memories.
- Laya supplies execution-engineering lessons about routing, lazy resources,
  batching, resident state, and benchmark discipline.

## 4. Mind Palace vs Graphiti

Graphiti makes temporal context graphs its primary representation. It derives
entities and edges, carries validity information on edges, and links facts back
to episodes. That is a strong fit when graph traversal and graph-native
queries are the requirement.

Mind Palace keeps a relational source and assertion ledger. A claim is authored
in source material and must appear literally in an archived chunk with offsets.
This is a narrower write model and a stronger evidence contract for the returned
assertion.

A graph is not required by the current Mind Palace queries. The current
relationship queries run over relational keys, versions, and claims. The research
graph helpers are not on the released query path.

## 5. Mind Palace vs Mem0

Mem0's simple API is a useful product model. Applications can send content and
later search it, with scopes, metadata, expiration, and update/delete behavior.
Its default path can infer structured memories, and its documentation tells
users to request raw storage when verbatim content is required.

Mind Palace should not replace authored memory with inferred memory. The current
write path validates source text and rejects a claim that is not literally
supported by the archive. That preserves a clear answer to “who decided this
was true?”

The simple surface is still available through SDK sync, search, context, and
memory operations. The audit defers a new `memory.add()` facade until the
product decides how it should represent authored versus derived material.

## 6. Mind Palace vs Letta

Letta's MemFS is an agent-owned file repository. Files are Markdown with YAML
frontmatter, edits become git commits, and blocks can be attached to multiple
agents. This is a strong model for agent-independent, inspectable, and portable
memory.

Mind Palace differs in the authority unit. A Mind Palace claim is tied to a
source version and an exact evidence span. A Letta memory file is authored by
the agent and its history is represented by commits. Both approaches preserve
history; they answer different questions.

The next product lesson from Letta is shared state. A corpus can be used by
several agents without making memory agent-owned, but shared writes still need
identity, permissions, and conflict policy.

## 7. Mind Palace vs MemPalace

MemPalace directly challenges the idea that extraction-first storage is
sufficient. Its default drawers keep verbatim conversation text, and its rooms
and wings provide a navigable organization model. It also publishes benchmark
artifacts and explicitly records the cost of its lossy AAAK representation.

Mind Palace already keeps raw `memory_versions.content`, including frontmatter
and the full source body. It adds a second layer that extraction-only systems
usually lack: authored claims, exact evidence, validity windows, supersession,
conflicts, and replayable snapshots.

The strongest combined architecture is therefore:

```text
raw source
  -> authoritative authored claims and evidence
  -> rebuildable semantic and retrieval projections
```

The audit does not claim that this combination is empirically better than
MemPalace. It demonstrates the properties that are present in the repository
and keeps the comparison honest.

## 8. Lessons from Laya

The following Laya principles are already reflected in Mind Palace:

- route and validate before loading an expensive runtime;
- construct semantic resources lazily;
- protect shared first-use initialization;
- keep loaded model state resident;
- batch compatible work and preserve order;
- keep optional capabilities optional;
- bind measurements to source, environment, workload, and iteration counts;
- isolate synchronous inference from the event loop where profiling requires it.

No Laya model or execution code was copied.

## 9. Memory Ledger Analysis

The ledger audit in
[`docs/architecture/ledger-analysis.md`](ledger-analysis.md) measured the
three layers in an isolated PostgreSQL schema.

| Layer | Tables | Mutability | Meaning |
|---|---|---|---|
| L0 source | `memory_versions`, `memory_documents` | Append-only under ordinary DML | Observed source bytes and lineage |
| L1 authority | claims, evidence, archived chunks, snapshots and seals | Append-only and constraint-checked | Authored knowledge and reconstructable state |
| L2 derived | `documents`, `chunks`, embeddings, manifest | Disposable | Live search and context projection |

Deleting L2 rows and the embedding index did not change the canonical results
of current, history, changes, evidence, as-of, query, pack, or replay. The
comparison pinned `valid_at` so that the test measured L2 independence rather
than wall-clock state.

The gap was that no code path rebuilt L2. The follow-up adds `api/services/rehydrate.py`
and `mindpalace reindex`. The command writes only L2 rows and never calls
`record_version`.

## 10. Raw vs Authoritative vs Derived Memory

L0 is the source of truth for what was observed. L1 is the source of truth for
what the system is willing to call a claim. L2 is an optimization.

L2 may contain embeddings, summaries, indexes, caches, or future inferred
statements. Deleting L2 must not delete L0 or L1. A future derived claim must
retain source document and version links, derivation method and version, a
status that cannot be confused with `CURRENT`, and an explicit cost.

The current system does not infer claims. That is deliberate. The derived-memory
contract is documented in
[`docs/architecture/derived-memory.md`](derived-memory.md), but no autonomous
extraction path was added.

## 11. Temporal Semantics

`observed_at` records when the archive observed a version. `valid_from` and
`valid_until` are authored validity bounds. `as_of` filters observation time;
`valid_at` evaluates the validity window. A snapshot fixes both clocks.

The model is temporal and partly bi-temporal in the sense that it separates
observation from validity. It is not a full transaction-time database. It does
not independently model correction time, transaction time, or every event-time
history.

A late correction can be observed now and valid in the past. A deletion is an
append-only tombstone. A restoration is a new version. These semantics are
sufficient for the current memory operations and are documented in
[`docs/architecture/temporal-semantics.md`](temporal-semantics.md).

## 12. Provenance / Evidence

Every authoritative claim has at least one evidence row. Each evidence row
identifies the claim, version, chunk, quote, and character offsets. The database
checks exact substring containment and offset arithmetic before commit, and a
deferred trigger requires evidence for every claim.

Responses derive sources from retained evidence. A bounded pack keeps evidence
with retained claims and keeps conflict alternatives together. Provenance is
lineage, not truth, and source text remains untrusted data.

The post-release change did not alter these guarantees. The audit added no
second provenance model for another interface.

## 13. Current-State Reconstruction

Lifecycle state is derived from archive history:

```text
NEW -> MODIFIED -> DELETED -> RESTORED
```

Same-document claims supersede through explicit links. Different active
documents with the same key and different values form conflict groups. The
archive is not rewritten to make current state convenient.

The ledger probe reconstructed the public operations after L2 removal and
obtained byte-identical canonical results. This is the core invariant behind
the substrate claim.

## 14. Snapshot Semantics

A snapshot is a deterministic capture of archive references at an inclusive
observation cutoff. It is not a second database and it does not copy live
index state.

The post-release migration `006_snapshot_membership_seal` makes membership
immutable after capture. A later direct same-corpus membership insert is
rejected. The service writes the seal in the same caller-owned transaction as
the snapshot.

Five live PostgreSQL tests passed against the local service. This strengthens
the replay contract without changing snapshot IDs or response formats.

## 15. Memory Pack

A Memory Pack is a bounded, machine-readable projection. Its budget is Unicode
characters of the complete canonical JSON envelope. It retains selected
current, historical, uncertain, conflict, change, evidence, and source data, and
marks truncation explicitly.

The pack is deterministic for fixed archive state, clocks, selectors, and
budget. It is not an archive export format. A consumer cannot assume that an
empty pack means the source corpus is empty, and the pack does not bound the
cost of reading the archive.

Repeated candidate serialization remains a measured cost. The audit defers
incremental accounting until it can prove exact compatibility with the frozen
pack oracle.

## 16. Retrieval Architecture

Live retrieval follows this order:

```text
corpus scope
  -> optional embedding
  -> candidate generation
  -> hybrid or vector ranking
  -> optional reranking
  -> bounded context or response
```

The corpus check happens before the model is loaded. Multiple corpora require an
explicit name. A missing model now produces the same sanitized 503 contract on
search, context, and ask, and the reranker is lazy as well.

The M009 feed is a separate read path. It does not share the live ranking
pipeline.

## 17. Retrieval Evaluation

The repository keeps live retrieval, M006 memory semantics, and M006.75
held-out memory retrieval separate. The frozen M006.75 result remains 47/60
with zero false-current promotions, provenance failures, temporal failures,
conflict failures, budget failures, and execution failures.

The current audit did not add a new competitor benchmark. It added no generated
answer evaluation and did not turn M007's blocked status into a result. The
retrieval evaluation boundary is recorded in
[`docs/research/retrieval-evaluation.md`](../research/retrieval-evaluation.md).

## 18. Graph Decision

Mind Palace does not need a graph database for its current relational queries.
The useful graph ideas, such as temporal edges and episode lineage, can be
represented as derived projections when a workload demonstrates a need.

No Neo4j, FalkorDB, Neptune, or graph migration was added. The research graph
helpers in `api/services/memory_reasoning.py` remain excluded from the API
image and are not treated as a production capability.

## 19. Change Feed

The M009 feed remains the durable synchronization primitive. It provides
bounded keyset continuation, deterministic ordering, and a signed corpus-bound
cursor.

It does not provide exactly-once delivery, CDC, a broker, or a lossless
late-arrival guarantee. Clients needing complete reconciliation must run
periodic fresh traversals and deduplicate.

The audit did not add a second feed, Kafka integration, or a hidden MCP feed.
The feed's MCP exclusion remains intentional.

## 20. Portability

The repository has portable interfaces, not archive interchange. The PostgreSQL
database is the archive. Moving it requires a controlled `pg_dump` and
`pg_restore` with the same extensions and migrations.

Memory Pack is a bounded read projection, not a complete export. The canonical
M006.75 artifact is reproducible only under its recorded source, Python,
dependency, model, and fixture identity. The follow-up fixes the benchmark's
migration boundary so post-release infrastructure does not silently change the
frozen fixture hash.

Archive export/import remains deferred because it requires a format, identity
rules, snapshot preservation, and compatibility policy. The details are in
[`docs/architecture/portability.md`](portability.md).

## 21. Multi-Agent Semantics

A corpus is independent of any one agent. Several agents can read the same
authoritative corpus, and the current L2 index can serve all of them. This is
shared corpus memory, not shared mutable agent state.

A future multi-agent policy must define writer identity, private overlays,
permissions, and conflict handling. The current system has corpus isolation,
not authorization. That limitation is deliberate and documented.

## 22. Local-First / Deployment Model

The authoritative core requires PostgreSQL with pgvector, pgcrypto, and
pg_trgm. The API image runs as UID 10001, exposes port 8000, and does not bake
model weights into the image. The semantic model is downloaded or mounted on
first use.

The minimum durable deployment is PostgreSQL plus migrations. Live search adds
an embedding model; optional reranking adds a second model. The new `reindex`
command rebuilds the live layer from the archive when a local index is lost.

This is self-hosted and local-first in the sense that the database and model
cache are controlled by the operator. It is not a zero-dependency embedded
store, and it is not a hosted service.

## 23. Security / Memory Poisoning

Source text is data. The archive does not promote instruction-like text into
system policy. The memory-mode generation prompt tells downstream models to
treat retrieved fields as untrusted.

This prompt rule is not a complete security boundary. A future trust-class
model should distinguish source, authored, derived, inferred, unverified, and
conflicted material without changing the authoritative archive.

Corpus scoping is now enforced on live retrieval. It is namespace isolation,
not authentication. Deployments still need network protection and, for
sensitive data, an authorization layer.

The corpus delete route now returns 409 when archive rows exist, rather than
leaking a foreign-key traceback as 500.

## 24. Performance Model

The measured cost model remains:

```text
routing + database + model load + embedding + ranking + projection + serialization
```

The lazy semantic boundary removed roughly 5 seconds and 780 MB of resident
memory from API import. The archive operations did not gain a hidden query
reduction; their improvement came from CPU and Python projection work.

The new follow-up preserves that boundary:

- corpus resolution precedes embedding;
- the reranker model loads only when reranking is requested;
- semantic dependency failures are translated to sanitized 503 responses;
- rehydration is an explicit operator action, not a request-path cost.

## 25. Database Scaling

The feed scales through bounded keyset pages. At 10,000 versions, the released
measurement was p50 808.565 ms and p95 834.543 ms for a full traversal at page
size 50. The 100,000-version attempt exceeded the harness bound and is recorded
as deferred.

The ordinary archive path still loads broad history. A projection-specific
loader and snapshot replay join remain the largest measured database work. They
are deferred until a production-shaped equivalence gate exists.

## 26. Semantic Runtime

`Embedder` is a model-free singleton until first use. The model is then loaded
once and reused. `Reranker` now follows the same first-use boundary and uses a
model lock.

The semantic extension is optional for the authoritative core. Removing the
embedding stack does not remove current, history, evidence, snapshot, replay,
feed, pack, or provenance operations. It does not remove `query` either, which
degrades to lexical relevance instead of returning 503.

It does still remove `context()` and `search()`, which are live-index surfaces
with no model-free path. Measured, not assumed: with a model that cannot load,
4 of 5 public memory surfaces still answer.

The container still ships the semantic dependency stack. Splitting the DB-only
runtime from the semantic runtime is deferred until deployment evidence makes
the extra operational surface worthwhile.

## 27. Developer Experience

The public path remains:

```text
install -> start PostgreSQL -> create corpus -> sync -> search/context
         -> memory current/history/evidence/as-of/snapshot/replay/pack
         -> feed -> SDK/REST/CLI/MCP
```

The audit fixed two concrete gaps in that path:

- live model failures return a stable 503 instead of a 500 traceback;
- sync summaries include bounded per-file error reasons, and the SDK preserves
  them.

The new `mindpalace reindex --corpus NAME` command restores live search from
the archive without touching L0 or L1.

- The Quick Start still needs clearer guidance that a corpus without authored
`claims:` has live retrieval but no authoritative memory. Making L0 archiving
unconditional is a product decision and was not bundled into this follow-up.
- The container image was rebuilt after these changes; its import boundary still
loads without Torch or sentence-transformers.

## 28. Candidate Architectural Advantages

These are candidate advantages, not marketing claims:

1. Exact evidence lineage at character-offset granularity.
2. Immutable source versions with deletion and restoration history.
3. Two-clock temporal state with explicit conflict retention.
4. A model-independent authoritative read path.
5. Rebuildable derived search state.
6. Deterministic bounded memory output.
7. A corpus-scoped durable change feed.
8. Snapshot and replay over a captured reference set.

Each is demonstrable through repository code or tests. A competitive moat
still requires external comparison and production evidence.

## 29. Changes Implemented

The post-release follow-up implements:

- `api/services/rehydrate.py` and `mindpalace reindex`;
- repository support for preserving an archived document ID during rehydration;
- lazy, locked reranker initialization;
- sanitized semantic dependency errors on search, context, and ask;
- bounded sync error details in service, REST, and SDK results;
- 409 archive conflict handling for corpus deletion;
- corpus-scope and rehydration regression tests;
- updated competitive, derived-memory, portability, developer-experience, and
  benchmark documentation;
- a stable single-head migration boundary for frozen M006.75 evidence.

## 30. Before / After Evidence

| Area | Before | After |
|---|---|---|
| Live model failure | 500 with dependency traceback | sanitized 503 |
| Reranker construction | model loaded eagerly | model loaded on first score |
| Sync failure | count only | bounded path and reason list |
| Corpus delete with archive | 500 foreign-key traceback | 409 archive conflict |
| Lost live index | no rebuild path | `reindex` from archive |
| M006.75 fixture hash | new migration changed frozen hash | migrations 001-005 remain the frozen boundary |
| Focused follow-up tests | not present | 165 passed in the expanded focused live set |
| Full regression | 988 passed, 151 skipped before follow-up | **994 passed, 153 skipped, 10 warnings** after follow-up |

The prior measured performance changes remain: API import 5,534.272 ms to
589.137 ms, imported modules 4,221 to 682, and peak RSS 861,428 KB to 80,696
KB. The 10,000-version feed measurement remains p50 808.565 ms and p95
834.543 ms.

## 31. Remaining Gaps

- L0 archiving is still opt-in per document or internal flag.
- Live-only documents cannot be reconstructed because no L0 version exists.
- No archive export/import format exists.
- No authentication or per-corpus ACL exists.
- Provenance lacks writer identity and trust class.
- Rehydration requires the semantic model for a complete live index.
- Embedding and chunker identities are not fully archived.
- The Memory Pack has no external consumer contract.
- The full public operations still load broad archive data.
- Reranking has a second undocumented model requirement.
- No head-to-head competitor benchmark exists.
- Letta source internals and some MemPalace backend internals remain unverified.

## 32. Explicit Deferrals

Do not add Neo4j, Redis, Kafka, another vector database, autonomous LLM
extraction, A2A, a frontend, GPU infrastructure, hosted SaaS, or an enterprise
authorization system from this audit alone.

Do not make L0 unconditional until the storage and ingestion cost of that
policy is measured and the product decision is explicit.

Do not replace the archive with a graph or a summary store. Build a graph or
derived extraction layer only when a real query workload shows that relational
state and evidence cannot answer it.

Do not build archive export/import until identity, snapshot, and compatibility
rules are specified.

## 33. Final Architectural Position

Mind Palace can become the durable memory substrate underneath AI systems for
corpora whose source material is archived and whose claims are authored with
evidence. That claim is defensible today for the archive-backed path.

It is not yet a universal substrate for arbitrary ingestion, and it is not yet
a complete portable or multi-tenant memory platform. The honest position is
therefore “yes, with explicit boundaries,” not “yes, because it stores memory.”

The durable differentiator is not the presence of embeddings, a graph, or a
chat history. It is the combination of immutable source, authoritative
evidence-backed state, explicit temporal and conflict semantics, replayable
snapshots, and rebuildable derived intelligence.

## 34. Next Concrete Engineering Objective

Make L0 completeness an explicit product decision and measure its cost. Provide
a corpus-level archive policy or a documented authoring requirement so a
developer can tell whether a successful ingest created authoritative memory or
only a live retrieval document.

Then measure a projection-specific archive loader against the existing
canonical results. Keep the current loader until current, history, evidence,
conflicts, snapshots, replay, and packs remain byte-equivalent at production
fixture sizes.
