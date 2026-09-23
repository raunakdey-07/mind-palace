# Public memory API (schema version 1)

The implemented contract is `api/models/memory.py`, with operation validation,
transaction ownership, projection, and packing in `api/services/memory_public.py`.
REST (`api/routers/memory.py`), SDK (`mindpalace_sdk.py`), MCP (`mcp_server.py`),
and CLI (`cli/memory.py`) delegate to that boundary. This is persistent,
evidence-backed **source assertion memory**, not perfect truth or semantic
contradiction detection. See [the evolving demo](MEMORY.md).

## Operations and selectors

All REST operations below are **POST** with a JSON request, including reads.
All require `corpus`; `query` defaults to `""` (all memories) except that the
`query` operation requires a nonempty question in that field. Names must match
`[A-Za-z0-9._-]+`, length 1–128. Memory reads do not create missing corpora.

| REST path | Meaning | Additional accepted selectors |
|---|---|---|
| `/api/memory/query` | Question retrieval with bounded evidence | **nonempty `query` required**, `intent`, `path`, `as_of`, `valid_at`, `snapshot_id`, `budget` |
| `/api/memory/current` | Current claims and relevant conflicts | `path`, `valid_at` |
| `/api/memory/history` | Current, historical, uncertain claims, conflicts, and changes | `path`, `as_of`, `valid_at` |
| `/api/memory/changes` | Before/after claim sets per version event, plus relevant conflicts | `path`, `as_of`, `valid_at` |
| `/api/memory/evidence` | Claim with its supporting evidence; conflicting sides remain together | **`claim_id` required**, `as_of`, `valid_at` |
| `/api/memory/as-of` | Current claims/conflicts at observation cutoff | **`as_of` required**, `path`, `valid_at` |
| `/api/memory/snapshot` | Commit immutable whole-corpus version references | `as_of` only; `query` must be empty |
| `/api/memory/replay` | Replay saved references, including history | **`snapshot_id` required**, `path` |
| `/api/memory/pack` | Bounded evidence-backed selection | `path`, `as_of`, `valid_at`, `snapshot_id`, `budget` |

Non-default unsupported selectors are rejected, even if an adapter exposes them.
In particular, **evidence rejects `path`**, and **snapshot rejects nonempty
`query`, `path`, and `valid_at`**. `snapshot_id` cannot be combined with `as_of`
or `valid_at`. Claim and snapshot IDs are 64 lowercase hexadecimal characters.
`path` is an exact corpus-relative source path, not a filesystem read request.
`query` is at most 2,000 characters; whitespace-only queries are invalid.

### Matching and time

- Existing lookup operations (not the new `query` operation) retain case-folded
  lexical **AND** over word tokens in claim key, claim
  text, canonical value, and source path. It is not vector search or an LLM.
  `query="streaming"` matches all Dispatch architecture versions because they
  share `architecture.streaming`. Natural-language questions are **not guaranteed**
  to retrieve the intended claim; extra words can eliminate every match.
- `as_of` selects observations inclusively (`observed_at <= as_of`). Request
  timestamps must be timezone-aware, e.g. `2026-09-17T12:00:00+00:00` or a `Z`
  timestamp. Naive datetimes are rejected. Capture actual returned observation
  times or snapshot cutoffs rather than inventing historical ingestion times.
- `valid_at` evaluates the author's validity window separately from observation
  time. The window is `[valid_from, valid_until)`; missing bounds remain unknown.
  Without an explicit validity selector, validity uses `as_of` if supplied,
  otherwise one fixed current UTC time for the response.
- `CURRENT` means latest active, in-window source assertion, not verified truth.
  Explicitly replaced claims are `SUPERSEDED`; inactive/unreplaced or out-of-window
  claims are `UNCERTAIN`. Active documents with the same key and different values
  produce `CONFLICTING` alternatives, excluded from `current_memories`.
  History and uncertainty lists can overlap; they are not disjoint partitions.
