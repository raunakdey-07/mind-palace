# M010 Performance & Architecture Report

This is a post-release engineering audit report, not a new product milestone or
release. The released `v0.6.0` tag remains the correctness authority for M009.
All measurements below use disposable PostgreSQL schemas or databases unless
stated otherwise.

## Executive Decision

Keep the two bounded changes implemented in this audit:

1. `Embedder` construction is cheap, and the SentenceTransformer import/model
   load happens on first actual embedding use, protected by a single-flight lock.
2. Empty-query public memory projection resolves the archive once instead of
   twice, and conflict detection compares claims grouped by key.

Keep the M009 feed unchanged. Its keyset query, cursor contract, and released
benchmarks are not the bottleneck identified here. The larger remaining cost is
the public memory projection, which still loads and assembles a complete archive
before applying some selectors.

The current image, dependency, and runtime boundary remains the post-v0.6.0
hygiene design. No new queue, vector database, graph, or semantic service was
introduced.

## Current Baseline

The baseline revision was `b2129e7`, the post-hygiene `main` tip. The immutable
release tag remained `v0.6.0` at `033c1484dca53fbb40dbf82904aacf5f2834142a`.

### Startup

The clean-worktree probe and the post-change probe use Python 3.14.7, the
installed CPU dependency set, and fresh subprocesses with model resolution
forced offline where available.

| Probe | Before | After | Change |
|---|---:|---:|---:|
| `api.main` import wall time | 5,534.272 ms | 589.137 ms | 89.4% faster |
| `api.main` process wall time | 6,783.009 ms | 798.901 ms | 88.2% faster |
| Imported modules | 4,221 | 682 | 83.8% fewer |
| Peak RSS | 861,428 KB | 80,696 KB | 90.6% smaller |
| `mindpalace_sdk` import | 63.876 ms | 63.876 ms | unchanged |
| CLI root help | 265.914 ms | 336.643 ms | noise-level variation |

After lazy loading, `api.main` imports without Torch,
`sentence_transformers`, or `transformers`. The public `mindpalace_sdk` import
was already model-free. `import mindpalace` is not a supported Python import in
this repository; the documented module is `mindpalace_sdk`, so the failed probe
is recorded as a naming check, not a product regression.

### Semantic model

The post-change semantic probe records:

```text
Embedder construction:       0.015 ms
first embedding:           5,110.624 ms
warm embedding:               10.388 ms
dimension:                       384
```

The model is loaded on the first semantic use and remains resident in the
process. The lazy boundary removes that cost from health, feed, CLI help, and
DB-only imports without changing semantic query behavior.

## Top Bottlenecks

| Rank | Problem | Measured cost | Root cause | Decision |
|---:|---|---|---|---|
| 1 | Semantic dependency/model initialization on every API process | 5.53s import wall, 4,221 modules, 861 MB RSS | Router globals and `api.services` imported the model stack before request routing | **OPTIMIZE:** lazy import/load and single-flight lock implemented |
| 2 | Empty-query public operations resolve and conflict-check the archive twice | 1,000-version current: 1,831.301 ms before, 722.755 ms after; evidence: 1,698.795 ms before, 655.854 ms after | `project()` called `_query_result` twice; conflict pairs compared all active claims | **OPTIMIZE:** reuse the empty result and group pairs by key |
| 3 | Public memory projection loads the full archive | 1,000-version current still returns about 1.4M characters and performs four statements; one-claim evidence cost nearly the same as full current | `_load()` selects raw content, metadata, chunks, claims, and evidence for every version | **DEFER:** requires a new projection loader and careful conflict-closure tests |
| 4 | Memory Pack canonical budgeting serializes a growing response repeatedly | 1,000-version pack: 937.535 ms before, 927.356 ms after; no statement-count reduction | Every candidate rebuilds and serializes the accumulated response | **DEFER:** exact Unicode budget and atomic conflict/change semantics need a dedicated correctness gate |
| 5 | RRF ranks all eligible chunks before final `k` | Existing retrieval plans process the full eligible branch and sort before limiting | Ranking query shape, not feed code | **DEFER:** candidate-depth changes require retrieval-quality measurements |
| 6 | Repeated corpus resolution in feed | Feed remains about 38 ms at 1,000 versions and uses four statements per measured page | One corpus lookup plus one bounded page query | **KEEP:** no demonstrated feed problem; combining queries would change empty-corpus semantics |

The full-archive loader is the largest remaining opportunity, but it is not a
safe micro-optimization. It must preserve cross-document conflict closure,
provenance, snapshots, and historical semantics.

## Laya-Inspired Findings

