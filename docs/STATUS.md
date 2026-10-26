# Project status

The current, verifiable state of Mind Palace and everything still outstanding.

Every figure here names the command that produced it and the commit it was
measured at. A number that was not measured is marked **not established** rather
than filled in — this table previously carried a test count that no run had ever
produced, and a test count that nobody ran is worse than no test count, because
a reader checks the others and assumes this one was checked too.

Corrections and additions measured in the v0.8.0 work are marked **[v0.8.0]**.

This is the authoritative summary. The documents under `docs/research/` are dated
records of individual experiments and are kept as they were written; where one of
them has been superseded by a later measurement, that is noted here rather than by
rewriting it.

## What the project is

A durable, versioned memory substrate for AI applications. It keeps original source
material, maintains authored knowledge over time, answers questions about what is
true now, what used to be true, what changed, what disagrees, and what evidence
supports each answer, and hands the result to an application as a bounded,
portable object.

The architecture has three layers and the separation is load-bearing:

```text
L0  source and verbatim versions      immutable
L1  authoritative knowledge            claims, evidence, validity,
                                        supersession, conflicts, snapshots
L2  derived retrieval                 embeddings, chunks, caches, indexes
```

L2 may be deleted and rebuilt without changing a single byte of authoritative
memory. That was verified by measurement, at `f52272d`; the table below says so
row by row, including the two rows whose harness no longer runs at this head.

## Release state

| | |
|---|---|
| package version | `0.9.2` (`pyproject.toml`) |
| latest tag | **`v0.9.2`** at `19863d8`. `v0.9.1` remains at `be1931a` |
| `v0.9.2` state | released: tagged on `main` and published as a GitHub Release |
| previous release | `v0.8.0`, unmodified |
| migration head | `012_claim_embedding_hnsw`, additive and reversible |
| tests | **2041 passed, 0 failed, 20 skipped** (as of current head, `python -m pytest -q`); 2061 collected, from the junitxml in `docs/performance/evidence/pytest-head.xml`. **The two skips that are opt-in and named remain allowlisted**: `MIND_PALACE_MIGRATION_ROUNDTRIP` and `MIND_PALACE_TEST_BUILD_WHEEL`. The additional skips are from `tests/test_embedder_backend_equivalence.py` (18) and `tests/test_search_semantics.py` (1), with reasons for missing optional dependencies (`[torch]`/both backends); `check_skips.py` accounts for them under the default-dependency set. Current head at `3b0562e` produced **2048 passed, 0 failed, 2 skipped** (as of `3b0562e`, 2050 collected). Previous head at `c65ffb2` produced **2032 passed, 0 failed, 2 skipped** (as of `c65ffb2`, 2034 collected); see the recorded run at `c65ffb2` in `docs/performance/test-runs.json`. Every earlier figure is labelled with the run that produced it, because `check_evidence.py --require-head --junitxml` now rejects an unlabelled figure that this run did not produce -- and it did, at run `38446879617`, for `1613`, `1619`, `1625` and `24`. Superseded figures, kept for the record: `1996 passed` as of `bd3cfc2`, `1992 passed` as of `c61ce10`, `1981 passed` as of `7465fe8`, `1637 passed` as of `d1a4612` (CI run `38446879617`), `1613 passed` as of `312958d` (CI run `38400659433`, 8 skipped) and `1619 passed` as of `c3c8252` (CI run `38410829513`, 8 skipped). Those 8 skips included 6 in `tests/test_ingestion_statistics.py` with the reason `no reachable DATABASE_URL`. **That reason was false and the cause is now established**: SQLAlchemy 2.1 resolves a bare `postgresql://` to psycopg (v3), which this project does not install, and `_db_available()` swallowed the `ModuleNotFoundError`. Those 6 tests now run on the runner, which is the entire difference between 1631 and 1637 here. `docs/performance/test-runs.json` carries the commit, the collected count and the junitxml the runner wrote. |
| lint | `flake8`, `black --check --line-length 100` clean |
| release metadata | `check_versioning.py` PASS, `check_evidence.py --strict` PASS |
| Memory Pack schema | version 1, unchanged since `v0.6.0` |
| receipt schema | version 1, unchanged since `v0.8.0` |
| environment | Python 3.14.8, PostgreSQL 15.19, pgvector 0.8.6, all-MiniLM-L6-v2 cached |

**The runner needs the models, and did not have them.** `sentence-transformers` is a
declared dependency, so a clean runner installs the *package* but not the *weights*.
With `HF_HUB_OFFLINE=1` and no cache, **41** tests fail on `OSError: We couldn't
connect to ...` — every test that indexes or ranks anything, plus 11 fixture errors
in `test_search_semantics.py`. `--maxfail=1` reported only the first, which is how a
green local run and a red CI run coexisted through a release.

The `tests` job now provisions both models explicitly, at **pinned revisions**, through
`scripts/ci/provision_models.py`:

| model | pin | why it is needed |
|---|---|---|
| `sentence-transformers/all-MiniLM-L6-v2` | `1110a243…` | the default read path |
| `cross-encoder/ms-marco-MiniLM-L-6-v2` | `233902d2…` | `rerank=true`, which `test_search_semantics.py` covers with no guard |

The embedder's pin is not invented — it is the revision recorded in the frozen
`eval/m00675/result.json`, so CI verifies the suite against the weights the released
benchmark result was measured with. The script also writes `refs/main` to the pinned
revision, because `model_fingerprint` reads that file to identify the model; without
it a pinned download would break the frozen benchmark's own model check.

Then, before any test runs, `--verify` loads both models **through the application**
(`Embedder`, `Reranker`) with `HF_HUB_OFFLINE=1`, and refuses to continue otherwise.
A missing model should cost one clear line rather than 41 failures forty minutes
later. After that, pytest runs offline and without `--maxfail=1`.

Those are two different requirements — the model must exist, and the tests must not
need the network — and conflating them is what hid the original gap. A local run on
a machine with the cache therefore proves less than it looks like.

`actions/cache` on `~/.cache/huggingface` makes a repeat run cheaper, keyed on OS,
Python version and both revisions. Measured: a hit provisions in ~1 s, a miss ~60 s,
and the snapshots total **1.7 GB** because they carry every format variant, not just
the safetensors. It is an accelerator only: a miss re-downloads from the Hub, and
`--verify` refuses a wrong or partial cache. No credentials are stored — both models
are public.

`tests/test_ci_model_provisioning.py` reads the workflow itself and fails if the
provisioning step is deleted, weakened, made unpinned, or duplicated into a second
copy in one job but not the other. `MIND_PALACE_LEXICAL=1` needs none of this: it
asserts that no part of the model stack is imported, and passes on a runner with no
models at all.

`v0.9.0` is a minor release and it is additive. No migration: the schema head does
not move. No public response field changed, existing receipts remain verifiable, and
no MCP tool was added.

**Read that in the past tense, about `v0.9.0` specifically. It is not a statement
about this branch.** `v0.9.0`'s head is 007. The branch's head is **012**, and
migrations 008-012 have **never shipped in any release** — they all
arrived in `da8a14a`, after the `v0.9.0` tag. So the first release cut from this
branch *does* move the schema head, by three migrations, and the "no migration"
sentence above does not apply to it.

**Upgrade notes are NOT YET WRITTEN. NOT ESTABLISHED.** No `docs/release/`
directory exists in this tree. What is established about the `v0.9.0` -> 010
upgrade, from `scripts/release/upgrade_safety.sh` and the runtime-role
assertions in `scripts/release/assert_runtime_role.py`:

