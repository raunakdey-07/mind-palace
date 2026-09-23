# Mind Palace

[![Release](https://img.shields.io/github/v/release/raunakdey-07/mind-palace)](https://github.com/raunakdey-07/mind-palace/releases)
[![License](https://img.shields.io/badge/license-Apache%202.0-blue.svg)](LICENSE)
[![CI](https://github.com/raunakdey-07/mind-palace/actions/workflows/ci.yml/badge.svg)](https://github.com/raunakdey-07/mind-palace/actions/workflows/ci.yml)

[Current release: v0.5.1](docs/release-map.md) · [Changelog](CHANGELOG.md)

**Mind Palace is a persistent, portable memory layer for AI applications that
need persistent, versioned, evidence-backed knowledge over an evolving corpus.**
It turns authored Markdown into versioned source assertions: current knowledge,
history, changes, conflicts, provenance, and reproducible context. It remembers
what sources asserted—not perfect truth.

Memory belongs to the corpus, not a particular model or chat session. PostgreSQL
persists it across application restarts; Python, HTTP, CLI, and MCP expose the
same memory contract. This is **corpus memory for AI**, not human memory,
conversation/preferences storage, a chatbot, or an agent framework. Portability
means model-independent interfaces, not a promised archive export/import tool.

## Project status

**Latest released baseline:** v0.5.0 / M006.75, empirically benchmarked at
**47/60**. The current v0.5.1 engineering handoff packages M007.1 independent
adjudication infrastructure; it does not claim M007 scientific results.

| Phase | Status | Evidence |
|---|---|---|
| M004 persistent memory foundation | Complete | Released |
| M005 developer/API surface | Complete | Released |
| M006 real-world memory evaluation | Complete | Released |
| M006.75 query/relevance evaluation | Complete | 47/60 benchmark |
| M007.1 adjudication infrastructure | Ready | 118-case blinded package |
| M007 scientific evaluation | Pending | Independent adjudication required |
| M008 temporal/longitudinal evaluation | Pending | M007 gate required |

Implemented research infrastructure includes blind review generation,
authoritative traceability, ambiguity preservation, reviewer schema/validation,
DecisionReceipt fingerprints and explanation, replay and structured drift
detection, temporal/snapshot/conflict primitives, synthetic semantic/property
suites, and the executable M007–M008 runner. These are not substitutes for
independent human adjudication or empirical benchmarks.

## Why memory beyond RAG?

Live retrieval asks **“What text is relevant?”** An evolving application also
needs **“What changed, what was current then, and which sources disagree?”**
Replacing live chunks does not itself preserve those relationships. Similarity
scores are not version history or a record of supersession.

Mind Palace keeps live retrieval and explicit assertion memory separate:

- `/search`, `context()`, and default `/api/query/ask` retrieve from the live index.
- Memory operations read an append-only archive of authored claims and evidence.
- An old claim can remain historical after its source is updated or deleted.
- Different active sources can disagree without the latest writer silently winning.
- Snapshots capture version references so later updates do not rewrite the past.

See [Why persistent corpus memory?](examples/WHY_MEMORY.md). No LLM is used to
extract or adjudicate claims; exact source containment establishes provenance,
not truth or semantic entailment.

## Core memory model

```text
corpus → stable source path → observed versions → authored claims → exact evidence
                                  ↓                   ↓
                         lifecycle / snapshots    supersession / conflicts
```

| Concept | Meaning |
|---|---|
| Corpus | Persistent namespace; the same relative path may exist independently in different corpora |
| Document | Stable `(corpus, path)` identity across deletion/restoration; renames are new identities |
| Version | Immutable observed source, metadata, chunks, predecessor, and lifecycle event |
| Claim | Explicit key/value/assertion with optional authored validity bounds |
| Evidence | Exact quote, archived chunk offsets, source hash, and corpus/version/claim links |
| Snapshot | Whole-corpus immutable version references, including history and tombstones |

Lifecycle events are `NEW`, `MODIFIED`, `DELETED`, and `RESTORED`. Identical
re-ingestion is `UNCHANGED` and adds no version. A prose-only edit may create a
version with `memory_changed=false`; it is not necessarily a changed assertion.
Same-document replacements can supersede prior claims. Different-document claims
with the same key and different values form conflicts, not supersession.

The archive foundation is migration `004_memory`; `005_multiple_evidence` adds
multiple distinct evidence references per claim while preserving existing string
behavior and corpus/version foreign keys. [Schema and authoring](examples/MEMORY.md).

## Current, historical, and conflicting assertions

```python
from mindpalace_sdk import MindPalace

mp = MindPalace("my-corpus")
mp.sync("./docs")  # Markdown with explicitly authored claims; uses local embeddings

current = mp.memory.current(query="streaming")
history = mp.memory.history(query="streaming")
print(current.current_memories)
print(current.conflicts)  # alternatives are retained, not silently resolved
print(history.historical_memories)
```

`CURRENT` means latest active source assertion inside its authored validity
window—not independently verified present-day truth. Explicit replacements are
`SUPERSEDED`; inactive/unreplaced or out-of-window claims are `UNCERTAIN`.
`CONFLICTING` alternatives are excluded from the unopposed current list.
History and uncertainty may overlap.

`as_of` is an inclusive **observation** cutoff; `valid_at` evaluates authored
`[valid_from, valid_until)` bounds. Missing bounds remain unknown. Replay fixes
both clocks to the saved snapshot cutoff. Capture returned timestamps rather than
inventing ingestion dates. Existing `current`, `history`, `changes`, and `pack`
lookups retain lexical **AND** matching: `streaming` can match
`architecture.streaming`, while extra question words may eliminate every result.

For natural-language retrieval, use the separate M006.5 query service:

```python
answer_context = mp.memory.query(
    "What carries Dispatch events now?", intent="current", budget=8000
)
print(answer_context.canonical_json())  # evidence-backed context, not generated prose
```

The question goes in `query`; it must be nonempty. `intent` defaults to `auto`
and accepts `auto`, `current`, `historical`, `temporal`, `change`, `conflict`, or
`provenance`. Explicit time selectors take precedence over question dates; a
question's date-only `YYYY-MM-DD` means midnight UTC observation time. Stage names
need a caller-supplied `as_of` or `snapshot_id`. “Before Kafka” selects history,
not a precise inferred event boundary. See [query semantics](examples/MEMORY_API.md#question-retrieval-m0065).

Query reuses the configured sentence-transformer (default `all-MiniLM-L6-v2`),
loaded on demand by this service and reused in-process. Question and archived
claim/text-key-path vectors are recomputed per query, transient, and have no
persistent vector cache or index. A **0.30 cosine floor / 0.90 top-score band**
selects authored keys; relevance propagates within the same authored key, never
establishes authority, and can miss or over-select claims. Persistence still
resolves status, time, provenance, and complete conflict groups before packing.
No M006.5 schema migration is needed.

## M006.75 Memory Evaluation

M006.75 is the current frozen evaluation of persistent/versioned memory query
behavior over an authored, evolving corpus. Its 60 held-out questions cover
current memory, historical memory, temporal queries, multi-topic questions,
conflicts/provenance, and explicit abstention.

| Category | Exact/full |
|---|---:|
| Overall | **47/60** |
| Current | 6/10 |
| Historical | 5/10 |
| Temporal | 10/10 |
| Multi-topic | 9/10 |
| Conflict / provenance | 7/10 |
| Abstention | 10/10 |

Safety is reported separately from exact/full retrieval correctness:

- false-positive retrievals: **0**
- false-current promotions: **0**
- provenance failures: **0**
- conflict failures: **0**
- temporal failures: **0**
- budget failures: **0**
- execution failures: **0**

The result demonstrates that semantic retrieval is not the same as memory
authority: the benchmark preserves temporal state, provenance, conflict
closure, bounded output, and explicit abstention independently of similarity.
It does not claim that Mind Palace is generally better than RAG, other memory
systems, Jev, or Jev-Mem, and it is not a production-readiness certification.

On this frozen 60-question corpus, the result is **47/60 exact/full**.
The corrected release source tree has canonical result hash
`881530c7c6e7ba29fb38eb5b27609071463f733c6a8cd173aeff04173a5d8dc8`.
The bounded Memory Pack correction changed the source fingerprint and hash,
but repeated runs produced the same per-question decisions, category results,
and safety invariants. See the [benchmark README](eval/m00675/README.md), the
[reproducibility protocol](docs/evaluation/m00675-reproducibility.md), the
[manifest](eval/m00675/manifest.json), the
[canonical result](eval/m00675/result.json), and the
[runner](scripts/benchmark/m00675.sh).

## Documentation and interfaces

- [Architecture and memory model](examples/MEMORY.md)
- [API and developer interfaces](examples/MEMORY_API.md)
- [Evaluation reports](docs/evaluation/)
- [M006.75 reproducibility](docs/evaluation/m00675-reproducibility.md)
- [M006.75 benchmark artifact](eval/m00675/README.md)

## Evidence that survives updates

Every returned claim has exact archived evidence and source attribution. Quotes
are not inferred summaries; offsets must match archived chunk slices. Deleting a
source removes its live index entry but retains a tombstone, history, and proof.

The A–G fixture's `data/storage.md` authors **one claim with two supporting
quotes in two chunks**. Migration 004's one-reference constraint could not
represent this; migration 005 accepts a nonempty list of distinct exact quotes
as well as the original string. Both supporting chunks must contain the claim
text. Evidence is ingested atomically with the version. Snapshotted versions
reject later evidence INSERTs, and downgrade refuses claims with more than one
reference rather than discarding provenance.

Corpus content—including hostile-looking text—is **data, not instructions**.
Attribution does not prove source truthfulness or make downstream LLMs immune to
prompt injection.

## Reproducible, bounded memory packs

```python
saved = mp.memory.snapshot()
pack = mp.memory.pack(query="streaming", snapshot_id=saved.snapshot.id, budget=8000)
assert len(pack.canonical_json()) <= 8000
print(pack.canonical_json())
```

The budget counts **Unicode characters of the complete canonical JSON envelope**,
not tokens or UTF-8 bytes. It includes state, claims, evidence, sources, conflicts,
and truncation metadata. Valid budgets are 512–128000 (default 8000), but even an
in-range budget can fail if the empty envelope does not fit.

Claims retain all their evidence; conflict alternatives stay together or are
omitted together. Quotes are not sliced. `truncated=true` warns that omissions
are not evidence of absence. Sources derive only from retained evidence.

Given the **same frozen state, validity cutoff, selectors, and budget**, canonical
lexical packs are identical. Query packs also require the same question, intent,
embedding model, and runtime for deterministic selection; no cross-model/runtime
byte-identity is promised. Independent wall-clock calls are not promised byte-identical:
the default validity clock and live state can change. A small pack budget does
not bound archive-read cost; history loading is currently unbounded.

## Interfaces

SDK, REST, CLI, and MCP delegate memory policy to one typed public boundary.
See [MEMORY_API.md](examples/MEMORY_API.md) for selectors, transaction ownership,
errors, budgets, and adapter limitations. Memory reads do not create missing corpora.

### Python SDK (`mindpalace_sdk.py`)

```python
from mindpalace_sdk import MindPalace

mp = MindPalace("corpus-name")       # named local client; initializes embeddings
summary = mp.sync("./docs")
results = mp.search("query", k=5)
context = mp.context("question", budget_tokens=4000)

reader = MindPalace()               # no embeddings at construction; query loads on demand
print(reader.memory.current(corpus="corpus-name", query="streaming").canonical_json())
```

Remote SDK memory calls use HTTP. Remote `sync`, `search`, and `context` are not
supported. Async local applications should use the async public service rather
than call the synchronous local SDK inside an active event loop.

### REST API

```text
POST   /api/corpora                  create a corpus
GET    /api/corpora                  list corpora
GET    /api/corpora/{name}           inspect one corpus
DELETE /api/corpora/{name}           blocked when archive rows exist
POST   /api/corpora/{name}/sync      sync with a directory
GET    /api/search?q=&k=&hybrid&rrf&rerank   raw search
GET    /api/context?q=&corpus=&budget_tokens=  live context pack
POST   /api/query/ask                default live RAG; explicit memory mode available
POST   /api/memory/query             question retrieval with intent and bounded evidence
POST   /api/memory/current           current assertions and conflicts
POST   /api/memory/history           assertion history
POST   /api/memory/changes           before/after events
POST   /api/memory/evidence          evidence for a claim
POST   /api/memory/as-of             assertions at an aware observation cutoff
POST   /api/memory/snapshot          commit whole-corpus immutable references
POST   /api/memory/replay            replay a saved snapshot
POST   /api/memory/pack              complete JSON bounded in Unicode characters
```

Memory reads are POST requests with a JSON `corpus` and selectors. Backend
unavailability is an error, not an empty success. Default `/api/query/ask` is
live RAG; explicit `mode="memory"` sends its `question` through memory `query`
with automatic intent, then invokes memory-aware generation with the configured LLM. Neither mode's generated-answer quality is established by the
memory semantics gate.

### MCP server

```json
{
  "mcpServers": {
    "mind-palace": {
      "command": "python",
      "args": ["-m", "mcp_server"],
      "env": {"DATABASE_URL": "postgresql://..."}
    }
  }
}
```

Tools: `context`, `search`, `sync`, `list_corpora`, plus `memory_current`,
`memory_history`, `memory_changes`, `memory_evidence`, `memory_as_of`,
`memory_snapshot`, `memory_replay`, `memory_pack`, and `memory_query`.

### CLI

```bash
python -m cli.main memory current --corpus my-corpus --query streaming
python -m cli.main memory query --corpus my-corpus \
  --query "What carries Dispatch events now?" --intent current --budget 8000
```

The `memory` group is remote-only at `http://127.0.0.1:8000` (override with
`--base-url`). All nine operations have commands. The separate `eval memory`
command below runs locally against PostgreSQL without a REST server.

## Quick Start

### 1. Install and start infrastructure

```bash
git clone https://github.com/raunakdey-07/mind-palace.git
cd mind-palace
python -m venv .venv
. .venv/bin/activate
pip install -r requirements.txt
pip install -e .
docker compose up -d postgresql          # or podman-compose up -d postgresql
export DATABASE_URL=postgresql://mpadmin:secret@localhost:5432/mindpalace
```

Local defaults target disposable containers only. PostgreSQL and the required
extensions are necessary even for the network-free fixture demo. Port 5433 in
the demo commands below is the separately available local validation database;
use your actual database port.

### 2. Migrate your persistent database

```bash
python -m alembic -c migrations/alembic.ini upgrade head
```

Migrations provision pgvector/pgcrypto/pg_trgm. The rolled-back evaluation harness
instead applies migrations inside its own temporary schema; it does not migrate
your live schema.

### 3. Sync documents and retrieve live context

```bash
python - <<'EOF'
from mindpalace_sdk import MindPalace

mp = MindPalace("my-first-corpus")
summary = mp.sync("examples/docs")
print(summary)                # added/changed/unchanged/deleted counts
pack = mp.context("How does authentication work?", budget_tokens=2000)
print(pack.context)
for source in pack.sources:
    print(f"  source: {source.title} ({source.path})")
EOF
```

Normal sync uses sentence-transformer embeddings (`all-MiniLM-L6-v2` by default).
Weights may need an initial Hugging Face download; cached weights can run with
`HF_HUB_OFFLINE=1`. No paid LLM is needed for ingestion or memory reads.
`context()` is token-estimated live retrieval; `memory.pack()` is character-bounded
archived assertion memory. Their budget units are not interchangeable.

## Run the real evolving-corpus demo

```bash
DATABASE_URL=postgresql://mpadmin:secret@localhost:5433/mindpalace \
  HF_HUB_OFFLINE=1 python examples/memory_evaluation_demo.py
```

The standalone A–G demo loads the checked-in
[Dispatch corpus](examples/evaluation/corpus/) through the shared benchmark
workload context and checks all [39 authored cases](eval/memory_benchmarks.yaml).
It prints current/history, changes, conflicts, evidence, snapshots, and budgets:

- Redis Streams → Kafka; JWT → OIDC; Kafka partitions 6 → 12.
- A prose-only edit changes a document but not its claim mapping.
- A stale runbook introduces a conflict, deletion removes it from current, and
  identical restoration reintroduces the same semantic pair.
- One storage claim keeps two exact references; hostile source text stays data.
- A–G snapshot replay bytes remain identical after later updates and deletions.
- Frozen-state packs at 1000/2000/4000/8000/16000 characters check safety and
  honest truncation, not guessed capacity-dependent recall.

**Network-free by default means no model/provider calls or downloads.** Embeddings
are artificial deterministic fixtures, explicitly **not retrieval-quality evidence**.
A PostgreSQL connection is still required. No API server or LLM is used. The random
schema, migrations, corpus, and snapshots live in one rolled-back transaction;
no persistent corpus or output artifact remains. Default workload timeout is
120 seconds (`--timeout-seconds` accepts 1–600; rollback cleanup is additional).

The demo was run successfully on port 5433 with `HF_HUB_OFFLINE=1`: **39 cases
passed**, with final **17 versions, 16 claims, 17 evidence references, 7 stage
snapshots, 11 live documents, and 1 conflict group**. This is a fixture semantics
run, not performance or generated-answer validation. [Run record](examples/MEMORY.md).

The older **persistent SDK demo** remains available:

```bash
python examples/memory_demo.py
```

It creates a unique dedicated `m005-demo-<uuid>` corpus and records SDK JSON under
`examples/evolving-project/runs/<corpus>/`. It exercises Redis Streams → Kafka →
Kafka managed → deletion → restoration and a separate two-source auth conflict.
Use `--corpus m005-dispatch-my-first-run` only for a new name; all existing names
are refused. It uses real embeddings and deliberately leaves committed data and
artifacts, even on partial failure. See [setup and walkthrough](examples/MEMORY.md)
and [the older authored fixtures](examples/evolving-project/README.md).

## Memory evaluation and results

From the repository root, with the database URL set:

```bash
mkdir -p eval/results
HF_HUB_OFFLINE=1 python -m cli.main eval memory --repetitions 1 \
  --save eval/results/memory-fixture-smoke.json
```

The report path must be new; `--save` refuses overwrite and does not create its
parent. Omit it for text output only. This command uses artificial fixture
embeddings and no LLM. One repetition is a functional smoke test, not percentiles.

Manual measurement with **real, already cached** embeddings:

```bash
HF_HUB_OFFLINE=1 python -m cli.main eval memory --embeddings cached \
  --repetitions 20 --sizes 100,500,1000 \
  --save eval/results/memory-cached-manual.json
```

Cached mode loads the existing model offline, requires 384 dimensions, and fails
if the weights are absent—no download or fixture fallback. Scaling sizes are
separate synthetic one-claim/document workloads, bounded by a supplied sum of
5000. There is no default scaling sweep. **5000 is a safety cap, not a measured
result.** p50/p95 require at least 20 samples after an untimed warm-up; timings
include savepoints, exclude setup/query embedding, and retain samples. Snapshot
timings measure fresh INSERTs rolled back per call, not deduplication.

### Read the results without conflating the metrics

| Recorded cached-run observation | What it actually says |
|---|---|
| **39/39 tailored lexical cases passed** | Authored memory semantics and safety, not general question understanding |
| **0/24 nonempty current-claim sets correct using the natural-language questions** | Material lexical lookup weakness; outside the labeled gate, not hidden by empty-set wins |
| **79.17% live-baseline text coverage** | Literal expected-text recoverability, **not semantic accuracy** or temporal framing |
| **Generation A/B: NOT RUN** | No LLM service available; no generated-answer quality result |

These cached results are distinct from the fixture demo validated above. Current
labels are exact sets; other nonempty list labels generally assert presence;
missing labels are unscored and explicit empty lists assert emptiness. The gate
checks labeled cases, replay, all pack safety, and operation/scaling execution.
Low-budget recall losses are diagnostic, not automatic failure. The live hybrid+RRF
baseline has no claim/status model. [Precise evaluator semantics](examples/MEMORY_API.md#reusable-evaluation-workload-and-cli-semantics).

An opt-in `--generation` adapter exists for A: a memory-pack prompt through
`LLMService`, and B: the **actual default `/api/query/ask` route** at each live
stage (cached embeddings required). Both need explicit `LLM_PROVIDER`; provider
credentials, data sharing, and costs apply. Run standalone, never in a process
serving traffic: B temporarily binds route globals. Literal answer checks are
not semantic judges or proof of injection resistance.

The maintainer-authored [M006 measured report](docs/evaluation/m006.md) is the
place for actual environment, timings, and further results; this docs/demo update
does not create it or invent unknown scores. **Unrun generation means M006 is
not declared complete. No deployment certification is implied.**

### M006.5 question retrieval: implemented and evaluated

The new service improves the unchanged original natural-language diagnostic from
**0/24 lexical to 17/24 exact current sets**. On **40 added questions**, **30/40**
match all labeled sets exactly: historical **4/4**, temporal **3/6**, conflict
**4/4**, provenance **3/3** (current **13/19**, change **3/4**). There are still
**17 exact-set failures** across the 64 questions; labels were not changed.

There were **0 safety failures across 264 output cases** (64 full-budget outputs
plus 200 budget-sweep outputs), and no execution failures. Budget correctness is
not retrieval completeness: even a correctly bounded, fully attributed pack can
omit relevant claims or include irrelevant ones. `passed` gates safety/execution,
not exact relevance. Generation remains **NOT RUN**.

One warmed fixed query over 20 repetitions measured **124.2/131.3 ms p50/p95**;
preembedded live search measured **4.0/5.1 ms**. Query includes per-call embedding
and archive projection; live search excludes query embedding. This is **not a
fair end-to-end latency comparison**, nor a general latency guarantee.
[Full M006.5 report, all failures, and reproduction](docs/evaluation/m0065.md).
The historical [M006 report](docs/evaluation/m006.md) is preserved, not superseded
by a claim that generation or production validation is complete.

## Known limitations

- Existing lexical operations remain lexical AND. M006.5 adds heuristic semantic
  question retrieval, not universally reliable question answering or automatic
  claim extraction. Related concepts, multi-part questions, and abstention remain
  relevance limits.
- Public memory reads load full corpus history before projection/packing, without
  pagination. Output budgets do not bound memory/SQL cost. Scaling is not proven.
- Conflicts require same-key differing values across active documents; no general
  contradiction detection, independent truth verification, or semantic entailment.
- `/ask` grounding and citation instructions are prompt-based, not formally verified;
  memory semantics results do not establish generated-answer quality.
- Markdown-only source support today; plain text/code are planned.
- Sync is per-document atomic, not an atomic whole-directory snapshot. Deletions
  require an explicit sync; renames are new identities.
- **No built-in authentication. Namespaces are not authorization.** Archive
  retention/erasure is unresolved; tombstones retain evidence, and append-only
  guards block archived-corpus deletion. Owners are not a tamper-proof boundary.
- Retrieval strategy differences remain within statistical noise; reranking's
  roughly 1.7-second latency remains opt-in (separate findings below).

## Existing live retrieval pipeline

The retrieval pipeline and benchmark remain useful independently of memory.

```text
create corpus → sync/ingest → inspect → search → pack context → AI application
```

Search ranks relevance; context packing decides what fits a token-estimated
prompt budget. Corpus-scoped retrieval/ingestion keeps documents independent
across namespaces, but does not authenticate callers. Default legacy RAG `/ask`
is not a corpus-scoped memory operation.

### Synchronization

| State | Action |
|---|---|
| added | New files are chunked, embedded, indexed |
| changed | Files are reprocessed; stale live chunks replaced |
| unchanged | Skipped via content-hash manifest |
| deleted | Removed from live index; archived sources receive tombstones |

A failed document does not leave a false successfully-indexed state.

### Live context packing

`context()` returns a structured pack (illustrative envelope, not a measured result):

```json
{
  "query": "...",
  "context": "...bounded evidence text...",
  "sources": [{"title": "...", "path": "...", "doc_id": "..."}],
  "chunks": [{"text": "...", "score": 0.87, "rank": 1, "source": {}}],
  "token_estimate": 1234,
  "strategy": "hybrid_rrf",
  "truncated": false
}
```

Strongest ranked evidence is kept first; overflow drops from the bottom. Sources
derive from retained evidence. This is separate from memory's exact complete-JSON
character bound.

### Retrieval architecture and prior findings

```text
Markdown → parse (frontmatter + heading paths) → chunk (structure-aware)
        → embed (all-MiniLM-L6-v2, 384-dim, normalized)
        → PostgreSQL + pgvector (HNSW)
        → hybrid retrieval (vector + pg_trgm keyword)
        → Reciprocal Rank Fusion
        → optional cross-encoder reranking (opt-in)
        → context packing (budgeted, attributed)
```

**Default strategy: hybrid + RRF.** Measured on a 202-document benchmark,
all non-reranked strategies are statistically indistinguishable on Recall@3
(paired bootstrap, 95% CI). RRF is preferred because rank fusion needs no
score calibration as the corpus evolves. Vector-only (~6 ms) is an equivalent-
quality fast path. Reranking shows point-estimate gains but costs ~1.7 s per
query—opt-in only.

The separate retrieval evaluation has **202 documents and 98 queries across 14
categories**, including lexical, semantic, terminology mismatch, negative,
metadata-filtered, similar-title, section-level, and multi-document cases. Ground
truth is hand-labeled and mechanically validated against corpus metadata;
strategy comparisons use paired bootstrap confidence intervals.

```bash
python -m cli.main eval strategies --candidates 10,20,50 --details
```

See [eval/EVALUATION.md](eval/EVALUATION.md) for methodology, intervals, failure
analysis, and superseded-results history. These findings validate regression
safety and expose failure classes; they do not prove superiority over other
retrieval systems or memory quality.

## How Mind Palace compares

| Category | What it gives you | What you still have to build | Mind Palace's difference |
|---|---|---|---|
| Vector databases (pgvector, Pinecone, Qdrant) | Storage + similarity search | Parsing, chunking, sync, context assembly, attribution | Owns the corpus-to-context pipeline on pgvector |
| RAG frameworks (LangChain, LlamaIndex) | Composable retrieval abstractions | Your evaluation, gates, provenance policy | Ingestion-to-context plus explicit assertion history |
| Agent memory (mem0, Zep) | Conversation/episodic memory | Corpus ingestion | Evolving corpus assertions, not conversation experience |
| MCP memory servers | Protocol transport | Storage and memory policy | Retrieval and memory tools with budgets and provenance |
| Document search | Keyword/lexical matching | Embeddings, context assembly, budgets | Hybrid live retrieval alongside archived memory |

Mind Palace is not a database or an agent framework. It is a focused corpus-memory
layer between authored knowledge and AI consumers. The retrieval benchmark,
`tests/test_api_contract.py`, and evolving-corpus assertions validate different
contracts; none establishes perfect truth or production readiness.

## Security posture

> **Corpus content is data, not instructions.**

Do not promote retrieved text into trusted system instructions. Mind Palace
attributes evidence; consuming applications must enforce their own trust boundary.
Authentication is not implemented: restrict REST and MCP to trusted callers and
add authentication/authorization before exposing sensitive data. Namespace SQL
scoping is not an access-control system.

Database credentials come from `DATABASE_URL`; example defaults are for disposable
local containers. Archive tables are append-only under ordinary DML, not tamper-proof
against the database owner. Deleting source files or calling corpus deletion is
not an archive-retention/erasure solution.

## Project structure

```text
api/
├── routers/               # REST corpora, ingest, search, query, context, memory
├── models/                # Pydantic retrieval and memory contracts
└── services/
    ├── corpora.py         # corpus namespaces
    ├── ingestion.py       # sync/reconciliation pipeline
    ├── parser.py          # frontmatter + heading-path extraction
    ├── embedder.py        # sentence-transformers wrapper
    ├── retrieval.py       # vector/hybrid/RRF/reranked search
    ├── reranker.py        # cross-encoder (opt-in)
    ├── context_packer.py  # live context packs
    ├── memory.py          # versioned assertion archive
    ├── memory_public.py   # shared public projection and packing
    ├── memory_benchmark.py # rolled-back semantics evaluation
    ├── evaluation.py     # Recall/Precision/MRR/nDCG
    ├── benchmark.py      # retrieval strategy comparison
    └── confidence.py     # bootstrap CIs, paired differences

mindpalace_sdk.py          # Python SDK
mcp_server.py              # MCP integration
cli/                       # Typer CLI
content_eval/              # 202-doc retrieval corpus
eval/                      # retrieval and memory benchmark manifests
migrations/                # Alembic migrations and extensions
tests/                     # unit, contract, integration, migration tests
examples/                  # docs, persistent demo, rolled-back A–G demo
```

## Development

```bash
pytest -q                       # DB-backed tests require configured PostgreSQL
DATABASE_URL=... pytest -q      # full suite incl. integration tests
make migrate
black --check api cli tests migrations --line-length 100
flake8 api cli tests migrations --max-line-length=100
python -m cli.main eval strategies
```

## Contributing

Contributions welcome. Please open an issue before large architectural changes.
Retrieval changes must include benchmark evidence.

## License

Apache License 2.0. See [LICENSE](LICENSE) and [NOTICE](NOTICE).
