# Project status

The current, verifiable state of Mind Palace and everything still outstanding.
Everything here was measured at the release commit `f52272d` unless a line says
otherwise. Numbers that came from earlier experiments are marked with the commit
that produced them.

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
memory. That is verified, not asserted; see the invariants below.

## Release state

| | |
|---|---|
| package version | `0.9.0` |
| tag | `v0.9.0` |
| previous release | `v0.8.0`, unmodified |
| migration head | `007_claim_embedding_cache`, unchanged |
| tests | **1362 passed**, plus the CI job provisions the embedding model first |
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

**The runtime changed no memory semantics.** `mindpalace recall`, `explain` and
`receipt` went from about 6.2 s to about 0.63 s, and everything they return is
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
  6.17 s, with the model stack never imported. It selects different keys on
  vocabulary-poor questions, by design, and is documented as a trade-off rather than a
  substitute. See [operations](operations.md#model-free-lexical-mode).
- **One failure, one exception class.** The in-process SDK path raised the service's
  own `MemoryError` while the runtime and HTTP paths raised `MemoryClientError`. All
  three now raise `MemoryClientError`, with the same code, message and status. A genuine
  defect is still not disguised as an API failure.
- **The relevance acceptance gate tests overlap against full claim terms.** Shared
  terms still cannot discriminate between candidates, so ranking is unchanged; but
  they are not evidence that a question is off-topic. The frozen memory benchmark
  holds at 1.000 on every metric with zero safety failures, before and after.

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
[operations](operations.md#why-not-a-lexical-fast-path).

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

## Verified invariants

All re-verified at `f52272d`.

| Invariant | Evidence |
|---|---|
| Deleting every L2 row leaves authoritative memory byte-identical | 12/12 packs identical, one digest |
| Rebuilding L2 restores the same digest | 12/12 packs identical, same digest within a run. **Caveat below:** the absolute digest is not reproducible across runs |
| The Memory Pack reader needs no server | clean-venv import pulls in no heavy dependency |
| Retrieval cannot create authority | 202 questions, 0 untrusted claims, 0 injection markers in claims or evidence |
| Answers survive a missing model | 202/202 answered, 163 correct |
| Answers survive a missing retrieval path | 202/202 answered, 163 correct |
| Authoritative output does not depend on the claim cache | 202 packs identical with L2 destroyed including the cache |
| Corpus isolation holds | `tests/test_corpus_isolation.py`, `tests/test_corpus_scope.py` |

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

## Correctness

**Headline: on questions written by someone other than the authors, the system
answers 131 of 157 correctly (83.4%, Wilson 95% CI 76.8%-88.6%) [v0.8.0].** The
202-question benchmark reports 194/202 (96.0%, 92.7%-97.7%), but that set is a
development set with eight known failures, and it must not be quoted as accuracy
on unseen questions.

The canonical authored benchmark, 202 questions, dataset hash
`9cdecda73cbdc8ae`:

| Strategy | Score |
|---|---:|
| semantic always | 178/202 |
| lexical always | 164/202 |
| lexical, then semantic | **194/202** (dev set, not a held-out result) |

Evidence: `docs/performance/m0115-experiments.json` <- key: summary.strategies.C_fallback

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

Deleting a corpus whose archive still references it returns 409 by design, but the
end-to-end operator path for archive retention and erasure is still unresolved, and
tombstones retain their evidence.

Authentication is not built. Corpus namespaces are separation, not authorization,
and an owner is not a tamper-proof boundary.

### 6. The wheel ships research tooling

The distribution includes 32 files under `scripts/benchmark/`. The packaging
configuration has included `scripts*` since `v0.6.0`, so this is pre-existing, but
the release grew that directory substantially. It affects artifact size, not
correctness, and changing it is a packaging decision rather than a documentation one.

## Rejected work, with the measurement that rejected it

Recorded so the same ground is not covered twice.

| Candidate | Result |
|---|---|
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
