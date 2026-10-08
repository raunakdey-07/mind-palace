# Changelog

All notable public releases are listed here. Milestone identifiers are
preserved inside each release entry and map to the public semantic version
through [`docs/release-map.md`](docs/release-map.md).

## [v0.9.0] - 2026-10-08

Milestones `M015.2` (five-minute memory path) and `M016` (instant recall, explicit
model-free mode, real-project dogfooding), consolidated into one public release. They
are one milestone for a product: `v0.9.0` is the release in which Mind Palace becomes
something a developer can install, use in five minutes, and keep.

Verification still establishes integrity and recorded provenance. It does **not**
establish that the original source was factually correct, and it does not establish
authenticity unless a digest pinned out of band is supplied.

### Five-minute memory workflow

```console
$ mindpalace init
$ mindpalace remember "The production datastore changed from SQLite to PostgreSQL." --key datastore
Remembered.
  version: 5aae989d3134 (new)
  corpus:  default
  path:    memory/datastore-aa7e5b234e1d.md

$ mindpalace recall "What datastore does production use?"
The production datastore changed from SQLite to PostgreSQL.
   source: memory/datastore-aa7e5b234e1d.md @ 5aae989d3134
   time:   true as of 2026-10-08 03:40 UTC
```

Seven commands — `init`, `remember`, `recall`, `explain`, `history`, `receipt`,
`verify` — that take a statement and a question.

- **The default corpus.** No `--corpus` on the first command. Scoping stays available
  for anyone who needs it.
- **Idempotent writes.** The same statement twice returns the same `version_id` and
  `changed=false`. Four concurrent identical writes produce exactly one version and
  one claim, so a retry after a lost response converges instead of duplicating.
- **A statement becomes authoritative memory**, with exact-substring evidence, through
  the same archive a synced document uses. Not a search index entry.
- **Concise recall.** Answer, source, and the time it was true. Depth is on request.
- **Explicit abstention.** `NO_RELEVANT_MEMORY`, never a guess, and never evidence
  cited for an abstention.
- **Evidence-aware `explain`**: the exact characters that support the answer, its
  supersession history, and whether a receipt is available to export.
- **Writes need no embedding model.** `remember` never imports the model stack.

### Instant repeated reads

A local runtime keeps the embedding model and the database pool warm, and the CLI uses
it without the user choosing to.

| command | before | after |
|---|---|---|
| `mindpalace recall` | 6.17 s | **0.63 s** |
| `mindpalace explain` | 6.23 s | **0.67 s** |
| `mindpalace receipt` | 6.21 s | 0.62 s |
| `mindpalace history` | 0.81 s | 0.61 s |
| `mindpalace verify` | 0.56 s | 0.56 s |
| SDK `recall`, fresh process | 5.11 s | **0.51 s** (opt-in `runtime=True`) |

MCP is unchanged, and correctly so: an MCP server is a long-lived process and pays the
import once.

The cause was measured with `-X importtime` rather than assumed. A one-shot recall was
6.53 s, of which 5.32 s was `import sentence_transformers` (1.68 s `torch`, 1.20 s
`transformers`, 2.66 s the rest), 0.49 s was model construction, and **61 ms was the
memory work**.

- `mindpalace runtime start | stop | status`, including `--json`. Optional: every
  command works with no runtime at all, and `MIND_PALACE_RUNTIME=0` turns it off
  everywhere.
- **Crash and stale-socket recovery.** A killed runtime leaves a socket file; the next
  command removes it and starts a fresh one. SIGTERM finishes any in-flight request,
  unlinks the socket and exits 0. Idle 15 minutes, it exits on its own.
- **Unix socket only.** A 0700 directory, a 0600 socket, and a token read from a 0600
  file — never from a command line, where any local user could read it out of `ps`.
  No code path binds a TCP port, and there is a test that asserts it.
- **Protocol versioning.** A runtime refuses a request that asks for a different
  protocol, with an instruction to restart it, rather than answering with the
  semantics of the build it was started from.
- **No model, no silent different answer.** If the model cannot load, the runtime
  records the reason and the read takes the same path it would have taken without a
  runtime.

### Verifiable memory

Carried forward unchanged from v0.8.0 and now reachable from every surface.

- Receipts on REST, SDK, MCP and the CLI; `mindpalace receipt` writes a self-contained
  file.
