# Retrieval routing and the claim representation cache

## What was measured, and what changed

Two changes, both in the relevance layer, neither touching authority.

| | Before | After |
|---|---:|---:|
| semantic always | 175/202 | 178/202 |
| lexical always | 164/202 | 164/202 |
| lexical then semantic | 191/202 | **194/202** |
| warm p50, 88 claims | 502.94 ms | **43.24 ms** |
| ms per claim, warm | 5.72 | **0.45** |

Corpus hash `9cdecda73cbdc8ae055926320b85402d47dd7eed0cb28c96b9b191e5bed154a7`.
Artifacts: `docs/performance/m0115-experiments.json`,
`docs/performance/m0116-claim-cache.json`.

## Change 1: cached claim representations

Authority resolution embedded the text of every claim in the archive on every
question. Claim representations are derived from `claim.claim`, `claim.key` and
`claim.path`, all immutable once written, so they are cacheable derived state.

Migration `007_claim_embedding_cache` adds `memory_claim_embeddings`, keyed by
`(corpus_id, claim_id)` and carrying the SHA-256 of the exact string that was
embedded, the model name, the dimension and a version. A row is used only when
all three still match the claim being scored, so a changed claim, a swapped
model or a different dimension each invalidate their own rows.

| claims | uncached p50 | warm p50 | speedup | digests identical |
|---:|---:|---:|---:|---|
| 15 | 93.52 ms | 19.99 ms | 4.7x | yes |
| 55 | 346.63 ms | 28.70 ms | 12.1x | yes |
| 74 | 497.55 ms | 34.94 ms | 14.2x | yes |
| 88 | 577.65 ms | 43.24 ms | 13.4x | yes |

The cost curve is now roughly flat in archive size. Extrapolating, a thousand
claims should cost about what eighty-eight do, plus the question itself.

**Digests identical in every case.** That is the safety property, and it is
asserted rather than assumed: clearing the cache and asking again produces the
same canonical bytes.

### The cache is written by ingestion, never by a query

The first implementation filled the cache from the read path. It worked and it
was wrong twice over: a failed write poisoned the caller's transaction, because
PostgreSQL does not let a transaction continue after an error even when the
error is caught, and a read-only connection could not answer a question it had
every right to answer.

The cache is now filled by the ingestion service, which already embeds, and by
`mindpalace reindex` through `backfill_claim_embeddings`. The read path only
reads. A corpus with no cache still answers, just more slowly, and dropping the
table changes latency and nothing else.

## Change 2: subject-preserving query decomposition

Three-part questions were being collapsed to a single topic. The cause was one
line in `plan()`: if *any* sub-part had fewer than two meaningful terms, the
whole question was treated as one. "Who owns the Orders Service, what tier does
it run at, and what does it depend on?" ends with "and what does it depend on",
which has one meaningful term, so all three parts were thrown away.

Dropping the weak part alone fixed the collapse but scored one question worse
overall (190 against 191), because a part like "what tier does it run at" has no
subject of its own and matches any service's tier.

Keeping the unsplit question *as well as* the parts fixed both. The whole form
resolves the subject, the parts add attribute detail, and the selected keys are
a superset of what the unsplit question alone would return.

| | naive decomposition | subject-preserving |
|---|---:|---:|
| overall | 190/202 | 194/202 |
| multi-topic | 18/23 | 22/23 |

This is why the measurement was worth running. The obvious fix, dropping the
weak part, was a regression.

## Routing, still not a planner

The routing distribution is unchanged in shape:

| Class | Count |
|---|---:|
| cheap sufficient | 148 |
| unnecessarily expensive | 16 |
| dangerously cheap | 26 |

148 of 202 questions do not need the model. 26 answered with lexical but needed
semantic, so a lexical-only product is not viable and a router is required.

The adapter remains the one already in the system: score lexically, escalate on
abstention. It is measured, it needs no new component, and it is not a general
planner. 16 questions are answered better without the model than with it, which
is the reason to prefer the cheap path first, and 26 are the reason not to stop
there.

## Degradation is unchanged

With a model that cannot load, 202 of 202 questions are still answered, 163
correct against 177 with the model. The cache does not change this: a corpus
with an unusable cache is exactly a corpus with no cache.

| Phase | answered | correct |
|---|---:|---:|
| full | 202 | 177 |
| no model loadable | 202 | 163 |
| retrieval unavailable | 202 | 163 |
| all L2 destroyed, including the cache | 202 | 177 |

The last row is the important one. Destroying every derived row, now including
`memory_claim_embeddings`, leaves all 202 authoritative packs byte-identical.

## What was rejected

**A read-side cache fill.** Faster to reach for, and it poisons transactions.
Rejected on evidence, not taste.

**Lexical candidate generation before semantic scoring.** The cache already
removes the dominant cost, taking per-query work from O(claims) embeddings to
one. Adding a two-stage candidate funnel would bound the remaining term, but
that term is now 0.45 ms per claim and 43 ms absolute. The funnel would
optimise a cost that is no longer the problem.

**A learned or model-based router.** 148 versus 26 is a wide enough gap that
the deterministic signal carries it. An LLM classifier would add latency, cost,
nondeterminism and a model dependency to solve a routing decision that token
overlap already solves.