- Conflict detection happens before query/path filtering. Selecting one matching
  side retains the other side and its evidence; a path is not an authorization
  boundary. Different-document updates never establish supersession.
- Snapshots capture the whole corpus under the writer lock, including historical
  versions and tombstones. Future snapshot cutoffs are rejected; identical cutoffs
  reuse the same snapshot. Replay fixes **both** observation and validity to the
  saved `as_of`, not replay time. `snapshot.observed_at` is capture time, not a
  second validity selector. Later syncs cannot change saved references. Migration
  `005_multiple_evidence` also rejects evidence INSERTs into captured versions
  under the same corpus lock; append-only evidence cannot silently change replay.

### Question retrieval (M006.5)

`POST /api/memory/query`, SDK `memory.query`, MCP `memory_query`, and CLI
`memory query` share `api/services/memory_query.py` through the public boundary.
This returns a typed, budgeted `MemoryResponse`, not an LLM answer. M006.5 adds
no database schema or migration; response `schema_version` remains **1**.

- Input is **`query`**, not `question`. It must contain a nonempty question
  (maximum 2,000 characters); omitted/empty input is `422 invalid_query` at the
  service, and whitespace-only input fails model validation.
- `intent` is an enum: **`auto` (default)**, `current`, `historical`, `temporal`,
  `change`, `conflict`, `provenance`. Non-default intent is unsupported by the
  older operations. `claim_id` is rejected for query even where adapters expose it.
- Auto intent uses time selectors first, then conflict vocabulary, provenance,
  change, current, historical vocabulary, and finally defaults to current.
  Explicit intent overrides this inference. This is a keyword heuristic, not
  general natural-language temporal reasoning.
- An explicit `as_of` or `snapshot_id` takes precedence over dates/stages in the
  question. Without either, one `YYYY-MM-DD` is interpreted as **midnight UTC**
  inclusive observation cutoff, not end-of-day or authored validity. Invalid or
  multiple dates return `422 invalid_timestamp`. A recognized `stage A`-style
  phrase requires an explicit selector from the caller's own stage mapping.
  Temporal intent requires `as_of`, a parsed date, or `snapshot_id`.
- “Before Kafka”, “after cutover”, and other relative phrases can select history,
  but **do not resolve a precise before/after-entity event boundary**. Supply a
  captured aware timestamp/snapshot for that. Existing `valid_at` and snapshot
  clock rules above still apply.
- The complete time-scoped archive is projected first. Relevance scores never
  turn an old claim into current authority. Ranking embeds the question and each
  archived claim's **claim text, authored key, and path**, using the existing
  configured normalized sentence-transformer (default `all-MiniLM-L6-v2`).
  A key's score is its maximum claim score; keys qualify at
  `score >= max(0.30, 0.90 * top_score)`. These are **relevance heuristics**, not
  confidence, truth, or authority thresholds. Scores propagate only within the
  same authored key, not an inferred semantic equivalence class.
- Current, temporal, and conflict intents select current claims plus connected
  conflicts; conflict intent is not a conflicts-only filter. Historical, change,
  and provenance also include historical/uncertain claims; historical/change
  include related changes. Connected conflict alternatives remain complete even
  across a path filter, and all retained claims keep their evidence.
- This service loads the existing embedding model on demand (inference runs off
  the async event-loop thread); the singleton can reuse an already loaded model.
  Other application components may initialize it earlier. **Vectors are transient
  and recomputed on every query: no persistent claim-vector cache or index.**
  Ordinary lexical memory reads do not require embeddings. Missing/unavailable
  weights can produce `503 memory_unavailable`; use offline flags when downloads
  are unwanted and provision the model cache first.
- Query determinism requires the same archive state, observation/validity clocks,
  question, selectors, intent, budget, **model and runtime**. Snapshot selection
  fixes the clocks, not model behavior across runtime/model changes.

[Measured M006.5 results and remaining relevance limits](../docs/evaluation/m0065.md)
do not establish universally reliable question retrieval or production readiness.
Existing lexical operations and default RAG remain unchanged.

