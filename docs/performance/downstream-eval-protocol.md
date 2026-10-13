# Downstream evaluation protocol — FROZEN BEFORE ANY RUN

**Frozen:** 2026-10-11, on `wip/platform-readiness`. No scored run has been made.
This document is the pre-registration: the hypotheses, the arms, the metrics, the
statistics and the decision rule are written down *before* seeing a result, so
that
the result cannot be reinterpreted afterwards.

Nothing here has been run. The only model credential on this machine is
`OPENROUTER_API_KEY` and it has a hard ceiling of `$0.00`
(`{"data":{"total_credits":0,"total_usage":0.06582244}}`); no local inference
server is running; the only weights on disk are two MiniLM **embedders**, which
cannot answer a question. See [cold-reader/NOT-RUN.md](cold-reader/NOT-RUN.md).

**Frozen hash:** `see scripts/freeze_eval_protocol.py`, which writes the digest
into `downstream-eval-protocol.freeze.json` and refuses to overwrite it. Editing
this document after a scored run is therefore visible.

---

## 1. The question

Does a model given Mind Palace context answer **better** than the same model
given the alternatives — across weak and strong models?

This is not the same question as "does Mind Palace retrieve accurately". A
retrieval metric answers whether the right chunk was found. The integrator's
question is whether the model's answer got better. Those can disagree, and when
they do the retrieval metric is the one that lied.

## 2. Arms

Same questions, same model, temperature 0. Three repeats where a model is
nondeterministic; the repeat variance is reported, not averaged away.

| arm | what the model sees | why it is here |
|---|---|---|
| **A** | no memory at all | measures baseline hallucination on private facts |
| **B** | the full corpus, verbatim, truncated to the context window | the honest upper bound. Wins here mean retrieval is not earning its complexity |
| **C** | naive RAG: same embedder as Mind Palace, top-k chunks, plain concatenation | the thing Mind Palace is usually compared against |
| **D** | Mind Palace's answer, rendered by one documented prompt template | the product |

**k for arm C is tuned on dev corpora only** and frozen before scoring. `k` is
selected by sweeping {3, 5, 10, 20} on a dev corpus and taking the best
per-model; the value and the corpus are recorded.

**B is skipped by design on corpora that do not fit.** It is recorded as
`skipped`, never silently omitted — "we did not run B here" and "B did well
here" are different findings and a reader must be able to tell them apart.

## 3. Question set

`heldout-v4-freeze.json`. Five corpora:

1. engineering decisions
2. agent/user preferences that change over time
3. product or policy documents
4. project notes containing conflicts
5. personal facts with corrections

Slice floors: **≥30% historical/supersession, ≥15% conflict, ≥15% absent**, the
remainder current and multi-topic.

**Authoring.** At least two model families other than the one that built the
system, plus human authors if available. Each author sees the README and the
corpus and **never** retrieval code or an earlier question set.

This set does not exist. The spec above is what it must satisfy; the questions
themselves are not written, because no independent author was reachable. Writing
them here would recreate the defect that makes v3 unusable.

## 4. Metrics

Reported **per slice and per model tier. No pooled headline number.** A single
number across slices hides exactly the failures that matter: a system can be
excellent on `current` and actively harmful on `absent`, and the average is fine.

* **correctness** — deterministic match against the expected fact set first
* **stale-fact rate** — answer states a superseded value
* **conflict handling** — does it surface the disagreement rather than picking
* **abstention correctness** — silent when it should be, answering when it can
* **harm rate** — questions the model got right in A, B or C and **wrong in D**.
  This is the most important number in the study and it is reported first.
* unsupported-claim rate
* input/output tokens, latency, cost

### Why harm rate leads

A memory subsystem that changes an answer from right to wrong is worse than one
that adds nothing. Every other metric here can improve while the product does
real damage, because the damage is concentrated in the slice a user checks
least. If arm D's harm rate is materially above A's, nothing else in the table
outweighs it.

## 5. Grading

Deterministic first: exact and normalised match against the expected fact set,
with supersession and absence handled as their own verdicts rather than as
failures.

Anything requiring judgement is LLM-graded by a **different model family** from
the answerer, blind to which arm produced the answer, with a **20% second-judge
audit**. The disagreement rate between judges is reported. If judges disagree by
more than 10% on any slice, that slice's numbers are marked unreliable rather
than averaged.

## 6. Statistics

n per cell, Wilson 95% intervals, paired bootstrap for arm differences
(10,000 resamples, seed fixed and recorded). Multiple comparisons across slices
and tiers are reported as a family; a slice that only clears the threshold before
correction is labelled as not clearing it.

## 7. Decision rule — fixed now

Per slice per tier, one of:

* **helps** — D beats C on correctness with intervals excluding a tie, **and**
  D's harm rate is not above A's, **and** D beats A on abstention correctness.
  All three, or it is not "helps".
* **no effect** — differences whose intervals include a tie.
* **hurts** — D is below C on correctness, or above A on harm rate.

Two things are stated in advance so they cannot be discovered later:

* **B beating D on a slice is reported as a finding against the product**, not
  as a retrieval tuning problem. If stuffing the corpus beats the product, the
  product is not earning its place on that slice.
* **The phrase "improves any model" will not be written** unless D helps on every
  tier *and* every slice. It is expected not to hold, and saying so in advance is
  the only way its absence means something.

## 8. Tuning hygiene

Templates and prompts are tuned on **dev corpora only**. The sealed v4 set is
scored **once per arm per model**. A second scoring run invalidates the result
and is reported as a development number, whatever it says.

## 9. What is deliberately not measured

* Retrieval ranking quality. A good ranker with a bad answer is still a bad
  product, and the ranker is not the thing an integrator is buying.
* Latency under load. Real, worth doing, and not what this protocol asks.
* Cost per correct answer across different providers' pricing.

## 10. Open questions for the gate

1. Which model tiers are reachable, and what happens to the cells that are not?
   A tier that cannot run is reported as unreachable; it is never replaced by a
   nearby tier.
2. If only one tier is reachable, is a single-tier result worth publishing, or
   is it a pilot? A single tier cannot support "across weak and strong models".
3. Budget. The corpus near 100k tokens multiplied by four arms and three repeats
   is a large spend against a key with no credit.