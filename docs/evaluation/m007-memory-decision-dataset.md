# M007 Memory Decision Dataset

Public release: Unreleased research

## Scope and split boundary

This inventory is for the local Memory Decision Engine research direction. It
does not change M006.75 artifacts.

The frozen evaluation split is:

* `eval/memory_questions_heldout.yaml`: 60 questions
* 10 current
* 10 historical
* 10 temporal
* 10 multi-topic
* 10 conflict/provenance
* 10 abstention

The held-out file and `eval/memory_query_policy.json` are immutable. The
dataset builder in `api.services.memory_decision_dataset` reads only
`eval/memory_benchmarks.yaml` and `eval/memory_questions.yaml`.

The development material currently available is:

| Source | Size | Main labels |
|---|---:|---|
| `eval/memory_benchmarks.yaml` | 39 | operation, stage, query type, current/history, evidence, source, conflict, lifecycle, snapshot, replay, budget |
| `eval/memory_questions.yaml` | 40 | current claims, historical claims, conflict groups, intent, stage, optional path/as-of |
| `examples/evaluation/corpus/stage-{a..g}` | 7 snapshots | authored keys, claims, evidence quotes, document versions, deletions/restoration |
| `eval/results/m00675-development-final.json` | 64 rows/policy | retrieval outputs, plans, selected keys, abstention, safety diagnostics |
| M004/M005 tests | many | lifecycle, snapshots, replay, evidence, interfaces, packing, corpus isolation |

The two question files contain 79 development questions in total. The
benchmark and question sets overlap in corpus concepts but use distinct
question identifiers and schemas. The builder uses the 40-question
`memory_questions.yaml` set for candidate-pair construction and uses the
authored corpus at the question's stage.

## Existing memory structure

Each staged document has authored frontmatter claims with:

* stable authored `key`
* claim text and value
* one or more exact evidence quotes
* document path
* byte-distinct document versions across stages
* deletion and restoration events

The M004/M005 implementation additionally exposes immutable evidence IDs,
observed timestamps, validity fields, supersession links, conflict groups,
snapshots, replay, and bounded Memory Packs. These are authoritative
projections, not model labels.

## Existing labels

The current labels directly support:

* current/unopposed claims
* historical claims
* temporal/as-of questions
* explicit conflict groups
* provenance/source/evidence expectations
* lifecycle events, deletion, restoration, snapshots, replay
* expected authored keys for the M006.75 held-out set
* unrelated/abstention cases
* multi-topic claim groups in the held-out set

The M006.75 artifacts also record selected keys, query plans, relevance scores,
abstention decisions, output claims, safety checks, and failure rows. These are
evaluation observations and must not be confused with independent training
labels.

## Memory-native tasks and label availability

| Task | Desired output | Existing direct labels | Gap |
|---|---|---|---|
| Candidate relevance | relevant / irrelevant / uncertain | positive expected claims; some explicit negatives | candidate-level negatives are incomplete and uncertain is absent |
| Subject compatibility | same/different/uncertain | expected authored keys | available for labeled questions, but not independently authored for every candidate |
| Abstention | answerable / insufficient evidence | 10 held-out and 2 development unrelated questions | too few development negatives for calibrated training |
| Temporal applicability | applicable/not applicable/uncertain | stage/as-of and authoritative outcomes | applicability labels are entangled with resolver outcomes |
| Candidate grouping | same/different information need | 10 held-out topic groups | no independent development topic annotations |
| Evidence sufficiency | sufficient/insufficient/conflicting | exact authored evidence and expected evidence | insufficient and conflicting evidence labels are sparse |

The builder creates development candidate pairs from authored structure. A
claim named by an existing expected label is positive. Other authored claims
are hard negatives only for questions with an explicit nonempty expected key
set. Abstention questions remain `unknown` at candidate level; labeling every
claim negative would manufacture supervision.

## Feature sources

Potential deterministic features are already available without a new database:

