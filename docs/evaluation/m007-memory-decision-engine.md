# M007 Memory Decision Engine

Public release: Unreleased research

## Status

**RESEARCH DATASET FIRST**

M007 is an isolated experiment, not a replacement for M006.75. The existing
query path remains authoritative for memory state, temporal resolution,
supersession, conflicts, evidence, and Memory Pack construction.

The current dataset audit found enough structure to build a transparent
development baseline, but not enough independently authored labels to train or
calibrate a trustworthy multi-head model. No model is trained by this phase.

## Intended boundary

```text
query
  -> candidate discovery
  -> memory decision observations
  -> existing authoritative resolver
  -> evidence/conflict projection
  -> bounded Memory Pack
```

The engine may recommend, rank, group, or abstain over bounded candidates. It
must never assert `CURRENT`, `HISTORICAL`, `SUPERSEDED`, `UNCERTAIN`, or
`CONFLICTING` state and must never write memory.

An eventual decision record is an evaluation/routing observation:

```text
query_id
candidate_id
subject_probability
relevance_probability
temporal_probability
evidence_probability
abstention_probability
decision
confidence
policy_version
```

It is not a memory claim or a persisted authority record.

## Proposed task heads

The first implementation should keep these outputs separate:

* candidate relevance: relevant, irrelevant, or uncertain
* subject compatibility: same subject, different subject, or uncertain
* temporal applicability: applicable, not applicable, or uncertain
* evidence sufficiency: sufficient, insufficient, or conflicting
* candidate grouping: same information need or different information need
* abstention: answerable or insufficient evidence

The graph that composes these judgments should be deterministic software. A
model, if justified later, supplies only local probabilities and confidence.
Disagreement with the authoritative resolver must be observable.

## Baseline before ML

The deterministic baseline should use existing features only:

* lexical overlap and existing embedding similarity
* authored-key, path, and document similarity
* query intent, topic, and temporal framing
* validity interval and snapshot compatibility
* evidence availability and exact quote coverage
* conflict/supersession/history indicators
* candidate count, score margin, and pack budget

It should report separate probabilities for relevance, subject compatibility,
and abstention, plus coverage/risk and calibration metrics. It must be
compared against:

1. M006.75 deterministic policy
2. existing semantic retrieval without a decision layer
3. a local model only if it beats the deterministic baseline on development
   data

No threshold may be tuned on the frozen 60-question set.

## Model progression

Only after candidate-level labels are expanded:

1. calibrated logistic regression over engineered features
2. gradient boosting if logistic regression is inadequate
3. a small shared encoder with separate heads only if simpler models fail

The target is CPU-compatible, offline, reproducible inference. A hosted API,
GPU requirement, new database, LLM agent, and generic Jev-compatible API are
out of scope.

## Hard negatives

Hard negatives must be generated from persistent memory structure and then
reviewed or authored, not inferred as truth from a retrieval failure:

* same vocabulary, different authored subject
* same subject, invalid temporal state
* same document, different claim
* relevant but conflicting alternatives
* relevant but insufficient evidence
* semantically nearby unrelated claims
* one satisfied topic of a multi-topic query
* deleted/restored or superseded versions

The current builder supports positive and non-abstention hard-negative
candidate pairs. It deliberately leaves abstention candidates unknown because
turning every distractor into a negative would create labels without an
independent judgment.

## Required evaluation

For every candidate model, report:

* accuracy, precision, recall, F1
* Brier score, ECE, AUROC, and AUPRC where applicable
* selective coverage/risk curves
* Recall@1/3/5 and multi-topic coverage
* subject, temporal, evidence, and abstention metrics
* false-current promotions, provenance failures, conflict closure, and budget
  correctness
* CPU latency, peak memory, and artifact size
* model recommendation versus deterministic-authority disagreements

The frozen M006.75 set is a final comparison only. It is not a training,
negative-generation, calibration, or debugging source.

## Safety and failure modes

The experiment must explicitly exercise unrelated and near-neighbor queries,
wrong subjects, historical/current confusion, temporal cutoffs, conflicts,
deleted/restored documents, multi-topic queries, missing evidence, adversarial
corpus text, empty candidates, candidate explosion, low confidence, and model
versus resolver disagreement.

Corpus text is data, never instructions. The resolver remains responsible for
all authority-sensitive state.

## Current recommendation

The unique opportunity is not generic choice/score/probability output. It is a
local, auditable composition of memory-specific judgments around persistent
claims, temporal versions, evidence, conflicts, and abstention. That
hypothesis is not yet proven because the current corpus lacks sufficient
independent labels for uncertainty, evidence sufficiency, candidate grouping,
and temporal applicability.

Expand and freeze a memory-decision dataset first. Only then build the
deterministic baseline, calibrate it on development data, and test whether a
small local model adds value without weakening M006.75 safety invariants.
