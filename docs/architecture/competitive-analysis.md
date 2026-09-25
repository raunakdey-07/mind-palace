# Competitive analysis

This document records the comparison work that has actually been done, the
pinned revisions it was done against, and the parts that are not yet done. It is
an audit record, not a positioning claim. No head-to-head benchmark against any
other system has been run in this repository, so no entry below asserts that
Mind Palace is faster, more accurate, or better on any task.

## Scope of the evidence

Four external systems were reviewed. Three have pinned source revisions. Letta
was reviewed through its current official documentation, but its source
revision was not pinned in this audit.

| System | Review reference | What was reviewed |
|---|---|---|
| Graphiti | commit `47f648213da99aa96ec190c61dbb3e0d163e1250`, `graphiti-core` 0.30.2 | Temporal graph model, provenance, validity, retrieval, incremental updates, and MCP surface |
| Mem0 (OSS) | commit `8d6c001966573786d36908bfe4dfd52935749200`, `mem0ai` 2.2.0 | Memory creation and inference, metadata, scopes, expiration, history, search, SDK, and self-hosting boundary |
| Laya | commit `23a17522aa4942da6cce53a995a275760320b691`, tag `v0.3.20` | Lazy loading, routing, resident state, batching, hooks, and benchmark discipline |
| Letta / MemFS | official memory and memory-block documentation, accessed 2026-09-25; source commit not pinned | Stateful agents, persisted messages, always-visible memory blocks, shared blocks, identity, and self-hosting |

The references fix the comparison boundary. They do not establish that any
system is faster or more accurate than another. The matrix below records
capabilities and architectural differences, not scores or a ranking.

The matrix uses only the capabilities described in the reviewed material. A
blanket claim about a system's internals would go beyond that evidence.

## What the repository claims today

`README.md` carries a five-row category table comparing Mind Palace against
vector databases, RAG frameworks, agent memory, MCP memory servers, and
document search. The row that touches this document is the agent-memory row,
which places mem0 in the conversation and episodic memory category and
characterizes Mind Palace's difference as "Evolving corpus assertions, not
conversation experience". That sentence is a claim in `README.md`, not a
finding recorded from the pinned Mem0 review, and the two should not be
conflated.

`Milestone.md`, section 23, set the standing instruction for this kind of
comparison: do not pretend competitors do not exist, do not manufacture
differentiation through marketing language, and validate the claim after
implementation rather than asserting it. The pin table above is the minimum
needed to satisfy that instruction honestly.

## Laya findings that are recorded

`docs/performance/REPORT.md` states that no Laya code was copied, and records
the principles judged transferable: route and validate cheaply before loading an
expensive runtime, make heavyweight imports and model construction lazy,
protect shared model initialization with a lifecycle lock, keep loaded model and
tokenizer state resident after first use, batch only compatible work while
preserving result order, keep optional runtime dependencies optional, bind
benchmark results to source, environment, workload, and consistency checks, and
isolate synchronous inference from unrelated event-loop work when profiling
justifies it.

Five of those produced a code change in this repository, in
`api/services/embedder.py` and in the image and dependency boundary:

- `Embedder()` construction is now model-free; `sentence_transformers` is
  imported inside first use.
- `_model_lock` serializes first model load, and `_instance_lock` serializes
  singleton creation, closing a concurrent first-use gap.
- The model stays resident after first use.
- The API image pins CPU-only Torch and runs as UID 10001.
- Benchmarks are recorded with commit, tag, platform, Python version, and
  dependency versions in `docs/performance/*.json`.

The same section records what was judged not to apply: Laya's routing taxonomy,
its LRU capacities, its CUDA kernels, and its decision protocol do not map onto
persistent corpus memory.

The measured effect is in `docs/performance/REPORT.md`: `api.main` import wall
time fell from 5,534.272 ms to 589.137 ms, imported modules from 4,221 to 682,
and peak RSS from 861,428 KB to 80,696 KB. The first embedding still costs
5,110.624 ms, which is now paid on first semantic use rather than at import.

## Capability matrix

`Yes` means the reviewed material describes the capability. `Partial` means the
capability exists in a different form or with a narrower contract. `Not
established` means this audit did not verify the capability. The table is not a
quality ranking.

| Capability | Mind Palace | Graphiti | Mem0 OSS | Letta / MemFS | Conventional RAG or vector search |
|---|---|---|---|---|---|
| Immutable source versions | Yes | Not established as an exact source-document ledger | Partial, per-memory history | Partial, persisted messages and mutable blocks | Usually not |
| Authored assertions | Yes | Partial, extracted graph facts | Partial, inferred memories | Partial, agent-managed blocks | Not usually |
| Exact evidence quote and offsets | Yes | Not established | Not established | Not established | Usually citation metadata only |
| Observation time and authored validity | Yes, separate clocks | Yes, temporal graph validity and episode time | Partial, expiration and history | Partial, message and block history | Usually not |
| Conflict representation | Yes, explicit conflict groups | Partial, temporal facts and updates | Partial, updates and history | Partial, last-write-wins block updates | Usually not |
| Deletion and restoration | Yes, explicit lifecycle | Not established as this lifecycle | Partial, update and delete operations | Partial, block and message deletion | Usually not |
| Snapshots and replay | Yes | Partial, historical temporal queries | Partial, per-memory history | Partial, persisted messages | Usually not |
| Deterministic memory retrieval | Yes, authored policy and stable projections | No claim made | No claim made | No claim made | Strategy-dependent |
| Semantic retrieval | Yes, lazy resident extension | Yes, hybrid retrieval | Yes | Agent context and tools, not the same contract | Usually yes |
| Graph retrieval | Not a built-in product capability | Yes | Not established for the reviewed OSS scope | Not established | Rarely |
| Machine-readable bounded Memory Pack | Yes | Not established | Not established | Not established | Not usually |
| Portable archive export and import | Not implemented | Not established | Storage abstraction exists; archive interchange not established | Persistence and cloud portability exist; archive interchange not established | Usually deployment-specific |
| Shared multi-agent state | Corpus namespace, without ACL | Not established | Scoped memory identities | Shared memory blocks | Application-specific |
| Streaming or durable feed | Yes, corpus-scoped keyset feed | Not established | Not established | Not established | Usually not |
| Ordinary interfaces | REST, SDK, CLI, MCP | MCP and library interfaces | SDK and hosted or self-hosted service | SDK, API, desktop, and channels | Library and service interfaces |
| Auditability emphasis | Exact lineage and replay | Temporal provenance | Memory history and metadata | Persisted agent state | Usually retrieval logs or citations |
| Reproducible project benchmark | Yes, frozen artifacts and harness | Not compared here | Not compared here | Not compared here | Strategy benchmarks vary |

