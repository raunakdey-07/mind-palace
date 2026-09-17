# Public memory API (schema version 1)

The implemented contract is `api/models/memory.py`, with operation validation,
transaction ownership, projection, and packing in `api/services/memory_public.py`.
REST (`api/routers/memory.py`), SDK (`mindpalace_sdk.py`), MCP (`mcp_server.py`),
and CLI (`cli/memory.py`) delegate to that boundary. This is persistent,
evidence-backed **source assertion memory**, not perfect truth or semantic
contradiction detection. See [the evolving demo](MEMORY.md).

## Operations and selectors

All REST operations below are **POST** with a JSON request, including reads.
All require `corpus`; `query` defaults to `""` (all memories). Names must match
`[A-Za-z0-9._-]+`, length 1–128. Memory reads do not create missing corpora.

| REST path | Meaning | Additional accepted selectors |
|---|---|---|
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

- Matching is case-folded lexical **AND** over word tokens in claim key, claim
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
  second validity selector. Later syncs cannot change saved references.

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
using omissions as evidence of absence. Other memory responses are not budgeted.

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

remote = MindPalace("my-corpus", base_url="http://127.0.0.1:8000", timeout=30)
print(remote.memory.current(query="streaming").canonical_json())
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
```

The CLI is **remote-only**, defaults to `http://127.0.0.1:8000`, requires `--corpus`,
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
`memory_as_of`, `memory_snapshot`, `memory_replay`, and `memory_pack`. Each accepts
one `request` object using the contract fields, for example:

```json
{"request":{"corpus":"my-corpus","query":"streaming","budget":8000}}
```

Success includes structured content and canonical JSON text. Service failures
return `isError: true` with structured/text `detail` containing code and message.
Snapshot is advertised as non-read-only; other memory tools are read-only.
Existing `context`, `search`, `sync`, and `list_corpora` tools remain separate.

## Explicit memory-aware generation

Ordinary `/api/query/ask` remains live RAG. Opt in deliberately:

```json
{"question":"streaming","mode":"memory","corpus":"my-corpus","budget":8000}
```

POST this body to `/api/query/ask`. Optional `as_of`, `valid_at`, `path`, and
`snapshot_id` use pack semantics. The response includes the typed memory pack,
source paths, and an answer requested to cite `[evidence:<id>]`. Empty evidence
short-circuits generation. The existing configured LLM/provider is used only in
this explicit generation step; it may require credentials or incur costs.
Lexical AND matching applies to the question too: use memory packs directly when
you want to choose lookup terms separately from the question sent to your model.
Status instructions and untrusted-data delimiters are defenses, not a proof that
an arbitrary LLM obeys citations or resists all prompt injection.

## Performance limitations

Archive reads currently load complete corpus history in one SQL statement; there
is no pagination or semantic relevance ranking. Packing performs repeated bounded
JSON projections. This avoids per-claim round trips but is not a scalability
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
  Alembic reports `004_memory (head)`. M005 adds no migration or schema changes.
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
  the author's truthfulness. The demo makes no external paid LLM calls. Local sync
  still embeds documents with sentence-transformers; Hugging Face model weights
  may be cached or downloaded on first use. An uncached model needs network/disk
  access; memory reads themselves do not require embeddings.
- M004's append-only archive guards **currently block corpus deletion when archive
  rows exist**. A live-file deletion through sync is different: it removes the live
  index entry and preserves archive/evidence. Do not promise API deletion as demo
  cleanup or as a retention/erasure solution. Ordinary DML cannot erase archives;
  the database owner is not a tamper-proof boundary.
- Whole-directory sync is per-document atomic, not an atomic directory snapshot.
  Use dedicated writers for this demo. This walkthrough is not a production
  readiness claim, a retrieval benchmark, or proof of perfect memory.
