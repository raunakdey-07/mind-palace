# Retrieval lab protocol — FROZEN BEFORE ANY REPORTED RUN

**Frozen:** 2026-10-11, before the ablation was scored. Digest written once by
`scripts/freeze_lab_protocol.py`, which refuses to overwrite without `--force`.

A smoke run over two arms on the **dev** set had already happened at this point.
That is not a violation: dev is the tuning set, tuning is allowed on it, and
nothing about it is evidence of generalisation. What is frozen here is every
choice that would otherwise be available to make *after* seeing an arm's
result.

---

## 1. What is being measured

Retrieval **ranking**, not answers. A question is matched against the corpus and
the ranked candidate list is scored against gold. No language model, no
temperature, no judge. Every number is recomputable from the artifact.

This is not the end-to-end benchmark and does not replace it. It cannot say
whether a returned claim is a *good* answer. It can say which of seven ranking
strategies puts the right key first, which is the question the ablation asks.

## 2. Sets, and what each one may be used for

| set | n | status | may be used for |
|---|---:|---|---|
| **dev** (`m0115_dataset`, 202 questions) | 202 | dev | tuning, ablation, everything in Phase 2 |
| **held-out v1** (`m0130_heldout`, 157) | 157 | **burned** | diagnosis only. Scored many times; its failures are known to this repository's author. Never evidence of generalisation. |
| **git-derived** | — | see below | supersession and history, with gold from git rather than from an author |
| **sealed v4** | — | **DOES NOT EXIST** | — |

**Sealed v4 does not exist.** The decision D2 that would have supplied
independent human authors was left unresolved, and the only model credential on
this machine has a `$0.00` ceiling. Authoring v4 here would recreate exactly
the defect that makes v3 unusable: the author already knows the failures. So
the sealed arm of this study is **NOT ESTABLISHED**, and nothing in the ablation
is presented as a generalisation result.

## 3. Gold

Per question: the authored claim key, the passages containing that claim's text,
or **ABSENT**. Labels come from `scripts/benchmark/m0115_dataset.py`, where a
platform specification is the ground truth and both the documents and the
questions are rendered from it. A question therefore cannot drift from the
corpus, because neither was written independently of the other.

The 27 ABSENT questions are the reason `answerable_vs_absent_auroc` is reported
at all. A ranking metric that ignores them scores a system that answers
everything perfectly on what it was shown.

## 4. Metrics, per slice, never pooled

Slices: `current`, `historical`, `temporal`, `conflict`, `provenance`,
`abstention`, `multi-topic`, `relationship`, `adversarial`.

* `recall@1/3/5` at **claim** level and **passage** level
* `mrr` and `mrr@1/3/5`
* `answerable_vs_absent_auroc` — the score that decides, ranked
* `risk_coverage` — accuracy against coverage, 11 points
* the four-way breakdown at a fixed threshold: `correct`, `wrong`,
  `false_answer`, `abstained_*`

**No pooled headline number is reported.** A single figure across slices hides
precisely the failures worth knowing: a system can be strong on `current` and
actively harmful on `absent`, and the average stays respectable. Per-arm
totals appear in the tables because they are asked for, and they are always
accompanied by the per-slice breakdown.

Slices with fewer than three questions are reported as `too few questions to
report a rate` rather than as a number.

## 5. The threshold

**0.30**, the shipped `RelevancePolicy.minimum`, fixed in `run.py` before any
arm was scored. It is *not* tuned per arm, because choosing each arm's best
threshold on dev and then reporting dev would answer "which arm is best at
operating where it is strongest" rather than "which arm is best".

Every arm's risk-coverage curve is in the artifact, and that curve — not the
0.30 column — is what the report leads with.

## 6. Arms

| arm | definition |
|---|---|
| **A** | the shipped scorer, imported from `api.services.memory_relevance` |
| **B** | BM25 only, over claim representations, same tokenizer as the lexical rung |
| **C** | semantic only, cosine over claim representations |
| **D** | reciprocal rank fusion of B and C, `k=60` |
| **E** | D, then the shipped cross-encoder reranks the top 50 |
| **F** | passage index projected back to claims |
| **G** | C's ranking with a stronger small embedder |

**Arm A imports the shipped code** — `plan`, `terms`, `claim_terms`,
`representation_of` — so it cannot drift from the system it represents. Arms B–F
are new code and are labelled as such everywhere.

**RRF's `k` is 60, not tuned.** Tuning it on dev and reporting dev is circular,
and RRF is not very sensitive to it.

**The rerank depth is 50, not swept.** A sweep chosen on dev and reported on dev
is the same circularity wearing a different hat.

**Arm G is attribution, not a better number.** If G is much worse than C, the
embedder is the limit. If G is about the same, the pipeline is. Both readings
are reportable; neither is flattering by default.

## 7. Authority, in arm F

F indexes passages so that body text can answer a question — 17 of the 72 dev
passages carry no authored claim, and the dogfood's confident-wrong answer was
exactly a body-text answer.

**F never promotes an unbacked passage to a claim.** A passage with no claim may
be retrieved and reported, flagged `unbacked_passage=True`; it cannot become a
key with a score. The count of unbacked top-1 answers is reported next to every
F number. An arm that improves recall by inventing authority has improved
nothing.

The authoritative Memory Pack digest is a frozen artifact and is not regenerated
by this lab; the L2-rebuild check is a separate verification (Phase 2).

## 8. Proposed targets, frozen before any sealed scoring

Starting point, negotiable at the gate:

* **confident-wrong ≤ 2% of answered questions**
* **coverage ≥ 70% of answerable questions**

**If the data says these are unreachable with a small embedder, the ceiling is
reported and the targets are not moved.** Moving a target after seeing the
curve is the one thing that would make the rest of this protocol worthless.

## 9. Statistics

`n` per cell is reported. Intervals are Wilson 95%, chosen over the normal
approximation because the confident-wrong cells here are single-digit counts,
where the normal interval returns a lower bound below zero and reads as more
certain than it is.

Paired significance testing across arms is **not** reported: with 202 dev
questions spread over nine slices, most cells are too small to support it, and
a p-value computed on a slice of twelve questions would be decoration. The
per-slice `n` is given so a reader can judge that for themselves.

## 10. What this lab does not measure

* Whether a returned claim is a good *answer* (end-to-end benchmark territory).
* Latency, cost, or behaviour at 10⁵ claims. The dev corpus has 88.
* Multi-corpus scoping, authorization, deletion, or rebuild integrity.