- **Offline verification.** `mindpalace verify` needs no server, no database, no model
  and no network. So does `mindpalace-proof`, on a bare `pip install mindpalace-os`
  with no CLI extra, because the receipt carries the response it describes.
- **Cross-surface parity.** CLI, SDK, REST and MCP return the same authoritative
  memory, evidence, temporal state and receipt. The runtime path does too, asserted in
  `tests/test_m016_runtime_equivalence.py`.
- **Memory Pack v1** unchanged, and byte-identical whether produced in-process or
  through the runtime.

### Explicit model-free mode

`MIND_PALACE_LEXICAL=1` reads through the lexical relevance rung that already existed
as the no-model degradation, taken on purpose.

| | semantic | `MIND_PALACE_LEXICAL=1` |
|---|---|---|
| `recall`, cold process | 6.17 s | **0.63 s** |
| `explain`, cold process | 6.23 s | **0.67 s** |
| model stack imported | yes | **none** |

It is documented as a deliberate trade-off, not a substitute. On a ten-fact corpus
across sixteen question shapes the two modes agreed on fifteen; the difference was an
abstention that semantic ranking did not make. Nothing was weakened to make them
agree.

**A lexical fast path.** A more attractive design was measured and *not* shipped:
find candidates lexically, then load the semantic model only when that looks
insufficient. It would have removed the 5.3 s import from every command rather than
from all but the first, and on 15 of 16 questions it was indistinguishable from the
semantic path. The sixteenth is the abstention above, and shipping it
would have made `NO_RELEVANT_MEMORY` depend on whether the model happened to be
cached on the machine — the exact acceptance coupling that would make an abstention
unreproducible across two developers' laptops.