The current Laya repository was inspected at commit
`23a17522aa4942da6cce53a995a275760320b691`, tag `v0.3.20`. No Laya code was
copied. The transferable principles are:

- route and validate cheaply before loading an expensive runtime;
- make heavyweight imports and model construction lazy;
- protect shared model initialization with a lifecycle lock;
- keep loaded model/tokenizer state resident after first use;
- batch only compatible work and preserve result order;
- keep optional runtime dependencies optional;
- bind benchmark results to source, environment, workload, and consistency
  checks;
- isolate synchronous inference from unrelated event-loop work when profiling
  justifies it.

Mind Palace already reuses a singleton model and batches `embed()` calls. The
new lock closes the concurrent first-use gap without adding a model registry or
new infrastructure. Laya's specific routing taxonomy, LRU capacities, CUDA
kernels, and decision protocol do not apply to persistent corpus memory.

## Changes Implemented

### Lazy semantic boundary

`api/services/embedder.py` now:

- imports `sentence_transformers` inside first model use;
- leaves `Embedder()` construction model-free;
- loads the model once with `_model_lock`;
- serializes singleton creation with `_instance_lock`;
- preserves the existing `embed`, `dimension`, `version`, and batch behavior.

Router globals can still refer to a lightweight `Embedder` object. The first
semantic request pays the model load.

### Projection and conflict work reuse

`api/services/memory_public.py` reuses the resolved state for an empty query.
`api/services/memory.py` groups active claims by exact key before forming
conflict pairs. Pair ordering, value comparison, cross-document requirements,
and conflict IDs remain unchanged.

### Runtime packaging fix

A fresh Python 3.11 image initially resolved SQLAlchemy without `greenlet`,
which made `api.main` import fail even though the source virtualenv passed.
The runtime manifest now declares `sqlalchemy[asyncio]`, ensuring the async
adapter dependency is installed in a clean image. The rebuilt image imports and
answers liveness/readiness successfully.

### Regression coverage

`tests/test_embedder.py` proves construction does not invoke the model and
first use does, including concurrent single-flight loading.
`tests/test_memory_public.py` proves an empty query invokes the archive resolver
once. Existing conflict, provenance, pack, Unicode, and benchmark tests provide
the semantic correctness gate.

## Before / After Measurements

The controlled 1,000-version run used the same unique-claim fixture and warm
PostgreSQL environment. Values are median wall time over three measured
iterations after one warmup.

| Operation | Before ms | After ms | Change |
|---|---:|---:|---:|
| current | 1,831.301 | 722.755 | 60.5% lower |
| history | 1,769.214 | 670.126 | 62.1% lower |
| changes | 1,762.545 | 674.490 | 61.7% lower |
| evidence | 1,698.795 | 655.854 | 61.4% lower |
| as-of | 1,717.028 | 663.686 | 61.3% lower |
| snapshot | 1,830.774 | 710.233 | 61.2% lower |
| replay | 1,772.593 | 680.227 | 61.6% lower |
| pack | 937.535 | 927.356 | 1.1% lower |
| feed | 38.000 | 39.963 | unchanged within noise |

SQL statement counts did not change: four for ordinary operations, five for
replay, and eleven for snapshot. The improvement is CPU/Python projection work,
not a hidden database-query reduction.

Raw artifacts:

- `docs/performance/startup-baseline.json`
- `docs/performance/startup-after.json`
- `docs/performance/semantic-after.json`
- `docs/performance/operations-baseline-1000.json`
- `docs/performance/operations-after-1000.json`
- `docs/performance/operations-baseline.json`
- `docs/performance/operations-after.json`

The reusable harness is `scripts/performance_probe.py`:

```bash
venvmp/bin/python scripts/performance_probe.py startup
venvmp/bin/python scripts/performance_probe.py semantic
DATABASE_URL=... MIND_PALACE_CURSOR_SECRET=... \
  venvmp/bin/python scripts/performance_probe.py operations
```

A 100,000-version direct DB-only feed fixture was attempted in a disposable
database. It exceeded the 20-minute bound during fixture/query work and was
terminated. The temporary database was removed. No 100k result is claimed; see
`docs/performance/feed-100k-attempt.json`.

## Correctness Results

Final project-runtime regression after the changes:

```text
967 passed, 147 skipped, 10 warnings
```

Focused results included:

- memory public/pack/conflict suite: `51 passed, 47 skipped`;
- embedding suite: `6 passed`;
- semantic/corpus/retrieval focused suite: `50 passed, 7 skipped`;
- M009 adapter/security/health tests remained passing.