* it applies cleanly to a real `v0.9.0` archive written through `v0.9.0`'s own
  `remember`;
* the `v0.9.0` receipt still verifies offline after the upgrade;
* claim identities do not move and no claim is lost;
* the pack is byte-identical once `valid_at` is pinned on both sides;
* supersession survives (history PostgreSQL, recall CockroachDB);
* the whole read surface works **as `mindpalace_runtime` in server mode with a
  credential**, and a policy keyed on the wrong column fails the job.

**A downgrade 010 -> 007 destroys `memory_principals` and disables row-level
security on all 9 protected tables. It preserves all 6 claims.** That cost belongs
in the upgrade notes and is not yet written down anywhere an operator would
find it.

**The runtime changed no memory semantics.** `mindpalace recall`, `explain` and
`receipt` went from about 6.2 s to about 0.63 s — figures measured on the
`sentence-transformers` backend, which was the default at v0.9.1 and is now the
`[torch]` extra — and everything they return is
byte-identical to the in-process path — same claim identities, evidence, temporal
state, conflicts, abstention, receipt digest and Memory Pack digest, across ordinary,
weakly related, absent, temporal, conflict and multi-topic queries. That is
`tests/test_m016_runtime_equivalence.py`, asserted rather than claimed.

Five behaviour changes, all deliberate and measured:

- **A first memory no longer requires an embedding model.** `remember` passes
  `require_embeddings=False`, so the embedder is never loaded rather than attempted and
  caught. `import sentence_transformers` costs 5.3 s on mains power, so reaching for a
  model that is already cached is not free either; the write does not reach. Bulk
  ingestion keeps `require_embeddings=True`, so a directory it cannot index is still a
  visible failure.
- **A read now persists the vectors it computed.** Since a corpus authored through
  `remember` starts with a cold read cache, `memory_query` writes back what it had to
  embed: 2,106 ms for the first read against 151 ms for the second. Contained by a
  `SAVEPOINT`, skipped on a read-only session, and best-effort, so it cannot fail a
  query that has already been answered. The cache holds no authority, so every one of
  those boundaries costs latency and nothing else.