The important distinction is authority. Mind Palace's source and claim layers
are authoritative and inspectable. Graphiti's graph, Mem0's inferred memories,
and Letta's agent-managed blocks solve related coordination problems, but the
reviewed material does not show the same exact quote/offset ledger and
replay contract. That difference is architectural, not a claim about quality.

## Where Mind Palace actually differs, and how that is backed

Every row below is a repository artifact. None of them is a comparison against
a named competitor, because no comparison run exists.

| Property | Where it is enforced | Evidence |
|---|---|---|
| Claims are authored, never model-extracted | Exact quote containment in `_prepare`, with the message that semantic inference is not supported | `api/services/memory.py`, `tests/test_ingestion_lifecycle.py` |
| Every returned claim has exact evidence | Database trigger plus deferred commit-time constraint trigger | `migrations/versions/004_memory.py` |
| Archive is append-only | Statement-level trigger on the seven archive tables created by that migration | `migrations/versions/004_memory.py` |
| Supersession is same-document and acyclic | Trigger requiring an immediate-predecessor claim in the same document | `migrations/versions/004_memory.py` |
| Conflicts are retained, not resolved | Conflict closure in `project` and `bounded_pack` | `api/services/memory_public.py` |
| Observation time is separate from authored validity | `observed_at` versus `valid_from`/`valid_until`, evaluated as `[valid_from, valid_until)` | [`temporal-semantics.md`](temporal-semantics.md) |
| Output is bounded in Unicode characters of the complete canonical JSON | `bounded_pack` | `api/services/memory_public.py`, `tests/test_context_budget.py` |
| A durable corpus-scoped operational feed | Keyset ordering on `(observed_at, version_id)`, HMAC corpus-bound cursor | `api/services/memory_feed.py`, [`docs/operations.md`](../operations.md) |
| The semantic runtime is an optional extension | Lazy import and load behind a single-flight lock | `api/services/embedder.py` |

## What the frozen benchmark does and does not show

M006.75 is 47/60 exact/full on its frozen 60-question held-out corpus
(`eval/m00675/result.json`, canonical result
`881530c7c6e7ba29fb38eb5b27609071463f733c6a8cd173aeff04173a5d8dc8`), with zero
false-current promotions, zero provenance failures, and zero execution failures
over 60 cases. `README.md` states plainly that this does not claim superiority
over RAG, other memory systems, Jev, or Jev-Mem, and it is not a
production-readiness certification.

For live retrieval, `eval/EVALUATION.md` records 98 hand-labelled queries over a
202-document corpus where every paired-difference interval for Recall@3 contains
zero, so no strategy is statistically distinguishable from another at that
sample size. That finding is about Mind Palace's own strategies. It says
nothing about any other system.

M007 and M008 are blocked. The blind review package has 118 items and zero
adjudicated decisions, and `eval/m007_m008/latest-program-report.json` records
`DATA_BLOCKED`. No accuracy, agreement, calibration, or comparative-improvement
claim for those programs exists, and none can be made from this repository.

## Letta and MemFS findings

The current Letta documentation describes a stateful-agent runtime rather than
a source-document memory ledger. Agents have a system prompt, memory blocks,
messages, tools, and runs. Messages remain stored after context eviction.
Memory blocks are structured, bounded context sections. They can be attached to
one or many agents, and a read-only block gives multiple agents a shared policy
surface.

This design offers useful ideas for Mind Palace: keep memory independent of an
individual agent, make shared state explicit, and let an operator inspect what
is pinned into an agent's context. It also differs in authority. Letta blocks
are mutable context values, and the documentation warns that concurrent updates
are full replacements with last-write-wins behavior. Mind Palace instead stores
immutable source versions, authored claims, exact evidence, and explicit
conflict state. A future export or shared-corpus design can learn from Letta
without copying its mutable block model.

The reviewed Letta documentation did not establish a git-backed MemFS
implementation, versioned Markdown corpus, consolidation contract, or exact
snapshot/replay API. Those points remain deferred rather than inferred.

The candidate comparison dimensions are the same ones the memory model already
distinguishes: whether a system can express authored claims with exact evidence,
retain superseded and conflicting assertions, answer a historical question
without a live index, replay a captured state, and bound its output. Running
that comparison fairly would need each candidate configured against the same
corpus with an agreed labelling protocol, and the repository has no such
protocol for an external system. The M007 review infrastructure was built for
this kind of adjudication and is still waiting on two independent reviewers.

Producing a comparison table with numbers from memory would be worse than having
no table. The capability matrix records observed architecture. A head-to-head
benchmark still needs a shared corpus, agreed labels, and a reproducible runner
for each system.
