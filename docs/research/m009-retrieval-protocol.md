# M009 Phase 1 — frozen retrieval evaluation protocol

**Status:** FROZEN at commit `dbdc35f3854a9c88703fe3777aeb5b16a282668b`
**Branch:** `milestone/v0.8.0-dev`

This document exists so that a later comparison cannot accidentally be between two
different corpora, models, or settings while being described as an ablation. Every
value below is either read from source or measured; nothing is assumed.

## Why this protocol exists

The project has already produced one clean-looking number that could not be
reproduced: a latency table whose cited artifact held different values, and a
rebuildability digest that changes every run. Both were caught late. The retrieval
matrix in Phase 2 will be compared across commits and across strategies, which is
exactly the situation in which that class of error becomes invisible. So the
inputs are pinned first, and the pinning is checked rather than asserted.

## 1. Corpus identity

| Field | Value | Source |
|---|---|---|
| Corpus builder | `scripts/benchmark/m0115_dataset.py::build_corpus` | source |
| Corpus name in the harness | `mp-baseline` (fixed constant) | `baseline_packs.py:57` |
| Corpus id derivation | `sha256("corpus:" + name)` | `api/services/corpora.py` |
| Dataset hash | `9cdecda73cbdc8ae` | measured, every run |

`baseline_packs.py` deliberately uses a **fixed** corpus name rather than a random
one. That matters: `corpus_id` seeds every content-addressed id in the archive
(document, version, claim, chunk, evidence, conflict), so a random corpus name
randomises the entire pack. An earlier harness version did exactly that and two
runs of unchanged code disagreed on all 202 packs.

## 2. Question-set identity

| Set | Count | Hash | Blind? |
|---|---:|---|---|
| Development (`m0115_dataset.build_questions`) | 202 | `9cdecda73cbdc8ae` (with corpus) | **No** — 8 known failures |
| Held-out v1 (`m0130_heldout.build_questions`) | 157 | `13030d897faabe4be95ab369c152da010bb4475cf916377436781308073fcba0` | **No** — analysed in prior sessions |

Both sets share the same corpus and differ only in questions. That is a
deliberate design choice: it holds the archive fixed so a wording effect is not
confounded with a document effect, and it makes the two scores directly
comparable.

**Held-out v1 is no longer a held-out set.** It was scored once, then analysed to
explain its failures. Any number produced by tuning against it is a development
number. Held-out v2 is required before any generalisation claim.

## 3. Model and embedding configuration

| Field | Value | Source |
|---|---|---|
| Default model | `all-MiniLM-L6-v2` | `api/services/embedder.py:15` |
| Override | `EMBEDDING_MODEL` env var | `embedder.py:38` |
| Dimension | reported by `get_embedding_dimension()` | `embedder.py:53` |
| Cache key | `f"{model_name}:{dimension}"` | `embedder.py:59` |

The cache key includes the model name and dimension, so a model change cannot
silently reuse vectors from another model. Any Phase 2 run must record both.

**Must be recorded per run:** `EMBEDDING_MODEL` as resolved, the dimension, and
whether the claim-vector cache served or fell back to embedding. A run that
silently falls back measures the embedding path, not retrieval.

## 4. Retrieval settings — read from source, not assumed

| Setting | Value | Source |
|---|---|---|
| Strategies compared | semantic always, lexical always, lexical→semantic | `m0115_experiments.py` |
| Lexical scoring | token overlap: `len(q ∩ t) / len(q)` | `memory_query.py:45` |
| Semantic scoring | cosine inner product over claim embeddings | `memory_query.py:45` |
| Fusion (if used) | reciprocal rank fusion, k=60 | `memory_query.py:62` |
| Relevance floor | 0.30 cosine | `memory_query.py:143` docstring |
| Top-score band | 90% of top score | `memory_query.py:143` docstring |
| Fallback policy | trust cheap path, escalate when it abstains | `m0115_experiments.py:235` |

The floor and band are **heuristics, not truth thresholds**, and the code says so.
Phase 2 must not tune them to recover development points (Phase 3).

## 5. Harness determinism — measured, not assumed

`docs/performance/evidence/200-harness-determinism.txt`: two consecutive captures
of all 202 questions at commit `dbdc35f` produced the identical set digest
`5a3b80490af52c85c33a3ed823472a2fd0da250c5c981f01aa35162117bd3cd4`, and the
comparator reported `IDENTICAL to baseline`, exit 0.

The harness normalises exactly one thing: `observed_at` strings are replaced by
their rank within the pack. That preserves ordering, which authority depends on,
while removing the wall-clock value, which does not carry authority. **Nothing is
stripped.** A mutation test confirmed the comparator is not vacuous: altering a
claim's text, flipping a validity interval, dropping a conflict record, and
reordering a conflict group each produced a non-zero exit and named the affected
question.

**Known limitation of the harness:** the mutator calls the same `normalize()`
function as the comparator, so this proves the *comparator* detects change, not
that `normalize` is independently correct. A digest from a second implementation
would be needed to close that gap.

## 6. The comparator

```
python scripts/benchmark/baseline_packs.py capture --out <path>
python scripts/benchmark/baseline_packs.py compare --baseline <b> --current <c>
python scripts/benchmark/baseline_packs.py compare --baseline <b> --current <c> --show <qid>
```

`compare` exits non-zero and names every question whose pack moved. Use `--show`
to get a field-level diff for one question.

**Never write to a tracked path.** Several harness scripts default their `--out`
to a tracked file. Always pass an explicit path under `/tmp`.

## 7. What is frozen, and what a Phase 2 run must record

Frozen by this document: corpus identity, question-set identity, model identity,
the seven strategy configurations, scoring functions, and the comparison method.

Recorded per run, never assumed: commit SHA, `EMBEDDING_MODEL` as resolved,
embedding dimension, cache hit/miss counts, corpus hash, question hash.

A comparison is only valid if both runs report the same value for every recorded
field. If a model or corpus hash differs, it is not an ablation.