- **Model-free reading is available on purpose.** `MIND_PALACE_LEXICAL=1` takes the
  lexical rung that already existed as the no-model degradation: 0.63 s instead of
  6.17 s, with the model stack never imported. Both figures are from the
  `sentence-transformers` backend, which was the default at v0.9.0 and is now the
  `[torch]` extra; the lexical number is the same on either. It selects different keys
  on vocabulary-poor questions, by design, and is documented as a trade-off rather
  than a substitute. See [operations](runtime.md#model-free-lexical-mode).
- **One failure, one exception class.** The in-process SDK path raised the service's
  own `MemoryError` while the runtime and HTTP paths raised `MemoryClientError`. All
  three now raise `MemoryClientError`, with the same code, message and status. A genuine
  defect is still not disguised as an API failure.
- **The relevance acceptance gate tests overlap against full claim terms.** Shared
  terms still cannot discriminate between candidates, so ranking is unchanged; but
  they are not evidence that a question is off-topic. The frozen memory benchmark
  holds at 1.000 on every metric with zero safety failures, before and after.

### The `memory` commands need no server either

`mindpalace memory pack` used to default to `http://127.0.0.1:8000`, while
`recall`, `explain`, `history`, `receipt` and `ingest-repo` all ran in-process.
The quickstart's bounded-context command was therefore the one core command that
failed with a connection refusal unless a server had been started by hand. Every
`memory` subcommand now uses the same in-process client as those commands when
neither `--base-url` nor `MIND_PALACE_BASE_URL` names a remote service; either one
still selects the HTTP client unchanged. `mindpalace serve --run` starts the HTTP
API for REST, the HTTP SDK, MCP over a network or another process, and without
`--run` it prints that command and says the CLI commands run in-process.

`uv.lock` is ignored: `uv run` writes it on first use, and a lockfile that is
re-resolved on every invocation is a local-development artifact rather than a
shipped input. Warm medians over five runs with no server listening, runtime
disabled, were `recall` 1.571 s, `explain` 1.789 s, `history` 0.975 s and
`memory pack` 0.949 s through `.venv/bin/mindpalace`; `uv run --no-sync` medians
were 1.627 s, 1.783 s, 1.041 s and 0.967 s, so the lockfile path adds no
re-resolution cost. Evidence:
`docs/performance/in-process-cli.json` <- key: summary.venv.recall.median_s

All of them are held by tests rather than by inspection, as is every property in
[product/first-use-audit.md](product/first-use-audit.md).

## Measured at v0.9.0

**The user journey.** One command per process, same corpus, mains power, median of
three, re-measured for this release under the same conditions as the `v0.8.0`
baseline. The left column is what every command costs with no background process at
all; nothing else differs between the columns.

| command | in-process | with runtime |
|---|---|---|
| `mindpalace init` | 0.65 s | — |
| `mindpalace remember` | 0.88 s | — |
| `mindpalace recall` | 6.17 s | **0.63 s** |
| `mindpalace explain` | 6.23 s | **0.67 s** |
| `mindpalace history` | 0.81 s | 0.61 s |
| `mindpalace receipt` | 6.21 s | 0.62 s |
| `mindpalace verify` | 0.56 s | 0.56 s |
| `recall`, `MIND_PALACE_LEXICAL=1` | **0.63 s** | — (bypasses it) |
| `explain`, `MIND_PALACE_LEXICAL=1` | **0.67 s** | — |
| SDK `recall`, fresh process, default | 5.11 s | — (`runtime` is opt-in) |
| SDK `recall`, `MindPalace(runtime=True)` | — | **0.51 s** |

The runtime itself: **5.39 s** cold start to `ready` with the model loaded, **0.05 s**
for a status round trip, **0.10 s** for a query over the socket with no CLI in the
path. Idle exit is 15 minutes.

Two things are worth reading out of that table rather than just the totals. First, a
warm `recall` costs about what lexical mode costs from a cold process, and neither is
0.1 s: the ~0.5 s that remains is interpreter startup and imports, which is the floor
for a one-shot command. Second, `explain` was the slowest semantic command in the
previous measurement and is now indistinguishable from `recall` — the difference was
measurement noise, not a second code path.

MCP is unchanged and correctly so: an MCP server is a long-lived process and pays the
import once. No MCP tool was added for the model-free mode, and none should be.

**Equivalence, measured rather than argued.** Reading through a warm runtime and
reading in-process produce byte-identical `current_memories`,
`historical_memories`, `uncertain_memories`, `changes`, `conflicts`, `constraints`,
`evidence`, `sources` and `truncated`. The one field that differs is
`state.valid_at`, and it must: it is "now", so two calls milliseconds apart record
different instants. That is correct behaviour, and it is why the equivalence tests pin
`valid_at`.

**Where the time actually went.** Profiling with `-X importtime` rather than
assuming: `import torch` 1.68 s, `import transformers` 1.20 s (2.66 s together),
`import sentence_transformers` 5.32 s, model construct and first embed 0.49 s.
Everything else in a one-shot recall — interpreter, CLI imports, database connection,
the query itself — is 0.74 s, and the memory work is 61 ms of that.

**Model-free reading.** `MIND_PALACE_LEXICAL=1` imports none of `torch`,
`transformers` or `sentence_transformers`, verified by inspecting `sys.modules` in the
process that ran the recall rather than inferred from a timing. The 0.63 s is
consequently the same figure the runtime reaches, from a cold process, with no
background process at all.

**A lexical *fast path* was measured and rejected.** On a ten-fact corpus across
sixteen question shapes it agreed with semantic ranking on fifteen, and the one
difference was an abstention: a question semantic ranking answered, lexical ranking
abstained on. Shipping it would have made `NO_RELEVANT_MEMORY` depend on whether the
model happened to be cached on that machine. Not built. An *explicit* lexical mode
ships instead, which is the same rung taken on purpose. See
[operations](runtime.md#why-not-a-lexical-fast-path).

## Measured at the five-minute path

**First-use path.** `mindpalace init → remember → recall → explain → history →
receipt → verify`, run as documented in a fresh environment against a fresh
database, offline. Asserted by `tests/test_m015_golden_path.py` and by a
dedicated CI job.

**Latency.** 200 remembered statements, on mains power: remember p50 10.8 ms /
p95 11.5 ms, recall p50 92.6 ms / p95 107.8 ms, explain p50 93.8 ms / p95 105.2 ms,
verify p50 0.82 ms / p95 0.93 ms in a separate interpreter with no database URL and
no network. At 1000 statements `remember` and `verify` stay flat and `recall`
reaches p50 988 ms, because the authoritative projection loads the version graph
before relevance narrows anything.

`remember` is 4× faster than at v0.8.0 because it no longer imports the embedding
model. The cost of importing it is the number that matters most at the process
level: `import sentence_transformers` is 5.3 s on mains (10.8 s on battery), so
`mindpalace remember` runs in
1.5 s while `mindpalace recall` runs in 14.4 s. Method and limits:
[performance/five-minute-path.md](performance/five-minute-path.md).

**Environment note.** Every latency number in this repository's history before
v0.9.0 was taken on battery. The two are not comparable: the same probe on mains
power is 3 to 4× faster. Compare within a condition, not across them.

**Idempotency.** An identical `remember` returns the same `version_id` and
`changed=false`. Four concurrent identical writes produce exactly one version and
one claim.

**Portability.** A bare `pip install mindpalace-os`, with no CLI extra and no
database URL, verifies a file written by `mindpalace receipt`.

## Invariants, and when each was last measured

Each row names the commit that produced its evidence. A row without a commit
was not measured at the current head, and quoting it as current would be the
same error as the `1583` figure this table used to carry.

| Invariant | Evidence | Measured at |
|---|---|---|
| Deleting every L2 row leaves authoritative memory byte-identical | 12/12 packs identical, one digest | `f52272d`. **Not re-verified at head — see below** |
| Rebuilding L2 restores the same digest | 12/12 packs identical, same digest within a run. **Caveat below:** the absolute digest is not reproducible across runs | `f52272d`. **Not re-verified at head — see below** |
| The Memory Pack reader needs no server | clean-venv import pulls in no heavy dependency | `f52272d` |
| Retrieval cannot create authority | 202 questions, 0 untrusted claims, 0 injection markers in claims or evidence | `f52272d` |
| Answers survive a missing model | 202/202 answered, 163 correct | `f52272d` |
| Answers survive a missing retrieval path | 202/202 answered, 163 correct | `f52272d` |
| Authoritative output does not depend on the claim cache | 202 packs identical with L2 destroyed including the cache | `f52272d` |
| Corpus isolation holds | `tests/test_corpus_isolation.py`, `tests/test_corpus_scope.py` | **re-verified: 24 passed** (as of `fd53840`, a subset run, not the full suite) |

**Why the first two rows are not re-verified, established this session.**
`scripts/benchmark/m011_rebuildability.py` builds a fresh per-run schema
(`CREATE SCHEMA "m011_<uuid>"`) and replays `migrations/versions/*.py` into it.
Migrations `008` and `009` create functions with `SET search_path = public`, so
at `CREATE FUNCTION` time PostgreSQL validates the body against `public` — which
in that database is empty. The run aborts:

```
asyncpg.exceptions.UndefinedTableError: relation "memory_principals" does not exist
[SQL: CREATE OR REPLACE FUNCTION mindpalace_resolve_credential(presented_hash CHAR(64))
```

The invariant itself may well still hold; what cannot be done is re-measuring it
with the harness as shipped. This is a defect in the harness-versus-migration
pair, not a statement about the archive: the same `search_path` pinning was
already found and documented in [the withdrawn record](withdrawn/README.md) as
breaking schema replay.

Evidence: `docs/performance/baseline-packs.json` <- key: set_digest

Frozen M006.75 evidence is byte-identical to `v0.6.0`, verified by diff. No
historical benchmark number was rewritten at any point.

**Caveat on the L2 rebuild digest [v0.8.0].** The invariant itself holds and is
re-verified: within a single run the digest is identical with L2 present, with L2
destroyed, and after L2 is rebuilt, and 12/12 packs match at every phase. The
*absolute* digest is not reproducible between runs, because
`scripts/benchmark/m011_rebuildability.py:145` derives `corpus_id` from a random
`uuid4()` and digests raw `canonical_json()` with **no `observed_at`
normalisation**. Two runs on unchanged code produce different digests
(`docs/performance/evidence/197-l2-digest-instability.txt`). This predates the
ANALYZE change and is a harness defect, not a product one: it is exactly the
non-determinism `scripts/benchmark/baseline_packs.py` was written to pin. Until
that harness is fixed, quote the *invariant* (identical across the three phases)
and never the absolute digest value.

## What a live CLI run against a real corpus found

The commands were run against real corpora -- `mindpalace history`,
`mindpalace recall`, `mindpalace ingest-repo` -- and four defects came out of
it. The fixes are measured, and the tests that would have caught each one are
in `tests/test_history_lineage_and_ingest_speed.py`.

**`history` walked the supersession edges backwards only.** The graph has an
edge in each direction and the reader followed one of them, so a question that
anchored on a *retired* claim found nothing to walk back to: the claim that
replaced it never entered the timeline, and the retired claim was printed under
`current now` as the one that is true now. The chain is now the connected
component of the supersession graph, in both directions and across documents.
The frame words (`change`, `changed`, `history`, `evolve`, `evolved`, `before`,
`previously`, `replace`, `replaced`) are dropped before the question is
matched, and a lineage is scored on the union of its tokens, so a question that
names the before and the after matches the whole chain.

**A table row lost the heading it sits under.** The relevance gate compared
the question against the claim's own words, and the word "SDK" was in the
heading above the table rather than in the cell. The heading is part of the
claim's context now, read from the chunk the claim was quoted from. It matters
where the claim's own key cannot carry it: a key holds a slug of its heading
path cut at 48 characters, so a deeply nested heading loses the words that
identify it.

**A wrapped sentence was archived as the half that carries no fact.** A
`Key: Value` statement ended where the next line opened with a capital, so
`the measured baseline: bundle sizes, first-paint and keystroke latency, PDF
and` was a complete claim. A line that stops on a conjunction or a preposition
is not finished, and the chunk boundary rule now follows the same rule.

**A table row that says nothing was a claim.** A table whose header row is
empty has no column to name a value by, so every row rendered as its own label
-- one claim per link in a table of documents. This repository's own `README.md`
carried sixteen of them.

**And a document holding the same table row twice was refused.** The evidence
quote was resolved against the first chunk that held it, and the same bytes
under two headers are two different claims. The evidence is quoted from the
lowest-order chunk that holds both the quote and the claim's own wording now.

### The ingest, profiled

`sync_repo` on this repository's `docs/`, 78 files, 1,814 chunks, 2,734 claims,
on a container-local PostgreSQL and the default ONNX backend:

| phase | before | after |
|---|---|---:|
| ONNX embedding, 256-token chunk texts | 59.8 s | 59.3 s |
| ONNX embedding, claim representations | 38.5 s | 32.6 s |
| `memory_claims` + `memory_evidence` writes | 5.8 s | 2.3 s |
| `memory_claim_embeddings` writes | 7.3 s | 5.0 s |
| `chunks` writes | 4.3 s | 4.5 s |
| **wall clock** | **125.6 s** | **112.3 s** |

The before column is `main`; the after column is this branch, and the branch
ingests **two more documents** than `main` does, because the two that used to
be refused now land.

**The honest part: the ingest is 80% embedding, and the embedding is at the
hardware's floor.** The claims, the evidence and the claim representations are
written in batches now -- they were written one row at a time inside a Python
loop, 4,974 statements for the 2,487 claims of a 78-document repository -- and
a representation whose hash is already cached is not encoded again. The
remaining time is the model: a 256-token MiniLM forward measures 322 GFLOPS
here, which is this machine's limit for fp32, and the per-text cost does not
improve past a batch of 8 (measured, interleaved, at batch sizes 4, 8, 16 and
32 -- the shipped constant of 8 is already at the knee). The vectors are
bit-identical across batch sizes, so this is a cost measurement and not a
change of behaviour. A 3x reduction would have to come from encoding less, and
the brief was explicit that the embeddings must not change.

### The chunk vectors, which nothing read

**Half the ingest was computing vectors for an index no read path queries.**
`recall`, `history`, `explain` and `receipt` all read
`memory_claim_embeddings`. None of them reads `chunks.embedding`. The chunk
vectors are the legacy chunk-level index, and a document that produced claims
was indexed twice: once as claims, which is what the product reads, and once
as 1,200-character chunks, which cost 256 tokens each to encode.

A document that produced no claims is the other case, and it is not the same
case. Such a document contributes no claim, so the chunk index is the only
index it has, and it keeps its vectors. `mindpalace ingest-repo --embed-chunks`
asks for the vectors for every document, and `mindpalace reindex` rebuilds
them from the archive for a corpus that was synced without them.

**The chunk index is not dead, and the search says so by working.** A search
gives the chunks that have no vector one before it looks, so the chunk-level
search answers for a corpus that was synced without them instead of returning
nothing for a corpus full of text. The vectors are the ones an ingest would
have computed -- the chunk's own text, through the same embedder -- and the
chunk texts are immutable, so a chunk gets the same vector either way.

The profile below is a different corpus set and a different machine from the
one above, so the two tables are not comparable to each other. Two corpora:
this repository's `docs/` (78 files) and a second repository's `docs/` (7
files), 85 files, 2,097 chunks and 3,907 claims, on a container-local
PostgreSQL and the default ONNX backend:

| phase | before | after |
|---|---|---:|
| ONNX embedding, chunk texts | 72.8 s | 0.6 s |
| ONNX embedding, claim representations | 44.9 s | 34.1 s |
| `chunks` writes | 6.1 s | 1.8 s |
| `memory_claim_embeddings` writes | 6.9 s | 6.4 s |
| `memory_claims` + `memory_evidence` writes | 3.4 s | 3.8 s |
| **wall clock** | **147.0 s** | **59.7 s** |

The before column is `main` and the after column is this branch, both measured
on the same machine over the same files. Reproduce it with
`scripts/benchmark/ingest_profile.py`, which times every phase where the work
happens, so the phases add up to the wall clock instead of describing it.

**Two more things were measured, not guessed.** The ONNX session now asks for
every graph optimisation the runtime knows about, and it gets one thread per
*physical* core rather than per logical processor: onnxruntime's default is
one per logical processor, and on a machine with SMT the two halves of a core
fight for the same execution units. On the 4-core/8-thread machine this was
measured on, a batch of eight claim representations took 58.9 ms with the
default and 38.7 ms with one thread per physical core. And the batches are
formed from the token lengths rather than the character lengths, because a key
like `md:cv-embed-performance-baseline-bundle-sizes-21723ee4` is a different
number of tokens than it is characters: over the 2,178 claim representations of
one corpus, sorting by tokens padded 63,093 positions where sorting by
characters padded 71,989, and encoded them 13% faster.

**No vector changed.** The claim vectors are byte-for-byte identical before and
after: the digest of every stored vector of both corpora is the same, 2,750
rows and 1,157 rows. The mask zeroes the padding and the pooling divides by
the unmasked length, so what a text is padded to does not change what it says,
and the thread count changes the order the reductions happen in and not their
result. The batch size, the batch order and the thread count were each
measured to leave the vectors bit-identical.

### The connections, two per document

**A sync opened a new PostgreSQL connection for every document, twice.** The
engine is `NullPool`: a pooled connection cannot cross an event loop, and the
SDK and the CLI create a short-lived loop per operation, so every session was a
new connection. `sync_repo` opened two sessions per document -- one to ask
whether the path was already indexed, one to ingest it -- so a 78-document
repository paid for 156 connections.

The cost is per connection, so it scales with the distance to the database.
Against the container-local PostgreSQL this repository is developed against, one
connection measures 28 ms and the 156 of them cost 4.4 s. The reported
**11.8 s** is the same shape measured against a remote server.

The run now holds one connection for its whole length, and the transaction
boundary does not move: each document still opens and commits its own
transaction, so a document that fails is rolled back on its own, the connection
stays usable and the run carries on. The tenant scope is written per
transaction and is transaction-local, so a reused connection cannot carry one
document's scope into the next.

Measured over this repository's `docs/` and a second repository's `docs/`, on a
container-local PostgreSQL and the default ONNX backend:

| phase | before | after |
|---|---|---:|
| connection setup | 4.91 s | 0.15 s |
| ONNX embedding, chunk texts | 0.42 s | 0.41 s |
| ONNX embedding, claim representations | 25.01 s | 25.33 s |
| `chunks` writes | 1.07 s | 0.25 s |
| `memory_claim_embeddings` writes | 4.81 s | 4.39 s |
| `memory_claims` + `memory_evidence` writes | 2.55 s | 1.70 s |
| **wall clock** | **42.7 s** | **33.9 s** |
| connections opened | 174 | 4 |

The before column is `main`; the after column is this branch, both measured on
the same machine over the same files, with
`scripts/benchmark/ingest_profile.py`. The `mind-palace` corpus alone goes
from **34.3 s to 25.7 s**, and its connection setup from 4.40 s to 0.04 s.

**Nothing in the archive changed.** The claims, the versions, the chunks and
every claim vector are identical before and after, by digest: the same four
digests in both runs, over 2,750 claim rows for this repository and 1,157 for
the second.

**And the benchmark could erase the corpus it measured.** It wrote into the
corpus its command line named, and `--reset` erased it, so a benchmark run
over `--corpus mind-palace=docs` wiped the live `mind-palace` corpus outright.
The name on the command line is a report label now; the run writes into a
corpus whose name begins with `_profile_` and deletes it when the run ends.

### A table row, and the part of the question that lost the subject

`mindpalace recall "What is the gzipped bundle size and keystroke-to-preview
latency of the SDK?"` answered with the bundle sizes of two other chunks. The
SDK's own row was fifth, and the 8,000-character response budget keeps four
claims, so the reader never saw the row that answers the question.

**The question is split into parts before it is scored, and the first part
loses the subject.** "What is the gzipped bundle size?" names no component at
all. Every row of the bundle-size table covers that part equally, so the part's
coverage cannot tell the rows apart, and the row that answers the question was
ordered among its siblings by the embedding's opinion of an unrelated sentence.
Measured against the question as a whole, the SDK's row covers one term its
siblings do not, and it is the answer.

**The coverage is measured against the question now, not against the part that
found the claim.** A part is a lens for finding the claims, not a smaller
question. The budget is unchanged: it is still the Unicode characters of the
complete canonical JSON, and the claim that answers the question is the one
that survives it.

Against the real `cv-embed` documents, the two facts the question asks for
come back in the first two claims, the unrelated rows of the same table after
them, and the response is 7,233 of the 8,000 characters with nothing dropped.

Nothing else moved. The frozen M006.75 set is identical on all sixty
questions, field for field, and the 202-question development set flips no
question.

### A corpus that goes stale, and the two ways out of it

**A file removed from a curated directory stayed active in the corpus
forever.** Its claims kept answering questions, and nothing reported that the
source was gone. `mindpalace ingest-repo` hardcoded `delete_removed=False`, so
the service could retire a document and the CLI never asked for it. The flag
is `--delete-removed`, and the default is unchanged: a sync that retires
documents is a decision, and the flag is how the caller makes it.

**A corpus that had collected a test claim needed a clean slate**, and the way
out of that was to drop the whole PostgreSQL database through `podman exec`,
which took every other corpus in it with the one that was dirty.
`mindpalace corpus reset` erases one corpus and re-initialises it under the
same name and the same id, with its grants intact. It asks for `--yes`,
because the erasure cannot be undone.

## Correctness

**An accuracy figure is not the useful number.** "131/157" averages a right answer
together with a wrong one, and an integrator cannot tell from it whether they can
act on an answer without re-checking it. The breakdown below separates the four
outcomes. It is produced by `scripts/quality_frame.py` from the two frozen
artifacts and written to `docs/performance/quality-frame.json`:

```bash
python scripts/quality_frame.py
```

| Outcome | held-out v1 (n=157) | frozen M006.75 (n=60) |
|---|---:|---:|
| correct | 116 (73.9%) | 37 (61.7%) |
| abstained when answerable | 11 (7.0%) | 5 (8.3%) |
| answered wrong | 14 (8.9%) | 8 (13.3%) |
| abstained correctly | 15 (9.6%) | 10 (16.7%) |
| **answered when it should have abstained** | **1 (0.6%)** | **0 (0.0%)** |

Evidence: `docs/performance/quality-frame.json` <- key: heldout_v1.confident_wrong

**The bottom row is what decides whether this is safe to build on.** One question
in 157, and zero in 60: the system almost always declines rather than inventing.
That is the property that makes a memory subsystem worth having, and no aggregate
shows it.

**But read the runner's own note before taking credit for that 1.** The question
`ho-abs-office-cat` expects no memory, the system returned nothing, and the
frozen grader recorded it as incorrect (`expect_state=ABSENT`, `failure='F7'`).
Returning nothing when nothing exists is the correct behaviour, and
`docs/performance/quality-frame.json` says exactly that in its own `notes` field.
**On held-out v1 the product's confident-wrong count is 0; the grader's is 1.**
The disagreement is unfixed and is counted as a grader defect, not a product one.

**The costs are real and they are not small.** 11 held-out questions where the
archive had the answer and the system returned nothing, and 14 where it returned
something wrong. That is 25 of 157 — roughly one held-out question in six is a
product limitation rather than a success.

Two caveats stated rather than buried:

* One held-out question (`ho-abs-office-cat`) expected no memory, returned no
  memory, and was graded incorrect by the frozen grader. It is counted as an
  abstention above because the behaviour is the behaviour, but the grader and this
  table disagree about it, and that disagreement is itself unfixed.
* The frozen artifact records `exact = 47`. The reproducibility report says the
  current source produces 45, and did not already produce 47 at `v0.8.0`. The
  rows above are computed from the artifact as stored, so they are the artifact's
  numbers, not the current source's.

Evidence: `docs/performance/m0130-heldout.json` <- key: summary.strategies.C_fallback

### The next held-out set is written and frozen, and unscored

`docs/performance/heldout-v3-freeze.json`, dataset hash
`d52545673ce864a9d774417085dc05f1a1d1df9be5b625107d088d42866175fb`:
**45 questions**, 31 expecting a current fact, 3 historical, 1 conflicting, 10
where the correct answer is silence. Regenerate with
`python scripts/freeze_heldout_v3.py`; the hash is over the canonical question
rows, so editing a question changes it. **It is not an independent set** -- see
[heldout-v3-README.md](performance/heldout-v3-README.md).

Named **v3**, not v2: `docs/performance/heldout-v2-freeze.json` already exists,
32 questions, frozen 2026-10-04 and already scored. A second set called "v2"
would make the identifier mean two things.

Authored from the ten maintainer questions in
[the first-hour audit](first-hour-audit.md) and from the question shapes the
measured failure classes predict, paraphrased rather than quoted. No retrieval
code was read while writing them. **It has not been scored.** Scoring happens
once, at one commit, and any retrieval change tuned against it is a development
result that has to be reported as one.

The aggregate figures remain, because the release documents have always quoted
them and removing them would break citations in the changelog:

### Three scores, and they are not the same kind of number

**Read this table before quoting any single figure from it.** They differ in what
was tuned on them, and one of them has been used for diagnosis.

| set | n | score | what it is |
|---|---:|---|---|
| **development** | 202 | **194/202 (96.0%)** | **DEVELOPMENT SCORE. Not an accuracy on unseen questions.** Retrieval, ranking and thresholds were all tuned against these 202 questions. Eight known failures remain and are listed individually below. |
| **held-out v1** | 157 | 131/157 (83.4%, Wilson 95% CI 76.8%-88.6%) | Authored independently and frozen before retrieval code was touched, then **used to diagnose failures**. Any fix tuned against it is a development result. Treat as a diagnostic instrument, not a measurement. **Burned.** |
| **frozen M006.75** | 60 | 45/60 exact, 55/60 abstention-exact, 0 execution failures, all seven safety invariants 60/60 | The least-tuned set here, and still only 60 questions. It is the one set that has not been used for diagnosis. |

**194/202 is a development score and must never be presented as the product's
accuracy.** Quoting it alone, or in a headline, overstates the evidence by the
13-point gap to held-out v1 and by more than that to any set nobody has tuned
against — which does not exist. The four-way breakdown further down is the part
that matters operationally.

The canonical authored benchmark, 202 questions, dataset hash
`9cdecda73cbdc8ae`:

| Strategy | Score | |
|---|---:|---|
| semantic always | 178/202 | development |
| lexical always | 164/202 | development |
| lexical, then semantic | **194/202** | **development — not a held-out result** |

Evidence: `docs/performance/m0115-experiments.json` <- key: summary.strategies.C_fallback

### The four-way breakdown of held-out v1

131/157 averages a right answer together with a wrong one. The table in
**Correctness** above splits the same 157 into the four outcomes an integrator
would have to act on, and that is the canonical statement. Two things about it
belong here as well, next to the aggregate:

**The costs are real and they are not small.** 11 held-out questions where the
archive had the answer and the system returned nothing, and 14 where it returned
something wrong. That is 25 of 157 — roughly one held-out question in six is a
product limitation rather than a success.

**The confident-wrong count is 1 on held-out v1 and 0 on the frozen set — and the
runner says the held-out 1 is a grader disagreement, not a product failure.** The
question `ho-abs-office-cat` expects no memory, the system returned nothing, and
the grader recorded it as incorrect (`expect_state=ABSENT`, `failure='F7'`).
Returning nothing when nothing exists is the correct behaviour. The runner's own
note in the artifact says so, and this document repeats it rather than quietly
taking the credit: **on held-out v1 the product's confident-wrong count is 0, and
the grader's is 1.**

The eight failures are fixed and classified, not a moving target:

| Question | Class | Cause |
|---|---|---|
| Why was the primary datastore chosen? | F7 | ranking |
| What is the primary datastore and why was it chosen? | F7 | ranking |
| A vendor brief asserts a datastore. What is the current shared cache? | F7 | ranking |
| Who caters the team lunch? | F2 | answered from an unrelated key |
| Which identity provider is used for SSO? | F2 | answered from an unrelated key |
| What constraint did the March orders outage introduce? | F9 | ranking |
| Which service owns object storage decisions? | F9 | no authored relation exists |
| Which decision rationale covers the current job queue? | F9 | vocabulary gap |

### Held-out question set **[v0.8.0] — analysed, no longer blind**

A held-out set of **157** questions was authored independently, frozen at dataset
hash `13030d897faabe4be` before any retrieval, conflict or projection code was
touched, and scored once. Phrasings differ deliberately; zero texts are shared
with the development set.

Evidence: `docs/performance/m0130-heldout.json` <- key: meta.dataset_hash

**It has now been used to diagnose failures, so results from any fix tuned against
it are development results. Treat held-out v1 as a diagnostic instrument, not as a
held-out measurement. A second set is required before quoting accuracy again.**

| Strategy | Development (202) | Held-out v1 (157) |
|---|---:|---:|
| semantic always | 178 | 124 |
| lexical always | 164 | 115 |
| lexical, then semantic | **194/202** | **131/157** |

Evidence: `docs/performance/m0130-heldout.json` <- key: summary.strategies.C_fallback

Per class, held-out lexical-then-semantic (correct / asked):

| class | n/N | % |
|---|---:|---:|
| current | 30/30 | 100.0 |
| historical | 23/23 | 100.0 |
| provenance | 22/26 | 84.6 |
| relationship | 14/16 | 87.5 |
| abstention | 15/16 | 93.8 |
| conflict | 2/2 | 100.0 |
| adversarial | 7/8 | 87.5 |
| temporal | 11/17 | 64.7 |
| multi-topic | 7/19 | 36.8 |

The 12.5-point gap between 96.0% and 83.4% is the result, and it has three
identified parts:

- **19 of 26 failures are F7, lexical retrieval**, clustered on asking for the
  *reason* behind a decision or what a *rule* or *release* introduced. The
  development set phrases these with wording that overlaps the authored keys; the
  held-out set phrases them indirectly and they are missed. A real weakness.
- **multi-topic lexical 0/19 is a scoring artefact, not a retrieval gap.** The AND
  matcher at `api/services/memory.py:528` requires *every* query token to appear
  in a single claim, so a question containing "summarise" or "relies" can never
  match. Nothing is retrieved, and nothing is ranked. This was confirmed by
  reading the scorer, not inferred from the score.
- **The two relationship failures share a failure code but have unrelated causes**,
  so that code must not be read as one problem.

Per-class percentages are given with n/N because several classes are small; a
per-class percentage on 2 questions carries no information.

### Latency

**[v0.8.0] The latency table below is currently NOT ESTABLISHED and must not be cited.**

The figures previously published for 100 and 1,000 claims (43 ms and 842 ms) could
not be reproduced, and do not appear in any artifact. `docs/performance/status-latency.json`
held 99.57 ms and 2,308.49 ms for those sizes while STATUS.md cited that file as
their source, so the citation did not resolve to the numbers it claimed. Both the
JSON and this document are untracked, so git history cannot adjudicate what any
artifact said at `f52272d`.

What is established: two separate measurement sessions on this host landed roughly
2.5x apart on identical work, with pure-Python projection inflating by the same
factor as the database stages. What is **not** established is any cause. The
explanations offered so far — background load, thermal or frequency effects, disk
or memory pressure — are hypotheses, and one of them ("transient host load") was
explicitly withdrawn after a diagnostic found the host at 21% utilisation with
87-94% idle CPU at the time it was proposed as the explanation. The host state
recorded at that diagnostic is in `docs/performance/evidence/140-loadavg.txt`.

The harness now refuses to publish a point when the one-minute load average
exceeds a threshold (default 0.5, `--max-load`), records the observed load in the
artifact, and exits non-zero rather than writing a baseline. It also measures each
size in its own process by default. Both changes exist because a loaded host
produced an artifact that was read as a clean baseline.

Retained from earlier measurements, for shape only and not as citations:
projection share rises with size, and pack construction is ~0.2 ms at every size
measured, which is not a cost. Every latency number must be re-measured on a host
whose load gate passes before any of it is quoted.

Note also that with 12 samples the harness's reported p95 is the index nearest the
95th percentile and is in practice the maximum. It is a worst observed value, not a
tail estimate.

## Remaining limitations

Ordered by how much they should worry an adopter.

### 1. Projection does work that grows faster than the corpus **[v0.8.0]**

The strongest evidence is a **call count**, not a stopwatch, because call counts do
not move when the host is busy. Between 5,002 and 25,000 claims on the scale fixture:

| function | 5k calls | 25k calls | ratio | exponent |
|---|---:|---:|---:|---:|
| `_hash` (once per conflict) | 13,481 | 353,685 | **26.24x** | **2.03** |
| `_canonical` / `json.dumps` | 70,592 | 1,498,068 | 21.22x | 1.90 |
| `matches` / `re.findall` | 21,815 | 395,349 | 18.12x | 1.80 |
| Pydantic `Claim.__init__` | 33,490 | 453,686 | 13.55x | 1.62 |

The term is the conflict scan at `api/services/memory.py:553`,
`combinations(active_by_key[key], 2)`. Conflicts equal the pairs that survive the
filters, so the `_hash` count tracks it exactly. Pydantic validation grows 13.55x,
close to claim count, so **model construction is not the term**.

**Wall-clock exponents are not published.** Two runs of the same profile produced
1.11 and 1.60 for the same function on this host (`docs/performance/m0131-projection.json`
against `m0131-projection-extra.json`). The difference between them is not
established, and no exponent is claimed from wall time here.

**Substantially an artefact of the synthetic corpus.** The scale fixture spreads
filler over a constant 295 distinct keys regardless of size
(`scripts/benchmark/m0117_scale.py:89`), so claims per key grow linearly and the pair
count grows quadratically. The largest single key group reached 86 claims, or 3,655
pairs, from one key. The quadratic is real; how much of it a real archive with a
wide key distribution would show is **not established**. The fixture is retained and
labelled a dense-key stress case.

### 2. The archive load has a query plan cliff, and its cause is identified

The cause is **absent table statistics**. `memory_claims` and `memory_evidence` have
`reltuples = -1` and `last_analyze = null` after migration and ingest: nothing in
the product ever runs `ANALYZE`. The planner therefore underestimates the row count
by a constant factor of about **250x** (3 estimated rows against 751 actual at
N=750; 4 against 1,000 at N=1,000), and chooses an index on `corpus_id` alone,
applying `version_id` as a filter and discarding 999 of every 1,000 rows.

| versions | estimate error | shared hit before ANALYZE | after ANALYZE | reduction |
|---:|---:|---:|---:|---:|
| 500 | ~250x | 266,470 | 4,584 | **58.1x** |
| 750 | 250.3x | 598,772 | 7,735 | 77.5x |
| 1,000 | 250.0x | 1,078,311 | 10,311 | **104.6x** |
| 1,500 | 250.2x | 19,556 | 15,249 | 1.3x |
| 2,000 | 250.2x | 28,074 | 22,331 | 1.3x |

**The damage is size-dependent, and this matters for how the bug is described.**
The 250x row-count underestimate is present at *every* size tested, but it only
changes the chosen plan below roughly 1,200 versions. At 1,500 and 2,000 versions
the planner picks the composite `(corpus_id, version_id)` index even with
`reltuples = -1`, because its default heuristic estimate scales with table size,
so ANALYZE is worth only ~1.3x there. The cliff is therefore bounded above at
roughly (1100, 1200] versions and the severe case sits near 1,000, which is where
it was first measured. An earlier draft of this document implied the defect
applied at all sizes; that was an overstatement and is corrected here.

Two indexes are involved and they flip independently:
`idx_memory_claims_validity` drives the claims side up to 1,000 versions and
`idx_memory_evidence_claim` drives the evidence side up to 1,100. It reproduces
through the real production path (`execute_in_session` → `_load`).

ANALYZE removes the cliff and is **authority-neutral**: 202/202 packs
byte-identical with the same set digest `5a3b8049…` measured across commit
`cda4d58` against the baseline captured at `f52272d`
(`docs/performance/evidence/195-verify-packs-after-commit.txt`).

Ruled out with evidence:

- **Not fixable by rewriting the query.** Seven variants were tested, each verified
  byte-identical to production across 89 variant-by-size cells. None removes the
  cliff. The decisive test narrowed the planner's search space to index scans only
  (`enable_indexscan=off`, `enable_bitmapscan=off`) and the planner *still* chose
  the corpus-only index with identical cost. That makes this an **estimation
  failure, not a search failure**: no SQL can reach an estimator that rates one
  index probe plus 999 discarded heap fetches as cheaper than a composite probe.
- **Not a prepared-statement or generic-plan problem.** `auto`,
  `force_custom_plan` and `force_generic_plan` all select the same index and cost
  the same 1,068,313 shared hits at 1,000 versions. This matters, because a previous
  rewrite was rejected for exactly that failure mode, so the fix here does not
  inherit that risk.
- **Not a missing index.** `(corpus_id, version_id)` is already the leading
  columns of the existing unique index `memory_claims_corpus_id_version_id_id_key`.
- **`default_statistics_target` is inert.** It changes nothing, permanently, because
  it only takes effect at the next collection and nothing collects.

**Severity is lower than it first appears.** This host's server has
`autovacuum=on`, `analyze_threshold=50`, `analyze_scale_factor=0.1`, `naptime=60s`,
and the self-healing was **measured directly** at t+55s on a committed corpus:
bad plan at ingest, all four tables auto-analysed, plan corrected, without any
threshold override. So on any install where rows are committed and autovacuum
runs, the cliff lasts about a minute. It persists where autovacuum cannot keep up
or does not run: a bulk load, a restore, or a database with autovacuum disabled.
Nothing in this repository disables autovacuum.

**The fix is committed** at `cda4d58`, as
`IngestionService._refresh_planner_statistics`, called once per bulk sync from
`sync_repo` after the per-document transactions have committed. It is bounded
(skipped below 200 touched rows), takes the corpus advisory lock so it cannot
interleave with a writer, is non-fatal (a failure is logged, never raised),
adds no migration and no public response field. Covered by
`tests/test_ingestion_statistics.py`, whose planner assertion was mutation-tested:
stubbing the hook to `return None` makes it fail on the index choice and the
shared-buffer ceiling, and it passes again once restored
(`docs/performance/evidence/194-mutation-proof.txt`).

Separately and independently, the sort spills to disk from about 3,000 versions
(external merge). Raising `work_mem` to 256 MB removes the spill and cuts the
5,000-claim load by 1.49x with identical buffer counts, and does nothing at 1,000.
Spill and index cliff are different effects.

Not established: whether the projection behaviour above the cliff boundary changes
under ANALYZE. Authority-neutrality was measured on the 88-claim corpus, row
identity was measured at 500-2,000, and pack identity across the commit, but the
full 202-question projection was not re-run on a large corpus on both sides.

### 3. Conflict detection is keyed, and that is visible to users

Two claims that contradict each other are detected only if they share an authored
key. The benchmark corpus deliberately contains a contradicting ADR under a
near-identical but different key. The system reports a resolved answer and does not
label the drift.

This is the difference between "no general contradiction detection" as a caveat and
a concrete case a user could hit.

### 4. Relationship reasoning is narrow

31 of 34 relationship questions are answered. The three failures are two ranking
weaknesses, one of which is a vocabulary gap where "rationale" cannot reach a claim
keyed on "reason", and one where no authored relation exists at all.

No multi-hop relationship reasoning is claimed or supported.

### 5. A handful of gates are unmeasured

Deleting a corpus used to return 409 whenever the archive referenced it, so a corpus
that had ever recorded a fact could never be removed. That is now implemented:
migration `009` adds `mindpalace_erase_corpus`, one SECURITY DEFINER function that
re-checks the caller's transaction-local tenant scope, takes the advisory lock every
writer takes, and removes all nine archive tables in dependency order plus the live
projection and every credential's grant. The runtime role holds no `DELETE` on the
archive, so that function is the only path; the append-only triggers still refuse
every ordinary `DELETE`, `UPDATE` and `TRUNCATE`.

What it does **not** remove is unchanged and stated: receipts and Memory Packs a
customer exported, database backups, and copies they stored elsewhere. There is no
cryptographic erasure and no TTL or purge — rows are deleted, not shredded — so
backup retention remains the operator's obligation and its window is the known cost.
See [security.md](security.md#data-lifecycle-deletion-erasure-and-what-survives).

Authentication and authorization are built for shared deployments and are off by
default: `MIND_PALACE_DEPLOYMENT=server` (which refuses to start unless
`MIND_PALACE_AUTH_MODE=required`, `MIND_PALACE_INGEST_ROOTS` are set and the connected
database role actually enforces the policies) plus a least-privileged runtime role.
An unrecognised profile value is refused rather than reinterpreted. Corpus namespaces
remain *not* authorization on their own, and an owner is not a tamper-proof boundary.
What changed is that naming a corpus is no longer the decision — a bearer credential
is, and PostgreSQL row-level security is beneath it, on every surface: REST, the
Python SDK, the local runtime socket and MCP. Roles are finer than read/write:
`corpus_admin` can erase the corpora it holds grants on, `principal_admin` can issue
credentials, and a workspace credential can no longer mint authority.
`mindpalace posture` reports whether the connected role enforces the policies, can
enumerate credential hashes, or can delete from the archive. `/health/ready`
reports only the verdict — `503` when the policies are not in force — because it is
read by whatever probes the service. See [security.md](security.md).

### 6. The wheel no longer ships research tooling **[v0.9.1] — resolved**

`scripts/` was in the distribution's `include` list from `v0.6.0`. Measured: **54
files, 590 KB of 1,328 KB unpacked — 44% of the distribution** — of which 48 were
`scripts/benchmark/`. Nothing in `api/`, `cli/` or any top-level module imports it;
the three test modules that do (`test_m009_feed_integration`,
`test_verifiable_memory_release`, `test_m00675_reproducibility`) run from a checkout,
where `scripts/` is present.

The wheel is now **227 KB / 74 files, down from 425 KB / 129**. The repository still
ships those scripts, which is where `check_versioning.py`, `check_evidence.py` and the
benchmark harnesses run from, so reproducing the published numbers still means cloning
— which is what the instructions already said.
`tests/test_release_packaging.py` keeps the boundary shut.

### 7. A cached read still pays the model import **[v0.9.1] — measured, not changed**

A semantic read whose claim vectors are *all* already cached still costs ~6.6 s on
mains, against ~0.67 s for the same read with `MIND_PALACE_LEXICAL=1`. The cause is a
single argument: `memory_query.py:267` passes `embedder.dimension` to `load_cached`,
and `Embedder.dimension` calls `_get_model()` — so the read imports
`sentence_transformers` purely to learn a number.

`memory_claim_embeddings.embedding_dimension` already stores it, so the information is
available without the model. **Not changed in this release.** The read path is the
authoritative one, and a dimension that disagreed with the configured model would
silently corrupt ranking rather than fail. A patch release is the wrong place to take
that risk on evidence this thin. It is a real saving for anyone not using the runtime,
and it is recorded here as a candidate rather than quietly taken.

### Python 3.15 lazy imports **[v0.9.1] — evaluated, not adopted**

Measured on CPython 3.15.0rc1; the full table is in
[the release map](release-map.md#python-315-lazy-imports-were-evaluated-and-not-adopted).
The short version: the model stack costs 5.61 s to import against a 0.11 s CLI import
graph, and `sentence_transformers` is *already* deferred inside
`Embedder._get_model()`, so lazy imports have nothing to remove on the path that
dominates. `-X lazy_imports=all` additionally breaks real commands with a partial
SQLAlchemy initialisation. Nothing shipped; the supported floor stays `>=3.11`.

## Rejected work, with the measurement that rejected it

Recorded so the same ground is not covered twice.

| Candidate | Result |
|---|---|
| Python 3.15 lazy imports (narrow and global) | Model stack is 5.61 s of a ~5.7 s non-interpreter cost and is already deferred inside `Embedder._get_model()`; the addressable surface is ~2%. `-X lazy_imports=all` breaks SQLAlchemy dialect init |
| Set-based archive load rewrite | Removed the quadratic scan, but a generic-plan cliff made it 12x worse in production |
| Candidate narrowing before projection | 170/202 equivalent answers |
| Per-document current-version pointer | 3.6 ms of a 20,894 ms query |
| Vectorised relevance scorer | 202/202 packs identical, but 1.00x to 1.07x end to end |
| Aggressive bloom-filter pushdown | Folded into the narrowing work above |
| Loading only the columns the projection reads **[v0.8.0]** | 202/202 packs byte-identical, and 0% to 0.6% end to end at every size measured. Kept as a payload reduction (286 KB vs 2.63 MB per 1,000 versions), NOT as a speedup. The `EXPLAIN` control now explains the null: row width is not the critical path, the aggregates are |
| The explanation "transient host load" for the contaminated latency rows **[v0.8.0]** | Withdrawn twice. It was an inference presented as a finding, and a later diagnostic contradicted it: the host was at 21% utilisation with 87-94% idle CPU when that explanation was proposed. What survives is the observation that two sessions landed ~2.5x apart; the cause is not established |
| The claim "the 100 and 1,000-claim latencies were 43 ms and 842 ms" **[v0.8.0]** | Withdrawn. Those numbers are in no artifact. The artifact STATUS.md cited contains 99.57 ms and 2,308.49 ms for those sizes |
| Rewriting the archive-load query to dodge the plan cliff **[v0.8.0]** | Seven variants, all byte-identical to production across 89 cells, none removes the cliff. With the search space narrowed to index scans only the planner still picks the corpus-only index. It is an estimation failure; only statistics fix it |
| Pre-grouping the aggregates into derived tables to dodge the cliff **[v0.8.0]** | Kills the cliff at N=1000 (437x fewer buffers) but relocates an equally large cliff to N=100-300, where the planner substitutes the pre-aggregate as a correlated inner and re-runs the GROUP BY once per outer row. Plan-unstable for the same underlying reason |
| `WITH ... MATERIALIZED` pre-aggregation **[v0.8.0]** | Lowest buffer counts measured (2,457 at N=1000) yet 30.9x SLOWER at N=1500. The CTE is joined back by Nested Loop with `Actual Loops = N`, an O(N^2) tuple comparison that never touches a shared buffer, so buffer counts actively hide the cost. A reminder that shared buffers alone are an incomplete metric |
| `default_statistics_target` to improve the estimate **[v0.8.0]** | Byte-identical plan at all 12 sizes. Inert permanently: it only applies at the next collection, and nothing collects |
| `SET LOCAL enable_indexscan=off` **[v0.8.0]** | 7.6x fewer buffers at N=1000 but still 4.8x slower than the best variant, and it is a connection-wide GUC that would disable index scans for unrelated queries. Kept as a diagnostic control only |

## Measurement caveats learned the hard way **[v0.8.0]**

Three of these cost real time and are recorded so they are not repeated:

- **Repeated measurements of the same work have landed about 2.5x apart on this
  host.** The cause is **not established**; treat it as observed variance rather
  than a characterised property of the machine. Latency numbers are not usable
  without the load gate, and the harness now refuses to publish above a threshold.
  A diagnostic taken at the point this was investigated recorded the host at 21%
  utilisation with 87-94% idle CPU, which rules out sustained load as an
  explanation; it does not identify what does explain it.
- **A harness that does not call the same function the product calls will measure
  a different system.** The projection harness passed the pre-change arguments and
  would have reported a clean 0% for a real change.
- **An unmutation-tested gate is not a gate.** The pack comparator has now been
  shown to fail on altered claim text, flipped validity, a dropped conflict record,
  and meaningful reordering, and to pass against itself.

## Where the evidence lives

| Claim | Artifact |
|---|---|
| Latency envelope | `docs/performance/status-latency.json` |
| Latency above 10k | `docs/performance/status-latency-extended.json` |
| Correctness, 202 questions | `docs/performance/m0115-experiments.json` |
| Held-out correctness, 157 questions | `docs/performance/m0130-heldout.json` |
| Authoritative pack baseline and comparison | `docs/performance/baseline-packs.json` |
| Degradation and poisoning | `docs/performance/m0115-degradation.json` |
| Rebuildability | `docs/performance/m011-rebuildability.json` |
| Load serialisation split | `docs/performance/m0124-load-split.json` |
| Detailed experiments | `docs/research/` |
| Public contract | `examples/MEMORY_API.md` |

## Reproducing the numbers

```bash
sh scripts/benchmark/ensure_postgres.sh
DATABASE_URL=postgresql://mpadmin:secret@localhost:5432/mindpalace \
  HF_HUB_OFFLINE=1 venvmp/bin/python -m pytest -q

PYTHONPATH=scripts/benchmark DATABASE_URL=... HF_HUB_OFFLINE=1 \
  venvmp/bin/python scripts/benchmark/m0115_experiments.py

PYTHONPATH=scripts/benchmark DATABASE_URL=... HF_HUB_OFFLINE=1 \
  venvmp/bin/python scripts/benchmark/m0122_instrumented.py \
    --sizes 100,1000,5000,10000 --repeats 3 --out docs/performance/status-latency.json

PYTHONPATH=scripts/benchmark DATABASE_URL=... HF_HUB_OFFLINE=1 \
  venvmp/bin/python scripts/benchmark/m0130_run_heldout.py
```

The latency harness measures each size in its own process by default; that is
deliberate, because a single combined process has produced two contaminated rows
here. Pass `--in-process` only when reproducing that failure.

The database setup is scripted because the container does not survive between
sessions, and a missing database has twice produced artifacts that read like
product behaviour.