* query/candidate lexical overlap and normalized term overlap
* embedding similarity from the existing local embedder
* authored-key equality and key-token overlap
* claim/document/path similarity
* candidate count and score margin
* query intent and explicit `as_of`/snapshot selectors
* observed stage and validity interval compatibility
* evidence count and exact quote availability
* conflict-group membership
* supersession/history presence
* deletion/restoration state
* pack budget and candidate-group size

These features can support a transparent calibrated baseline before any neural
model is attempted.

## Leakage risks

1. Never read `memory_questions_heldout.yaml` while building or tuning.
2. Do not use held-out labels, expected keys, or held-out failure rows as
   negative-generation rules.
3. Do not derive labels from the production query response being evaluated.
4. Keep document version identity and stage cutoffs separate from query labels.
5. Do not use current status as a relevance label; status is authoritative
   state, not semantic relevance.
6. Do not let evidence text containing instructions become executable input.
7. Candidate pairs from the same question must remain grouped when splitting,
   otherwise the query wording leaks across train and validation.
8. Calibration thresholds must be selected on development data only and frozen
   before held-out evaluation.

## Proposed future split

The current data is sufficient to define a development baseline and hard
negative generator, but not sufficient for trustworthy model training. A
future authored split should be grouped by query template and information
need:

* development/train: existing 40 additional questions plus newly authored
  candidate-level task labels
* validation: a separate authored set with novel paraphrases and hard negatives
* test: a new immutable memory-decision set, distinct from the M006.75 held-out
  set
* final comparison: the unchanged M006.75 60-question set, used once at the end

Do not randomly split candidate rows from one query across partitions.

## Missing categories

Before training a Memory Decision Engine, the project needs more independently
authored examples for:

* same-vocabulary wrong-subject pairs
* current-versus-historical candidate pairs
* temporal applicability without treating the resolver as a label generator
* evidence present but insufficient
* evidence that is relevant but conflicting
* multi-topic coverage and partial-answer penalties
* explicit low-confidence/uncertain labels
* candidate explosion and abstention under distractors
* disagreement between a model recommendation and the deterministic resolver
* calibrated confidence and selective-risk curves

## Current conclusion

The corpus is strong for evaluating persistent memory semantics and useful for
bootstrapping positive/hard-negative candidate pairs. It is not yet a complete
supervised dataset for all six proposed memory-native tasks. The primary
blocker is independently authored decision labels, especially uncertainty,
evidence sufficiency, candidate grouping, and temporal applicability.

Therefore the correct next step is **RESEARCH DATASET FIRST**, not model
training and not production integration.

## M007.1 dataset-integrity protocol

The generated development pairs are a candidate pool, not a gold training set.
Every row must retain explicit label provenance:

* `authored_expected_claim`: the claim is named by the existing question-level
  expected-claim annotation;
* `structural_negative`: the claim is not named by that annotation, but remains
  unadjudicated until a reviewer confirms irrelevance;
* `unknown`: the candidate cannot be assigned a candidate-level decision from
  the available evidence.

`label_confidence` is metadata about provenance, not a model confidence. The
current builder assigns `1.0` only to authored expected claims and `0.0` to
unadjudicated structural negatives. These values must not be used as training
targets or calibration labels. `adjudication_status` prevents a structural
negative from silently becoming a gold negative.

Before any M007.2 comparison or M007.3 training experiment:

1. independently review all structural negatives;
2. adjudicate temporal applicability, evidence sufficiency, and abstention
   without consulting the production resolver's output;
3. preserve disagreement and uncertainty as first-class outcomes;
4. group all candidates by question when creating any train/validation split;
5. record reviewer identity, adjudication rationale, and agreement statistics;
6. freeze a dataset fingerprint before evaluating the unchanged M006.75
   held-out set.

M007.1 is a failure gate: if the resulting labels do not provide sufficient
independent coverage, the experiment stops at protocol and benchmark design.