The full suite includes the existing Starlette TestClient deprecation and
asyncio-mark warnings. No new warning category was introduced.

M009 evidence under `docs/m009/` was not edited or regenerated. The feed's
released keyset, cursor, concurrency, health, and 10k evidence remain the
correctness baseline.

## Security Results

- Cursor signing, corpus binding, malformed/tampered/wrong-corpus rejection,
  and sanitized database errors were not changed.
- The model lock prevents duplicate first-use initialization without exposing
  model state or credentials.
- No cursor, signature, database URL, or secret is logged by the new path.
- The API image remains non-root (`10001:10001`) and exposes only port 8000.
- The Docker build context excludes Git, virtual environments, tests, research
  modules, reviewer files, evaluation data, and local caches.
- The full regression includes the existing security tests.

## Docker / Runtime Results

The post-hygiene API image was rebuilt after the performance changes:

- base: `python:3.11-slim`;
- size: approximately `1.29 GB`;
- CPU-only Torch: `2.14.0+cpu`;
- runtime user: `10001:10001`;
- port: `8000/tcp`;
- no Git, tests, local virtualenv, CLI, MCP server, research modules, reviewer
  files, or evaluation data in `/app`.

`docker compose build backend` passes. The current image imports the API without
loading Torch. The release-time no-cache build and in-container health checks
also passed in the post-hygiene audit; the performance code does not alter the
image dependency boundary.

The base image remains tag-based rather than digest-pinned. A digest policy is
deferred until the repository has a multi-architecture update process.

## Dependency Boundary

| Dependency group | Packages | Decision |
|---|---|---|
| Core API/database | FastAPI, Uvicorn, SQLAlchemy, asyncpg, Pydantic, HTTPX, PyYAML, frontmatter, Prometheus instrumentation | **KEEP** |
| Semantic extension | sentence-transformers, Torch, Transformers and transitive ML packages | **KEEP**, but load lazily and pin CPU Torch in Docker |
| Legacy API module | `langchain-text-splitters` | **KEEP** for `api.services.chunker` compatibility; it is not on the DB-only path |
| CLI | Typer | **OPTIONAL_RUNTIME** in package metadata; development requirements include it |
| MCP | `mcp` | **OPTIONAL_RUNTIME**; not imported by the API image |
| Migrations/operations | Alembic, psycopg2, pgvector Python adapter | **OPERATIONAL_RUNTIME** for migration workflows |
| Tests/development | pytest, pytest-asyncio, Black, Flake8 | **TEST_ONLY**; excluded from the API image |
| Research/evaluation | benchmark and decision modules plus their datasets | **RESEARCH_ONLY**; excluded from the API image |

The previous hygiene pass removed direct `langchain`, `langchain-community`,
`structlog`, and `python-dotenv`; narrowed Uvicorn to its base package; and
separated runtime from development requirements. This audit found no further
safe dependency removal that preserves the current public package and legacy
API modules.

## Architecture Decision

Keep one package for now, with an internal boundary:

```text
durable core
  database, lifecycle, projections, packs, snapshots, feed
        |
        +-- optional semantic extension
              lazy resident Embedder, retrieval, semantic query
```

The durable core is now model-free at import and startup. The semantic
extension remains available through the same public API, but loads only when
an embedding is actually requested. This is less disruptive than splitting
packages immediately and gives the next team a measurable boundary if needed.

Do not add Redis, a new vector database, a queue, an autonomous graph layer, or
an MCP feed surface based on this audit.

## Deferred Work

- Build a projection-specific archive loader that avoids raw content, metadata,
  and unrelated child rows while preserving conflict closure.
- Replace repeated pack canonical serialization with an incremental exact
  accounting method, guarded by the existing frozen pack tests.
- Replay through a snapshot-version join instead of loading and filtering the
  full archive in Python.
- Bound RRF branch candidates after retrieval-quality measurements.
- Add a complete dependency lock and a deliberate base-image digest policy.
- Add model preload policy only if cold semantic latency becomes a deployment
  requirement; do not make DB-only endpoints pay for it.
- Move CPU inference off the event loop only after a concurrency profile shows
  event-loop blocking is material.
- Establish a supported `mindpalace` Python import alias only if integration
  demand justifies a public packaging change.
- Complete a reproducible 100k feed benchmark with a faster disposable fixture
  loader or a dedicated DB-only benchmark database.

## Recommended Next Milestone

No new milestone is recommended from this audit alone. First land and observe
the two measured changes. The next architecture decision should be a separately
scoped database projection and Memory Pack work, justified by production-shaped
workloads, rather than a new infrastructure or model feature.
