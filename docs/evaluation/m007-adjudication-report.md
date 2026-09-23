# M007.1 — INDEPENDENT ADJUDICATION REPORT

STATUS: DATA BLOCKED

DATASET:
- questions: 40 development questions
- candidates: 412 structural candidate rows
- blind review package: 118 deterministic candidate items
- independently adjudicated: 0
- unknown: 105 source rows remain unknown; review items have no decisions
- uncertain: 0 independently adjudicated uncertainty labels
- dataset hash: not applicable; no adjudication records exist
- blind review package SHA-256: e6b461dcecbfa5e1bc2e8fe6cde00c503a39c86ee7aa42a988162dfe914f4bed

The candidate pool currently contains 38 authored expected-claim rows, 269
structural-negative rows requiring review, and 105 unknown rows. These counts
are provenance counts, not gold labels.

ADJUDICATION:
- reviewers: 0
- double-reviewed: 0
- agreement: not applicable; no inter-rater agreement may be claimed
- protocol: `eval/m007/adjudication/instructions.md` and `schema.json`

FAILURE COVERAGE:
- wrong subject: not independently reviewed
- wrong time: not independently reviewed
- stale/superseded: not independently reviewed
- conflict: not independently reviewed
- insufficient evidence: not independently reviewed
- ambiguity: 105 structural candidates remain unknown; not adjudicated

LEAKAGE:
- duplicate questions: not measured in an adjudicated split
- duplicate evidence: not measured in an adjudicated split
- cross-partition question leakage: no research partitions have been created
- cross-partition candidate leakage: no research partitions have been created
- claim overlap: not measured in an adjudicated split
- document-version overlap: not measured in an adjudicated split

M006.75 BASELINE:
- retrieval: not evaluated against independent candidate labels
- relevance: not evaluated
- subject: not evaluated
- temporal: not evaluated
- evidence: not evaluated
- authority: not evaluated
- abstention: not evaluated
- false-current: not evaluated
- false-authority: not evaluated

The released M006.75 result remains the authoritative 47/60 result. It was
not recomputed or reinterpreted.

KEY FAILURE EXAMPLES:
1. None recorded; no independent adjudication has been performed.
2. None recorded; structural-negative status is not a gold label.
3. None recorded; no M006.75 prediction or ranking was used.
4. None recorded; no temporal decision was inferred.
5. None recorded; no evidence or conflict decision was inferred.

VALIDATION:
- focused tests: 19 passed
- full tests: 930 passed, 147 skipped
- Black: passed for all M007.1 files changed in this pass
- Flake8: passed for all M007.1 files changed in this pass
- diff check: passed

DECISION:

GATE A — DATA READY: FAIL
GATE B — BASELINE DIAGNOSTIC: FAIL
GATE C — ENGINE JUSTIFICATION: FAIL

NEXT STEP:
CONTINUE M007.1

Do not begin M007.2. Obtain independent reviewer records for a deliberately
stratified subset, then compute agreement, leakage, category coverage, and
M006.75 baseline diagnostics. The structural candidate generator must remain
unadjudicated until that work is complete.

IMPORTANT:
Do not recommend M007.2 merely because it is on the roadmap. A new decision
engine is justified only if independently adjudicated evidence demonstrates a
real, measurable decision-quality problem.