## Response and evidence

Every success is a `MemoryResponse` with `schema_version: 1`, `query`, `corpus`,
`state` (`as_of`, `valid_at`, `snapshot`), `current_memories`, `historical_memories`,
`uncertain_memories`, `changes`, `conflicts`, `constraints`, `evidence`, `sources`,
`snapshot`, `truncated`, and `budget_unit: "unicode_characters"`.
`constraints` currently defaults to an empty list; do not infer a policy engine.

Claims include `id`, `key`, JSON `value`, `claim` text, `status`, `version_id`,
`path`, `observed_at`, validity bounds, `supersedes_id`, and `evidence_ids`.
Evidence includes its claim/version/document/chunk IDs, source path/hash,
heading, exact quote in `text`, chunk-relative character offsets, and observation
time. Sources derive only from attached evidence. Public responses do **not**
return complete raw archived files or full chunks. The database enforces that
quotes equal archived chunk slices; exact containment is not semantic entailment.

Migration `005_multiple_evidence` permits multiple distinct references per claim;
`004_memory`'s `UNIQUE(corpus_id, claim_id)` could not represent that requirement.
Authored `evidence` accepts the original string or a nonempty list of distinct
exact quotes. Every supporting chunk must also contain the claim text. Existing
string identities and corpus/version foreign keys are preserved; uniqueness now
covers claim/chunk/offsets. References are ingested atomically with the version.
Downgrade refuses any claim with more than one reference rather than dropping
provenance. See [migration and authoring details](MEMORY.md#migration-005_multiple_evidence).

Changes include `version_id`, `predecessor_id`, `event`, `path`, `observed_at`,
`memory_changed`, `relationship`, `previous`, and `current`. A document modification
need not change a claim: `DOCUMENT_MODIFIED` differs from `SUPERSEDES`. Deletion
appends a tombstone; restoration follows that tombstone with the same stable
source identity but does not link a claim across it as immediate supersession.

### Pack budget is not tokens or bytes

`budget` is a strict integer, minimum **512**, maximum **128000**, default **8000**.
The bound is `len(response.canonical_json())`: Unicode characters of the **complete
canonical JSON envelope**, including state, keys, escaped content, sources,
evidence, conflicts, and `truncated`. Canonical serialization uses sorted keys,
compact separators, `ensure_ascii=False`, and forbids NaN. Pretty JSON whitespace,
a CLI's trailing newline, and MCP transport wrappers are not included in this
bound; HTTP byte size and model token count are different quantities.

A range-valid budget can still return `422 invalid_budget` when the empty envelope
cannot fit (for example, a long query or corpus name). Selection greedily considers
current claims, changes, historical claims, conflict groups, then uncertain claims.
Claims/evidence and conflict sides are atomic; quotes are never cut mid-string,
and sources are rebuilt from retained evidence. An empty `truncated: true` pack
means nothing fit, not that the corpus has no knowledge. Check `truncated` before
using omissions as evidence of absence. Both `pack` and `query` are budgeted;
other memory responses are not. For query, `truncated=false` only means the
relevance-selected content fit; it does **not** certify retrieval completeness.

For the **same frozen state, validity cutoff, selectors, and budget**, the
canonical pack is identical. This is not a guarantee about independent wall-clock
calls: default validity timestamps differ, and concurrent ingestion can change
state. Use `snapshot_id` to fix observation and validity for repeatable selection.
Query additionally requires the same embedding model/runtime, question, and intent.

## Python SDK

Install the project (`pip install -e .`) and its requirements first. Named local
clients use the configured `DATABASE_URL` and initialize the embedding service;
`sync` requires a **named local** client. Unnamed local memory-only clients avoid
legacy retrieval initialization. Remote clients use HTTP for memory only; remote
`sync`, `search`, and `context` are not supported.

```python
from mindpalace_sdk import MindPalace

# Use the corpus name printed by a completed demo, or your own existing corpus.
mp = MindPalace()  # local memory-only client; no corpus created
current = mp.memory.current(corpus="my-corpus", query="streaming")
history = mp.memory.history(corpus="my-corpus", query="streaming")
changes = mp.memory.changes(corpus="my-corpus", query="streaming")
claim = current.current_memories[0]  # check for empty results in an application
proof = mp.memory.evidence(claim.id, corpus="my-corpus")
past = mp.memory.as_of(claim.observed_at, corpus="my-corpus", query="streaming")
saved = mp.memory.snapshot(corpus="my-corpus")
# The local SDK calls memory_public.execute: db.begin() commits on successful exit.
# This next call opens another transaction, so it also checks snapshot persistence.
replayed = mp.memory.replay_snapshot(saved.snapshot.id, corpus="my-corpus")
assert replayed.state.valid_at == saved.snapshot.as_of
pack = mp.memory.pack(corpus="my-corpus", query="streaming", budget=8000)
assert len(pack.canonical_json()) <= 8000
print(pack.canonical_json())

question_pack = mp.memory.query(
    "What carries Dispatch events now?", corpus="my-corpus",
    intent="current", budget=8000,
)  # unlike lexical reads, loads the configured model on demand
print(question_pack.canonical_json())

remote = MindPalace("my-corpus", base_url="http://127.0.0.1:8000", timeout=30)
print(remote.memory.current(query="streaming").canonical_json())
print(remote.memory.query("What carries Dispatch events now?").canonical_json())
```

SDK methods return typed Pydantic responses. `as_of(timestamp, ...)`,
`evidence(claim_id, ...)`, and `replay_snapshot(snapshot_id, ...)` use the positional
arguments shown above; other selectors are keywords. Packs accept `snapshot_id`
(`--snapshot-id` in CLI) and replay accepts `path` (`--path`). Passing
exposed-but-unsupported arguments does not bypass the central table above.

The synchronous local SDK rejects calls from an active event loop. In async local
applications use `await api.services.memory_public.execute(operation,
MemoryRequest(...))`, which owns and commits its transaction. Local service errors
are `MemoryError`; remote errors are `MemoryClientError`, with `code`, `message`,
and `status_code`. Remote adapter codes additionally include `timeout` (504),
`transport_error` (503), `invalid_response` (502), and fallback `http_error`.

## REST and CLI

Start the installed application separately on a trusted interface:

```bash
uvicorn api.main:app --host 127.0.0.1 --port 8000
```

Example read (substitute your existing corpus name):

```bash
curl --fail-with-body http://127.0.0.1:8000/api/memory/current \
  -H 'Content-Type: application/json' \
  -d '{"corpus":"my-corpus","query":"streaming"}'

curl --fail-with-body http://127.0.0.1:8000/api/memory/query \
  -H 'Content-Type: application/json' \
  -d '{"corpus":"my-corpus","query":"What carries Dispatch events now?","intent":"current","budget":8000}'
```

The `memory` CLI group is **remote-only**, defaults to `http://127.0.0.1:8000`, requires `--corpus`,
and prints canonical response JSON. Every command accepts `--base-url` and
`--query`. Use actual returned IDs/timestamps for the illustrative placeholders:

```bash
python -m cli.main memory current --corpus my-corpus --query streaming
python -m cli.main memory history --corpus my-corpus --query streaming
python -m cli.main memory changes --corpus my-corpus --query streaming
python -m cli.main memory evidence --corpus my-corpus --claim-id CLAIM_ID
python -m cli.main memory as-of --corpus my-corpus --as-of AWARE_TIMESTAMP --query streaming
python -m cli.main memory snapshot --corpus my-corpus
python -m cli.main memory replay --corpus my-corpus --snapshot-id SNAPSHOT_ID
python -m cli.main memory pack --corpus my-corpus --query streaming --budget 8000
python -m cli.main memory query --corpus my-corpus \
  --query "What carries Dispatch events now?" --intent current --budget 8000
```

CLI selector flags follow the contract table, with the SDK differences noted
above. Remote failures print `code: message` to stderr and exit 1; local value
validation exits 2. REST failures use `{"detail":{"code":"...","message":"..."}}`:
422 for invalid requests/timestamps/budgets, 404 for missing corpus/document/claim/
snapshot in the requested state, and 503 for database/schema/provenance
unavailability. Apply migrations for `memory_unavailable` caused by missing schema;
an unavailable backend is not an empty successful read.

## MCP

Run `python -m mcp_server` with `DATABASE_URL` configured. Memory tools are
`memory_current`, `memory_history`, `memory_changes`, `memory_evidence`,
`memory_as_of`, `memory_snapshot`, `memory_replay`, `memory_pack`, and
`memory_query`. Each accepts one `request` object using the contract fields.
For example, call **`memory_query`** with:

```json
{"request":{"corpus":"my-corpus","query":"What carries Dispatch events now?","intent":"current","budget":8000}}
```

Success includes structured content and canonical JSON text. Service failures
return `isError: true` with structured/text `detail` containing code and message.
Snapshot is advertised as non-read-only; other memory tools are read-only.
Existing `context`, `search`, `sync`, and `list_corpora` tools remain separate.

## Explicit memory-aware generation

Ordinary `/api/query/ask` remains live RAG. Opt in deliberately:

```json
{"question":"What carries Dispatch events now?","mode":"memory","corpus":"my-corpus","budget":8000}
```

POST this body to `/api/query/ask`. Its mandatory `question` becomes the memory
service's `query` with **automatic intent**; this ask adapter does not expose an
intent override. Optional `as_of`, `valid_at`, `path`, and `snapshot_id` use query
semantics (including temporal interpretation). The response includes the typed memory pack,
source paths, and an answer requested to cite `[evidence:<id>]`. Empty evidence
short-circuits generation. The existing configured LLM/provider is used only in
this explicit generation step; it may require credentials or incur costs.
This opt-in path now uses semantic memory query, not lexical AND. Use `memory.pack`
when you deliberately want lexical lookup terms separate from a downstream model's
question. Query retrieval and generated-answer quality are distinct; generation
was **NOT RUN** in the M006.5 evaluation.
Status instructions and untrusted-data delimiters are defenses, not a proof that
an arbitrary LLM obeys citations or resists all prompt injection.

## Reusable evaluation workload and CLI semantics

`api.services.memory_benchmark` is a standalone, reusable evaluation harness,
not a replacement for the public service. `eval memory` runs locally against
PostgreSQL; it does not call a REST server. See [commands and run record](MEMORY.md).

| API | Contract |
|---|---|
| `load_spec(file)` | Validate YAML version 1, ordered A–G stage aliases without gaps, operations and labels; resolve directories relative to the YAML |
| `memory_benchmark_workload(file, embeddings="fixture", database_url=None)` | Async context manager yielding an empty migrated random schema in one always-rolled-back transaction |
| `workload.apply_stage(alias)` | Apply exactly once in declared order: sorted Markdown overlays, then deletions, then whole-corpus snapshot; populate `snapshots`, `cutoffs`, `stage_results` |
| `workload.execute(operation, MemoryRequest(...))` | Shared `memory_public.execute_in_session` boundary in a savepoint-backed session; no durable outer commit |
| `workload.request_for(query, operation=None, budget=None)` | Translate authored stage/snapshot selectors to actual cutoffs/IDs; resolve an exact evidence claim selector |
| `workload.sessions()` | Sequential sessions bound to the one connection, `join_transaction_mode="create_savepoint"`; caller may use `execute_in_session` inside `db.begin()` |
| `workload.counts()` | Actual sandbox live/archive/snapshot counts, lifecycle totals, and current conflict count |
| `workload.normalize(value)` | JSON projection retaining content IDs, hashes, and list order; alias observation/as-of/valid-at clocks and snapshot IDs to stages |
| `score_expectations(q, response, normalized, claim_texts)` | Authored label checks, including supersession status/linkage and conflict evidence closure |
| `check_provenance(workload, response)` | Verify claim/evidence/source closure, scoped IDs/hashes, and exact archived chunk slices |
| `run_memory_benchmark(file, repetitions=20, corpus_sizes=(), embeddings="fixture", generation=False, natural_language=True)` | JSON-serializable semantics, diagnostics, safety gate, and separate runtime observations |
| `format_report(report)` | Concise gate, metrics, natural-language diagnostics, generation status, and timing summary |

The context uses `database_url`, else `MEMORY_TEST_DATABASE_URL`, else `DATABASE_URL`.
A PostgreSQL URL is required; its driver is set to asyncpg. It uses READ COMMITTED,
NullPool, and a **120,000 ms per-statement timeout**, not a whole-run deadline.
Migrations and ingestion happen only in the random schema. The outer transaction
rolls back even on errors; normal exit also disposes the engine. This context is
not concurrency-safe. The demo adds its own bounded workload timeout.

Logical paths are relative to the stage root, never stage-prefixed identities.
Delete-after-overlay supports F's on-disk runbook plus delete list. Missing files
in a later overlay alone do not imply deletion. Stage aliases denote real snapshot
observation cutoffs, not author-assigned dates. Evidence `claim` is exact text;
optional `path`/`as_of` disambiguate it inside the harness. Zero or multiple matches
are errors. This does **not** make `path` a public evidence request selector.

The benchmark captures live hybrid+RRF candidates (`k=20`) at each question's
stage before later updates. It evaluates labeled memory queries after all stages;
earlier-stage `current` cases execute as `as-of` at that stage's saved cutoff.
Snapshot replays are compared to raw canonical capture strings after G, not merely
to a second immediate replay. The standalone demo instead executes authored cases
at each declared live stage and compares all captures after G.

### Labels, baselines, and the gate

The real manifest has **39 independently authored cases**: current 16,
historical 3, temporal 4, supersession 3, conflict 3, provenance 3, deletion 4,
snapshot 3. Its `question` is not its tailored lexical `query`.

- Omitted labels are unscored; explicit `[]` asserts emptiness. Current-claim
  labels use exact sets. Other nonempty list labels require the labeled members
  to be present and may allow extras; despite some metric names ending in
  `exact_set_accuracy`, they are not all exact-set tests.
- Event labels are ordered cumulative sequences (names or partial Change maps).
  State labels compare partial public State maps after normalization. This fixture
  uses `{}` for state labels; that alone tests no state fields.
- Supersession also checks the predecessor's `SUPERSEDED` and successor's `CURRENT`
  status and linkage. Conflicts must retain evidence for all returned sides.
- Packs exercise 1000/2000/4000/8000/16000 characters, plus any authored extra
  budget. Whole claim/evidence units and conflict alternatives must stay closed.
  Low-budget recall loss and projection-label losses are diagnostics, not gate
  failures by themselves. An explicitly labeled primary pack must meet its labels.
- `passed` requires primary labeled cases, post-update replay equality, all pack
  budget/provenance/conflict/truncation safety checks, and operation/scaling
  execution checks. The `failures` list also includes diagnostic losses, so it
  can be nonempty when the gate passes. This is not a QA-quality gate.
- Live chunks have no explicit claim/status/version model. Baselines report
  literal expected-text availability and source paths, **not semantic accuracy**;
  empty claim expectations are not scoreable from untyped text. Budgeted baseline
  envelopes retain whole chunks with the same character accounting; that is an
  evaluation-only policy, **not the default `/ask` context policy**.
- Natural-language diagnostics replace `query` with `question` under the same
  operation/selectors, outside the gate. Read the nonempty-label denominator;
  empty-set successes are not evidence of question understanding. The recorded
  cached run was **0/24 nonempty current sets correct**, versus **39/39 tailored
  cases**. Baseline **79.17% text coverage is not semantic accuracy**.

Fixture embeddings are artificial deterministic hash vectors, not retrieval-quality
evidence. Cached mode loads the actual existing sentence-transformer with
`local_files_only=True`, without mutating the production singleton, and fails
without cached 384-dimensional weights. No fallback or model download occurs.

### Timings, scaling, and generation

Each operation has an untimed warm-up followed by `repetitions` samples (1–1000).
Both p50 and nearest-rank p95 are reported only for **at least 20 samples**.
Latencies include session/savepoint overhead, exclude setup and query embedding,
and retain raw samples. Per-operation SQL timing includes driver round trips and
savepoints; it is not server CPU time. Snapshot timings use a **fresh cutoff and
INSERT per call**, rolled back in that call's savepoint—not deduplicated snapshots.
Final dataset counts are captured before the separate operation-timing setup.

Scaling is opt-in, separate synthetic one-claim/document workloads, not A–G
semantic data. Suggested manual sizes: `100,500,1000`. Sizes must be positive,
with their supplied **sum at most 5000** (duplicates count toward validation;
execution uses sorted distinct sizes). Stage-file/deletion upper bounds are also
capped at 5000 possible versions. **5000 is a bound, not a measured result.**
The largest/smallest p50 ratio warning is descriptive (>1.5×), not a significance
test. See the maintainer-authored [measured report](../docs/evaluation/m006.md) for
actual performance; no unknown timings are supplied here.

Reports separate run-dependent clocks, IDs for snapshots, timings, sizes, SQL,
and generated outputs under `environment`; normalized semantic responses retain
content-addressed IDs, fingerprints, and sequence. Do not compare full reports
byte-for-byte across runs or mistake normalized clocks for authored validity.

Generation is disabled by default. Opt-in A calls `LLMService` with a memory pack;
B invokes the **actual default `/api/query/ask` route** at live-stage capture, with
cached embeddings required. Both need explicit `LLM_PROVIDER`; configured secrets,
provider data sharing, and costs apply. B temporarily binds route globals and
rejects overlapping harness calls, but does not protect unrelated server traffic:
**standalone process only, never concurrently with serving requests**. Bindings
restore on exit. Literal answer checks are not semantic judges or reliable
prompt-injection evaluation. **A/B were NOT RUN: no LLM service was available.**
A passing semantics gate does not certify generation or make M006 complete.

CLI `--save` writes a new JSON file exclusively; create its parent directory first.
It refuses overwrite. Gate failure exits 1; caught value/filesystem errors exit 2.
Other runtime failures may propagate; no output is evidence of success until the
command completes. There is no CLI whole-run timeout flag.

## M006.5 evaluation (separate from M006)

[The M006.5 report](../docs/evaluation/m0065.md) records **17/24** exact original
current sets versus the original lexical **0/24**, and **30/40** exact added
question cases. Added categories: current **13/19**, historical **4/4**, temporal
**3/6**, change **3/4**, conflict **4/4**, provenance **3/3**. All 17 relevance
failures remain reported against unchanged labels. The ownership clarification's
empty-history label is a potential label issue, not silently corrected.

**0 safety failures across 264 output cases** means provenance, authority,
conflict closure, canonical budget, and truncation checks passed—not complete
retrieval. The 40-question budget sweep scores 3/40, 3/40, 26/40, 30/40, 30/40
exact at 1000/2000/4000/8000/16000 characters respectively. A small budget can
correctly omit whole claim/evidence or conflict units. The gate covers safety
and execution only, not exact relevance or generation (**NOT RUN**).

A warmed fixed query over 20 repetitions measured **124.2/131.3 ms p50/p95**
including per-query embedding, archive projection and packing, versus **4.0/5.1 ms**
for preembedded live search. Different timing boundaries make this **not a fair
end-to-end latency comparison**. M006 timing exclusions above describe its older
harness, not M006.5 query embedding costs. See the new report for commands and
validation status; the M006 report is preserved.

## Performance limitations

Archive reads currently load complete corpus history in one SQL statement; there
is no pagination. Query adds heuristic semantic ranking, but recomputes transient
claim vectors for each request without a persistent cache/index. Packing performs
repeated bounded JSON projections. This avoids per-claim round trips but is not a scalability
claim. See `tests/test_memory_public.py` for a 100-document / 300-version timing
exercise and `examples/evolving-project/runs/` for local demo reports.

## Recorded M005 validation (2026-09-17)

- Starting working-tree suite: **259 passed, 7 warnings** with PostgreSQL. This
  included M004 plus 58 adapter-double tests, not completed M005 behavior.
- Final full suite: **598 passed, 0 skipped, 7 warnings** with PostgreSQL.
- Without database variables: **504 passed, 94 skipped, 5 warnings**.
- Shared public-service suite: 44 tests, including real PostgreSQL semantics,
  all-eight-operation cross-interface parity, foreign-corpus rejection, strict
  JSON budgets, transitive conflicts, and sanitized real database failures.
- MCP stdio smoke test spawns the actual server and exercises protocol discovery,
  typed current/pack responses, budget validation, and missing-claim errors.
- Black checked 74 Python files; Flake8 and `git diff --check` passed.
- Existing database upgrade 003→004 and repeated `upgrade head` succeeded;
  At that M005 checkpoint Alembic reported `004_memory (head)`; M005 itself added
  no schema change. The later M006 evidence requirement adds `005_multiple_evidence`.
- Five pre-existing pytest warnings concern asyncio-marked synchronous tests;
  two DB-run warnings concern connection cleanup in existing tests.

A local 100-document / 300-version exercise during the full suite measured:

| Operation | Wall time (ms) |
|---|---:|
| Current | 74.9 |
| History | 82.1 |
| Changes | 83.0 |
| Evidence | 76.1 |
| As-of | 72.2 |
| Pack | 374.3 |
| Replay | 59.5 |

These are single local measurements, not percentiles, a load test, or a memory
quality benchmark. The existing retrieval benchmark was not replaced.

The final demo completed successfully with corpus
`m005-demo-04043c9b07b74faea3e6d866a6979523`. It recorded five streaming lifecycle
events, two same-key supersessions, retained old evidence after deletion, and
byte-identical replay of snapshot
`9670836982b2dab8a274688b0af761eb91c1351d8b8e219b5bfb26db08990a3a` after restoration.
Its 8,000-character pack measured **7,950 characters**, included both sides of
the authentication conflict, and declared truncation. Local reports and response
JSON are in the ignored `examples/evolving-project/runs/<corpus>/` directory.

**Implemented and tested:** typed public memory adapters and shared semantics.
**Demo-ready:** the local evolving corpus and documented commands were exercised.
**Not certified deployment-ready:** access control, retention/erasure, pagination,
load testing, and operational review remain deployment responsibilities.

## Deployment and retention cautions

- **Authentication is not provided. Corpus namespaces are not authorization.**
  Restrict REST/network access and MCP hosts; add authentication and access control
  in your deployment before exposing sensitive data. SDK `headers` can support
  your own proxy, but do not enable built-in authentication.
- Treat all source text and metadata as untrusted data, not model instructions.
  Exact evidence and status reporting do not prevent prompt injection or verify
  the author's truthfulness. Neither demo makes LLM calls. The A–G evaluation demo
  uses artificial fixture embeddings with no model constructor; the older SDK demo's local sync
  still embeds documents with sentence-transformers; Hugging Face model weights
  may be cached or downloaded on first use. An uncached model needs network/disk
  access. Lexical memory reads do not require embeddings; the M006.5 query read
  does, using the configured model and transient per-query vectors.
- M004's append-only archive guards **currently block corpus deletion when archive
  rows exist**. A live-file deletion through sync is different: it removes the live
  index entry and preserves archive/evidence. Do not promise API deletion as demo
  cleanup or as a retention/erasure solution. Ordinary DML cannot erase archives;
  the database owner is not a tamper-proof boundary.
- Whole-directory sync is per-document atomic, not an atomic directory snapshot.
  Use dedicated writers for this demo. This walkthrough is not a production
  readiness claim, a retrieval benchmark, or proof of perfect memory.