An explicit mode is honest about the trade-off; an automatic one hides it. Details in
[docs/operations.md](docs/operations.md#model-free-lexical-mode).

### Developer experience

- **A public-only example.** `examples/project-log/` uses this repository's own
  decision history as memory. It imports `mindpalace_sdk` and nothing else: no
  `api.services`, no `api.db`, no internal retrieval module, no SQL. If it needed a
  change to work, the SDK would not be usable yet.
- **Errors answer what happened, why, and what to do.** Two were leaking internals and
  are fixed: a bad `--as-of` produced a raw pydantic message, and an unreadable receipt
  file did not say what a receipt is or how to write one.
- **Deterministic JSON.** `recall`, `explain` and `history` return the public
  response contract, verified key-for-key against the SDK. `explain --json` now emits
  the same `{response, receipt}` bundle `mindpalace receipt` writes, instead of
  silently dropping the receipt the human-readable form shows.
- **One failure, one exception class.** The in-process SDK path raised the service's
  own `MemoryError` while the runtime and HTTP paths raised `MemoryClientError`, so
  which one a caller needed depended on whether a runtime happened to be running. All
  three now raise `MemoryClientError` with the same code, message and status. A genuine
  defect is still not disguised as an API failure.
- **`mindpalace runtime status`** when a command feels slow, without dumping internals.

### Fixed

Defects found by review and dogfooding in this release, each now covered by a
regression test:

- **The test suite could not pass on a clean runner.** CI installed
  `sentence-transformers` but never the weights, and ran with `HF_HUB_OFFLINE=1`, so
  **41** tests failed on `OSError: We couldn't connect to ...` — everything that
  indexes or ranks anything, including 11 fixture errors in `test_search_semantics.py`.
  `--maxfail=1` reported only the first, which is how a green local run and a red CI
  run coexisted through a release. The `tests` job now provisions both models
  explicitly, confirms the embedder loads with the network disabled, and runs pytest
  without `--maxfail=1`. `--maxfail=1` was hiding failures, not preventing them.
- **A database-outage test was passing for the wrong reason.**
  `test_search_backend_unavailable_is_503_not_empty` asserted only that the word
  "unavailable" appeared in the error detail — and `"Semantic model unavailable"`
  contains it. On a runner with no model the mocked outage was never reached
  (`RetrievalService.search` was never called) and the 503 came from the model
  instead, so the DB-outage contract was not actually tested anywhere. It now asserts
  the exact detail and that the search was reached.
- **A zero-result search test depended on a developer's model cache.** It patched
  `RetrievalService.search` but not the embedder that `/api/search` calls first, so it
  passed locally and failed on CI. Both tests now state their precondition explicitly.
  No production behaviour changed: `/api/search` embeds the query before searching,
  `RetrievalService.search` needs that vector in its SQL, and `503` for an unavailable
  model remains correct — it is a vector endpoint with no lexical rung.
- **The CI step for the model-free mode could not tell a working mode from a silent
  one.** It asserted the exit code, which an abstention also satisfies. It now asserts
  that a discriminating question is actually answered, still imports no part of the
  model stack, and still announces itself exactly. Finding this surfaced a real
  limitation, now documented: lexical ranking scores the terms that discriminate
  between candidates, so a question whose only shared term is one *every* candidate
  carries — `What datastore does production use?`, with two datastore memories — is
  correctly abstained on. Predates v0.9.0 and unchanged by it.

- **A multi-line statement, and `remember --file` on any Markdown file, could not be
  recorded at all.** The frontmatter quote collapsed line breaks while the document body
  kept them, so the evidence the archive was told to cite was never an exact substring
  of the archived chunk.
- A statement beginning with `#` was chunked as a heading and rejected for the same
  reason.
- `POST /api/memory/remember` accepted a `file` path. On an endpoint with no
  authentication, that let any caller read a file relative to the server's working
  directory and recall its contents. The field is gone from the HTTP contract;
  `mindpalace remember --file` and the SDK's `file=` still read local files, which is a
  decision for a trusted operator.
- The REST response computed the claim key and document path independently of the write,
  so a whitespace-padded statement was reported under a key the archive did not hold —
  and a client retrying with that key would have written a second memory instead of
  recognising the first.
- An imported file's absolute path became part of its memory identity.
- `explain` attached the change set to the response *after* its receipt digest was
  computed, so the digest it printed no longer described what it printed.
- Two different keys that reduced to the same filename shared one document, so an
  unrelated fact could supersede another.
- `mindpalace verify` rejected a bare receipt or proof that `mindpalace-proof verify`
  accepts. One verification contract now.
- `mindpalace_runtime` was not packaged, so on an installed wheel the runtime silently
  never started and every command stayed 6 seconds.
- A read discarded the claim vectors it had just computed. A corpus authored without
  embeddings was re-encoded on every read; it is now encoded once.
- A runtime that loaded slowly was reported as `loading` by `start --wait`, which read
  that as failure.
- **The CLI's `explain --json` bundle shape was claimed and unasserted.** Nothing held
  that `explain --json` emits the same `{response, receipt}` document
  `mindpalace receipt` writes, while `recall --json` and `history --json` return the
  bare response. Now pinned, including that the receipt is verifiable against the
  response it ships with and that a rewritten answer is rejected.
- `eval/m00675/README.md` described the stored artifact's 47/60 as the *current*-source
  result. It is not: the current source measures 45/60, and had already drifted before
  v0.9.0. The README now separates the recorded artifact from the reproducible result.
- The README claimed a missing model "never removes the answer", which is true of
  every memory surface and false of the legacy `/api/search`, which has no lexical
  rung and answers 503. The exception is now stated.

### Changed

- `memory_query` now writes back the claim vectors it had to embed. This overturns a
  documented "a query never writes" decision, so it is bounded rather than assumed safe:
  its own `SAVEPOINT`, skipped on a read-only session, best-effort, and a partial batch
  is discarded when the lexical fallback runs. The cache holds no authority, so every one
  of those boundaries costs latency and nothing else.
- **The relevance acceptance gate tests overlap against full claim terms** rather than
  terms reduced by the set shared with every candidate. Shared terms cannot discriminate
  between candidates, so ranking still uses the reduced set; but they are not evidence
  that a question is off-topic, and using them for acceptance made any question about a
  subject unanswerable once a second claim shared that subject. This is the only
  retrieval change since M013.
- A read that fell back to lexical because no model loaded now says so on stderr, once.
  The degradation is correct and long-standing; what was missing was that it was silent.

### Measured

`scripts/five_minute_probe.py`, 200 remembered statements, on mains power, around the
service call: `remember` p50 10.8 ms, `recall` p50 92.6 ms, `explain` p50 93.8 ms,
`verify` p50 0.82 ms in a separate interpreter with no database URL and no network.
At 1000 statements `remember` and `verify` stay flat and `recall` reaches p50 988 ms,
because the authoritative projection loads the version graph before relevance narrows
anything.

Full numbers, method and limits:
[docs/performance/five-minute-path.md](docs/performance/five-minute-path.md). Every
latency figure in this repository's history before v0.9.0 was taken on battery, where
the same probe is 3 to 4× slower; compare within a condition, not across them.

### Compatibility

- **Memory semantics are unchanged.** Claim identities, evidence, provenance, temporal
  validity, supersession, conflicts and abstention are byte-identical between the
  runtime and the in-process path across ordinary, weakly related, absent, temporal,
  conflict and multi-topic queries.
- Memory Pack stays at version 1. The receipt schema stays at version 1. No migration,
  so existing v0.8.0 receipts remain verifiable and the frozen benchmark is untouched.
- `MemoryResponse` and every existing route, SDK method and MCP tool are unchanged.
  `memory_query` is kept alongside `memory_recall`; both reach the same service.
- The local runtime is never required: `MIND_PALACE_RUNTIME=0`, `MindPalace(runtime=…)`
  defaults to off, and a platform without `AF_UNIX` runs in-process.
- `sentence-transformers` remains a declared dependency, so a plain install keeps
  ranking semantically by default. Nothing in the package needs it merely to import or
  to run a command, which is asserted by blocking the model stack at the import hook.

### Frozen benchmark, unchanged

No benchmark question, policy, evaluator semantic, tokenizer or retrieval threshold
was touched by this release. That is now **measured, not asserted**: re-running the
frozen held-out suite under both source trees produces **60 of 60 identical canonical
per-question decisions** between `v0.8.0` and `v0.9.0`, with 0 execution failures and
all seven safety invariants passing in both.

- **M006.75 frozen held-out benchmark** — 60 scenarios, **45/60 (75.0%)** exact and
  55/60 abstention-exact on the current source, 0 execution failures, and **all seven
  safety invariants at 60/60** (provenance, temporal scope, status, conflict closure,
  current-authority preservation, truncation, budget containment).
- **M006.5 added-question set** — 40 questions, **30/40 (75%)** exact, 0 safety
  failures across 264 output cases.
- **M013 held-out v1** — 157 independently authored questions, **131/157 (83.4%)**.

The safety invariants are at 1.000; retrieval accuracy is not, and this release does
not conflate the two.

**One discrepancy is reported rather than fixed.** The frozen artifact
`eval/m00675/result.json` records 47/60, which the current source no longer
reproduces — and did not already at `v0.8.0`. The drift is four questions, all of the
same shape: the expected subject is still recalled and extra keys are admitted beside
it. The artifact's own `--verify` source check fails on `v0.8.0` too, and its recorded
source fingerprint matches no commit. Its **frozen inputs verify unchanged** (dataset
and policy hashes), which is the check that protects benchmark semantics. The artifact
was deliberately **not** regenerated: a re-run is a new research result, and
publishing one under the frozen `M006.75` name would misreport research as a product
gate. Full measurement and the likely cause are in
[the reproducibility report](docs/evaluation/m00675-reproducibility.md).

The 131/157 figure remains a generalisation result and a research limitation, and is
not superseded by the frozen benchmark: they were authored for different purposes and
answer different questions.

### Known limitations

- Recall grows linearly past a few hundred statements; the authoritative projection loads
  the version graph. That work is the guarantee, not overhead to be trimmed.
- The first command in a fresh process pays a ~5.3 s model import unless a runtime is
  already warm, or `MIND_PALACE_LEXICAL=1` is set.
- Authentication is a documented contract, not implemented code.
- Receipts are not signed; `--trusted-digest` covers authenticity against a value the
  user pinned themselves.
- Lexical mode selects different keys from semantic mode on vocabulary-poor questions,
  by design.

## [v0.8.0] - 2026-10-05

### Verifiable Memory

- **Portable Memory Receipts.** A receipt records what Mind Palace actually
  returned to one application, for one query, at one historical state: the claim
  identity and version, the document version and path that carried it, its
  evidence with offsets, its validity window, its supersession lineage, the
  authoritative digest and an embedded proof. `memory_receipt.py` is
  standard-library-only and ships in the distribution.
- **Receipts through every public surface.** `include_receipt=true` on
  `POST /api/memory/query` (and the SDK's `query(...)`) attaches the same
  canonical receipt to the existing response. Clients that do not ask for it see
  no new field at all: `receipt` is absent rather than null. The MCP
  `memory_explain` tool is `memory_query` with the receipt forced on, so an agent
  can ask why a memory is trustworthy without knowing the flag. All three
  surfaces call the same service and return the same object.
- **Historical memory.** `as_of` reconstructs the archive as it was, so a
  question asked before a supersession is answered with the claim that was
  authoritative then, and its receipt says which instant it describes. A claim
  that was `CURRENT` now but not yet valid at the queried instant is reported as
  a note rather than silently accepted.
- **Offline verification.** `mindpalace-proof` gained `receipt` alongside
  `verify`, `prove` and `explain`. Verification needs no server, database,
  embedding model, network or credentials, and `verify --json` reports integrity,
  provenance, temporal and supersession separately, never collapsed into one
  verdict.

### Trust boundary

Verification establishes **integrity and recorded provenance**: that the artifact
still represents the state the receipt describes, and that no covered record has
been altered since. It does **not** establish that the original source was factually correct,
and a digest alone does not establish **authenticity** — a party able to replace
both the artifact and the receipt can recompute the digests. Supply
`--trusted-digest` with a digest pinned out of band and authenticity becomes
checkable. `verify` states the unestablished case explicitly rather than letting
a digest imply more than it shows.

### Compatibility

- Existing responses, SDK behaviour, MCP tools, feed semantics and Memory Pack
  authoritative semantics are unchanged. The receipt is purely additive and is
  absent unless requested.
- The Memory Pack schema stays at version 1. The proof reuses
  `MemoryPack.canonical_json` exactly, so there is one canonicalisation contract
  rather than two.
- Retrieval is unchanged. No tokenizer, threshold, acceptance-policy or
  candidate-selection behaviour was modified; M013's rejected rewrites stay
  rejected.

### Fixed

- Verifying a historical proof compared the observation cutoff as text, so a
  claim recorded at exactly `as_of` was rejected whenever the artifact spelled
  the instant `...Z` and the proof `...+00:00` — which is what a real response
  does. Instants are now compared as instants, as they already were for
  `valid_at`.
- A receipt for a supersession chain authored without validity intervals named
  today's claim for a question asked earlier. Supersession is the only record of
  observation time, so it is now the fallback.
- The packaging-boundary test skipped unless a wheel happened to be built first,
  which is never true in CI. It builds on demand now, and additionally installs
  the wheel with `--no-deps` into an empty virtual environment and verifies an
  exported receipt through the installed command, outside the source tree.

## [v0.7.0] - 2026-09-28

### Added

- Unified authoritative `context()` surface across REST, the Python SDK and MCP.
  The archive is resolved first and ranked live chunks are attached as raw
  material, so a conflicted key is reported as a conflict rather than as the
  latest writer's text. The response keeps its existing fields and adds
  `status`, `memories`, `conflicts` and `changes`; existing fields are unchanged.
- `memory_pack.py`, a standard-library-only reader for the Memory Pack. It is
  shipped in the distribution so a consumer can read a pack with no database,
  ORM, embedding model or server present.
- Claim-representation cache (migration `007_claim_embedding_cache`) and a
  `mindpalace reindex` command that rebuilds the derived layer from the archive.
- `as_of` and explicit `intent` selectors on `GET /api/context`, and a lexical
  relevance fallback so authoritative query answers when the embedding model
  cannot load.
- Dependency-free Memory Pack reading, verified from a clean install.
- **Corpus scoping for live retrieval.** `/api/search`, `/api/query` and
  `/api/context` now resolve an explicit corpus before retrieving. With a single
  corpus the parameter may be omitted; with several, omitting it returns **422**
  and naming an unknown corpus returns **404**, rather than silently searching
  across namespaces. Callers that relied on the previous cross-namespace default
  must pass `corpus`.
- **Sync reports per-file failures.** A sync response now carries a bounded list
  of `errors` (at most 50, each a path and a non-sensitive reason) alongside the
  existing aggregate counters, exposed through the SDK and the CLI.
- Semantic loading stays lazy: neither the embedder nor the reranker loads a
  model until first use, and the reranker is a thread-safe singleton.
- The OpenAPI document now reports the installed package version, so it cannot
  disagree with the distribution.

### Fixed

- **Cached-vector queries got 22x slower on a long-lived connection.** After
  asyncpg prepared the statement, two large SQL array parameters dominated the
  load: roughly 380 ms for the first five executions and about 8,400 ms after.
  The load now selects by the indexed corpus, model and dimension prefix and
  filters in Python, which is also a stricter freshness check. Measured on the
  public query path with a warm cache: 10,168 ms to 1,745 ms at 5,000 claims,
  1,359 ms to 1,010 ms at 1,000, and no regression at 100.
- `bounded_pack` now preserves the `NO_RELEVANT_MEMORY` marker, so a bounded
  answer to an unanswerable question is distinguishable from an empty envelope.
- The archive load no longer ships chunk text to the public projection, which
  reads only a chunk's id and heading.
- **Deleting a corpus whose durable archive still references it is refused** with
  **409** and a named error, instead of failing on a foreign key or removing
  manifest rows before documents.
- **A missing semantic model is reported as 503**, not 500, on ingest, search,
  query and context, and the dependency detail is not echoed to the caller.
- **Feed cursors are validated and canonicalised** against the corpus name before
  use, so a malformed or foreign cursor is rejected rather than compared.
- `memory_rehydrate` preserves the document id the archive recorded, so a rebuild
  cannot change `Source.document_id` in a replay.
- The API description reads "The Durable AI Memory Substrate" and is pinned by a
  test, so it cannot drift back to the previous RAG framing.
- `memory_pack` is declared in the package, so `pip install` makes it importable.
- Runtime dependencies are declared in `pyproject.toml` rather than left empty.

### Changed

- Claim representations are tokenised once and memoised on the immutable claim
  text, and the common-term difference is computed once instead of per candidate
  and per topic. Neither showed a measurable end-to-end change on the measuring
  host and neither is presented as a performance result.
- The public projection no longer receives full chunk text.
- Process startup no longer registers a no-op ASGI lifespan handler. Startup
  remains independent of PostgreSQL connectivity, which was the handler's only
  stated purpose.

### Compatibility

Backward compatible for the Memory Pack schema, which stays at version 1, and for
every public response field. The one behaviour a caller can notice is corpus
scoping on live retrieval: where several corpora exist, `corpus` is now required
and an omitted value returns 422 rather than searching all namespaces.

### Upgrade

- Two additive migrations apply: `006_snapshot_membership_seal` and
  `007_claim_embedding_cache`. Neither rewrites existing rows. The Memory Pack
  schema stays at version 1 and no public response contract changed, so this is a
  minor release.

### Known limitations

- Measured warm p50 for the public query path: 47 ms at 100 claims, 1.0 s at
  1,000, 1.7 s at 5,000 and 4.4 s at 10,000, on one host with roughly 2x
  run-to-run variance. 25,000 claims and above were not measured.
- Above about 5,000 claims, projection dominates: it builds a model per archived
  claim and keeps roughly one. Reducing that requires the dependency closure to
  be solved first, because narrowing the archive before projection was measured to
  change 32 of 202 benchmark answers.
- The archive load is not a clean linear function of archive size between 1,000
  and 5,000 versions, and its sort spills to disk on a corpus of that size. The
  cause is not identified. JIT compilation and run-to-run variance are ruled out.
- Conflict detection is keyed, so a contradicting claim authored under a different
  key is not detected and is not labelled as drift.
- Relationship questions score 31 of 34. The failures are two ranking
  weaknesses, one of which is a vocabulary gap, and one missing authored
  representation. No multi-hop relationship reasoning is claimed.
- M006.75 frozen evidence is unchanged. No prior benchmark number was rewritten.

## [v0.6.0] - 2026-09-24

### Added

- Durable PostgreSQL-backed corpus-scoped memory feed with keyset ordering,
  integrity-protected corpus-bound cursors, REST/Python SDK/CLI interfaces, and
  dependency-aware health endpoints.
- Isolated M009 release validation for live interface equivalence, concurrent
  insertion semantics, exact 100/1,000/10,000-version fixtures, p50/p95 timing,
  and PostgreSQL query plans.
- Explicit MCP exclusion decision for the operational feed.

### Guarantees and limitations

The feed provides bounded keyset continuation over `(observed_at, version_id)`
and documents its MVCC/late-commit behavior. It is not exactly-once messaging,
a broker, CDC, or transactional event delivery. The final page has no implicit
continuation cursor; polling clients must restart and deduplicate or maintain a
separate boundary policy. M009 does not change any M006.75/M007/M008 research
inputs, results, or claims.

Validation evidence is recorded in [`docs/m009/RELEASE_READINESS.md`](docs/m009/RELEASE_READINESS.md).

## [v0.5.1] - 2026-09-24

### Added

- M007.1 independent-adjudication handoff infrastructure.
- Blind 118-case review package with authoritative traceability.
- Ambiguity-preserving reviewer context for exact and ambiguous matches.
- Reviewer schema, guide, blank JSONL template, and reviewer-file validation.
- DecisionReceipt infrastructure with canonical fingerprints, explanation, replay,
  and structured drift detection.
- Deterministic synthetic semantic/property suites and executable M007–M008
  research runner.
- Archive reconstruction through the existing ingestion service.

### Validation

The latest full-suite validation snapshot before this release was:

```text
942 passed, 0 failed, 147 skipped, 10 warnings
```

Skipped tests are environment-gated integration tests and are not counted as
passes.

### Scientific status

- M006.75 remains the latest empirical benchmark: **47/60**.
- M007.1 is **ready for independent human adjudication**.
- M007 scientific results are pending two independent reviewer submissions.
- M008 evaluation remains downstream of the M007 scientific gate.

This release packages research/evaluation infrastructure only. It does not claim
M007 accuracy, agreement, calibration, or comparative improvement.

## [v0.5.0] - 2026-09-23

### Included milestones

- **M004:** persistent, versioned, corpus-scoped memory foundation.
- **M005:** public REST, Python SDK, MCP, and CLI memory interfaces.
- **M006:** persistent-memory evaluation infrastructure and lifecycle
  validation.
- **M006.5:** natural-language memory querying and bounded relevance access
  over the persistent memory model.
- **M006.75:** frozen, reproducible evaluation of current, historical,
  temporal, multi-topic, conflict/provenance, and abstention behavior.

### Added

- Immutable document/version history with deletion and restoration lifecycle.
- Authored claims, exact evidence, provenance, validity intervals,
  supersession, conflicts, snapshots, and replay.
- Multiple evidence references per claim.
- Bounded Memory Packs that preserve evidence and conflict closure.
- Public memory operations across REST, Python SDK, MCP, and CLI.
- Natural-language query planning with explicit abstention and temporal
  handling.
- Reproducible PostgreSQL-backed M006.75 benchmark runner and verification
  artifact.
- Frozen held-out corpus, frozen policy, source/dependency/model fingerprints,
  and canonical result validation.

### Evaluation

On the frozen 60-question M006.75 corpus:

| Category | Exact / full |
|---|---:|
| Overall | **47/60** |
| Current | 6/10 |
| Historical | 5/10 |
| Temporal | 10/10 |
| Multi-topic | 9/10 |
| Conflict / provenance | 7/10 |
| Abstention | 10/10 |

Safety results are reported separately: 0 false-positive retrievals, 0
false-current promotions, 0 provenance failures, 0 conflict failures, 0
temporal failures, 0 budget failures, and 0 execution failures.

Canonical result:

```text
881530c7c6e7ba29fb38eb5b27609071463f733c6a8cd173aeff04173a5d8dc8
```

### Limitations

The benchmark is a project-specific authored evaluation, not a claim of
universal memory-system superiority or production readiness. M007/M007.1
decision-provider and Jev research is intentionally unreleased.

## [v0.4.1] - 2026-09-17

- Stabilized the pre-M006 persistent-memory baseline and database URL handling.
- This release is the starting point for the M006 evaluation work.

## [v0.4.0] - 2026-09-17

- Exposed persistent memory through the public developer interfaces.

[v0.6.0]: https://github.com/raunakdey-07/mind-palace/releases/tag/v0.6.0
[v0.5.1]: https://github.com/raunakdey-07/mind-palace/releases/tag/v0.5.1
[v0.5.0]: https://github.com/raunakdey-07/mind-palace/releases/tag/v0.5.0
[v0.4.1]: https://github.com/raunakdey-07/mind-palace/releases/tag/v0.4.1
[v0.4.0]: https://github.com/raunakdey-07/mind-palace/releases/tag/v0.4.0
