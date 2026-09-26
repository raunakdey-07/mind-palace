# Memory bake-off: two query products in one repository

## Question

Mind Palace exposes two ways to ask a corpus a question. Do they answer the same
question, and what happens to each when the embedding model cannot load?

- `context()` (SDK) and `GET /context` — documented in `api/routers/context.py` as
  "the primary product surface". Embeds the question, ranks live `chunks`, concatenates
  text.
- authoritative `query` (SDK `MemoryClient.query`, REST memory router, MCP) — resolves
  intent and temporal state against the archive, then ranks authored claims under a
  relevance gate and abstention policy, and returns a bounded Memory Pack.

## Methodology

`scripts/benchmark/memory_bakeoff.py`. One corpus, one question set, real PostgreSQL
15.4, real migrations, real ingestion, real cached `all-MiniLM-L6-v2`. Both adapters are
called exactly as the product calls them; nothing is stubbed.

The corpus is the "AI coding agent" workload from the brief: 4 documents, 5 versions,
6 authored claims, one supersession (`architecture.database`: MySQL to PostgreSQL) and
one unreconciled conflict (SQLite, authored valid from 2024-01-01, still open).

Seven questions, one per class: current-state, historical, change, conflict,
provenance, why, abstention. Scoring differs by class because the two products return
different shapes. The authoritative path is scored on its own fields. `context()` is
scored on whether the answer text is present, and its missing fields are recorded as
`false` rather than scored, because a pack of ranked text has nowhere to put them.

Reproduce:

```bash
DATABASE_URL=postgresql://USER:PASS@localhost:5432/mindpalace \
  venvmp/bin/python scripts/benchmark/memory_bakeoff.py --postgres-version 15.4
```

Raw output: `docs/performance/memory-bakeoff.json`.

## Evidence

With the embedding model available:

| Case | Authoritative | `context()` |
|---|---|---|
| current-state | pass, key `architecture.database` | pass |
| historical | pass, 1 historical | pass |
| change | pass, 3 change records | **fail** |
| conflict | pass, 1 structured conflict | pass |
| provenance | pass, 3 evidence rows with offsets | pass |
| why | pass | pass |
| abstention | pass, 0 memories returned | **fail** |
| | **7/7** | **5/7** |

Mean latency: authoritative 43.71 ms, `context()` 19.75 ms, warm, same machine, one
iteration per case. The authoritative path is about twice as slow because it resolves
the complete scoped state before filtering, which is the work the other path skips.

Two of the `context()` passes are text coincidence, not capability. Its pack contained
the word "sqlite" and the phrase "primary database is postgresql" because those strings
are in the retrieved chunk text. It has no `conflicts` field and no `evidence` field, so
it cannot represent that two sources disagree, and it cannot return a quote with a byte
offset. The artifact records `has_conflict_field: false`, `has_evidence_field: false`,
`has_validity_field: false` for every case.

The two real `context()` failures are not ranking misses:

- `change`: the question is about supersession. A ranked chunk list has no change
  records to return, so the answer cannot exist in that shape.
- `abstention`: RRF ranks the whole corpus, so a question with no answer returns the
  4 highest-scoring chunks anyway. The system returns something confidently related to
  nothing.

The as-of control placed a cutoff between the two `docs/storage.md` versions. The
authoritative path returned MySQL and not PostgreSQL, which is the correct historical
state for that instant.

## Degradation

`scripts/benchmark/degradation_probe.py` runs in two phases against a committed schema,
because the embedder keeps one resident model per process and renaming the environment
variable does not evict it. Phase one ingests with the working model; phase two is a
fresh process asking for a model name that cannot load.

Before the change, 3 of 5 public surfaces still answered:

| Surface | Before |
|---|---|
| `current` | OK, 3 current, 1 conflict, 5 evidence |
| `history` | OK, 3 current, 1 historical, 5 change, 1 conflict, 6 evidence |
| `changes` | OK, 5 change, 1 conflict, 6 evidence |
| authoritative `query` | **FAIL** `MemoryError: Memory embedding model unavailable` |
| `context()` | **FAIL** raw dependency traceback |

The archive held the answer the whole time. The only thing the model was needed for was
choosing which authored keys were relevant to the question.

## Change

`relevance()` gained a `lexical` mode that scores by normalized token overlap instead of
cosine. Both scores lie in [0, 1], so the existing `minimum`/`strong`/`relative` gates
are unchanged. `query()` now catches the model failure and retries lexically instead of
raising.

The fallback is safe because relevance only ever chooses *which authored keys to return*.
Claim status, validity windows, conflicts, supersession and evidence are decided by the
persistence projection before relevance runs. A model failure cannot change authority;
it can only change which keys are considered relevant. `memory_relevance.relevance`
now reports `"scorer": "lexical" | "embedding"` and zero embedding calls in lexical mode.

## Result

After the change, 4 of 5 surfaces still answer without a model:

| Surface | After |
|---|---|
| authoritative `query` | OK, 1 current, 1 conflict, 3 evidence |
| `context()` | **FAIL** unchanged |

The lexical fallback narrowed three current claims and one unrelated claim down to the
one relevant key, and still returned the conflict and its evidence. It abstained
correctly on the payroll question when the model was present, and the same gate still
applies without it.

Regression: with the model present the authoritative path still scores 7/7. Warm mean
across three runs was 35.4, 36.5 and 39.0 ms, matching the pre-change baseline of
38.8 ms. The 43.71 ms in the artifact is a single run including a cold first case.

Full suite: 1155 passed, 0 skipped, 0 failed, against 1147 before. Eight tests were
added. Three tests that pinned the old fail-hard contract were replaced, and the
sanitization guarantee they protected is kept by
`test_query_sanitizes_failures_when_lexical_also_fails`.

## Decision

Keep the lexical rung. The cost is one keyword argument and a retry, the ranking is
measurably worse than embedding, and the model-free guarantee is now real for the
authoritative query path instead of aspirational.

Do not, on this evidence, change what `context()` returns. It is faster, it is
documented as the product surface, and two of its failures are shape problems rather
than ranking problems. Replacing it with the authoritative pack is a breaking contract
change that deserves its own scoped pass with its own migration story. It is recorded
as the next objective, not done here.

## Limitations

- 7 questions on 1 corpus of 5 versions. This is a correctness comparison with a fixed
  answer, not a retrieval benchmark. It cannot estimate recall or ranking quality at
  scale, and it does not rank the two products on speed.
- `context()` is scored on substring presence in returned text, which flatters it on
  cases where the right string happens to appear. That is why its two apparent passes on
  conflict and provenance are reported as coincidence rather than credit.
- One iteration per case for latency. Enough to show the two paths differ by roughly
  2x, not enough to publish percentiles.
- The lexical gate's thresholds were inherited from the cosine gate, not re-tuned for
  overlap. On this corpus they separate correct from incorrect, but a corpus with short
  keys and long questions may need different floors.
- `context()` without a model is still an open failure. This change does not touch it.
