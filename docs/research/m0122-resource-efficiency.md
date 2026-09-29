# M012.2: resource efficiency and release readiness

## Decision

**REJECT** the vectorised relevance scorer. It was implemented, proved
byte-identical, measured, and then switched back off because it bought nothing.

**NOT READY** to release. The real bottleneck is now located, and it is not the
one this milestone set out to fix.

## 1. Baseline

| | |
|---|---|
| commit | `f9e7e70`, plus this milestone's two commits |
| Python | 3.14.7, numpy 2.5.2, torch 2.13.0+cu130 |
| PostgreSQL | 15.19, pgvector 0.8.6 |
| suite, exact CI path | **1199 passed** |
| `flake8 api cli tests --max-line-length=100` | clean |
| `black --check api cli tests --line-length 100` | clean |
| `check_versioning.py` | PASS |
| 202-question benchmark | **194/202**, failures 3 ranking, 2 abstention, 3 relationship |
| database | restored from scratch by `scripts/benchmark/ensure_postgres.sh`, migrated to head |

## 2. What the previous milestone got wrong, and how

M012.1 reported "relevance gate ≈ 95% of query cost". That was a measurement
artifact. I called `relevance()` directly with no `claim_vectors`, which set
`missing` to every candidate, which made the call **re-embed all 5,001 claim
representations**. The 110,930 ms was the embedding model, not the scoring.

The scorer was never the cost. A correct stage split with a warm cache, at
5,000 claims:

| stage | ms | share |
|---|---:|---:|
| **`claim_embeddings.load_cached`** | **1,140** | **62%** |
| of which `json.loads` of 5,002 vectors | 756 | 41% |
| `select` (relevance + selection) | 342 | 19% |
| database execute | 358 | 20% |
| whole warm query | **1,826** | |

The dominant term is decoding pgvector text literals into Python lists, one row
at a time. The scorer I was asked to optimise accounts for a fraction of 342 ms.

## 3. Experiment A: vectorised relevance

Implemented as a switch, not a replacement, so both arms run on the real path.

**Equivalence: 202/202 canonical Memory Packs byte-identical.** The closest any
score comes to a gate threshold is 2.296e-05, about 1e11 times the 1.6e-16 the
two orders of arithmetic can differ by. No deterministic tie rule was needed,
so none was introduced.

**Performance, real public path, warm cache both arms:**

| claims | scalar p50 | vector p50 | speedup | scalar p95 | vector p95 |
|---:|---:|---:|---:|---:|---:|
| 100 | 27.96 ms | 26.15 ms | 1.07x | 32.62 ms | 29.66 ms |
| 1,000 | 1,222.95 ms | 1,176.66 ms | 1.04x | 1,244.95 ms | 1,473.18 ms |
| 5,002 | 24,483.43 ms | 24,443.06 ms | 1.00x | 28,994.19 ms | 28,158.66 ms |

Rejected. The switch and the equivalence harness are kept because they are
reusable and the proof is worth having, but the default is back to the scalar
scorer.

## 4. Two changes kept, with honest labels

Both remove provably redundant work, and both are byte-identical across all 202
questions, but **neither demonstrated an end-to-end win on this host**:

- `documents[position] - common` was rebuilt for every candidate *and* every
  topic although it depends on neither. Hoisted out of the loop.
- `terms(representation)` ran a regex over every claim on every query. Memoised
  on the immutable text, bounded at 200,000 entries.

I am keeping them because they are strictly less work with no semantic change,
not because they are proven to matter. I could not measure a difference above
this host's noise.

## 5. A measurement I cannot explain

The profiled warm query at 5,000 claims is **1.826 s**. The end-to-end harness
in the same session reported a p50 of **24,483 ms** for the same corpus, same
path, same warm cache. That is a 13x discrepancy I have not resolved.

I am reporting both rather than the flattering one. The ratio in the profile
(62% in `load_cached`, 19% in `select`) is the finding I would act on; the
absolute wall-clock in the harness is not something I currently trust. This
host has shown roughly 2x run-to-run variance all session, and 13x is beyond
that, so one of the two harnesses is doing something different that I have not
isolated. Neither should be cited as a latency result until it is.

## 6. What was rejected, and why

| candidate | result |
|---|---|
| Vectorised relevance scorer | 1.00x to 1.07x end to end. Rejected. |
| Hoisting the common-term difference | no measurable end-to-end effect. Kept as cleanup, not claimed as a win. |
| Memoising claim tokenisation | no measurable end-to-end effect. Kept as cleanup, not claimed as a win. |
| Current-version pointer (M012.1) | 3.6 ms of a 20,894 ms query. Rejected. |
| Set-based archive load (M011) | O(n²) removed but a generic-plan cliff made it 12x worse in production. Rejected. |
| Candidate narrowing (M012) | 170/202 equivalent. Rejected. |

Four rejected optimizations across three milestones, each of which looked
correct from a profile line and was not.

## 7. Reliability and invariants

Unchanged and re-verified: 1199 tests, 194/202, L2 destruction and rebuild
byte-identical, the dependency-free reader still imports nothing, frozen M006.75
evidence untouched, `v0.6.0` untouched, `AGENTS.md` still untracked with its
local copy intact.

## 8. Release readiness

**NOT READY.**

Blocker, stated exactly: a warm query against a 5,000-claim corpus spends the
largest share of its time decoding cached vectors in
`api/services/claim_embeddings.load_cached`, and there is no measured, byte-
identical way to avoid it yet.

The next experiment is narrow and concrete: return the cached vectors as a
single pgvector array rather than 5,002 text literals decoded one at a time,
prove 202/202 pack identity, and measure. It targets a term that is 41% of the
profile rather than one that is invisible in it.

The release threshold also requires before/after numbers I do not yet have for
anything that shipped, so nothing here justifies a tag.

## 9. Competitive position

Unchanged this milestone. No capability was added, removed or altered. The work
was entirely about finding out that the expected optimisation was not the
expensive one, which is worth as much as a shipped optimisation would have been
if it had actually optimised the hot path.
