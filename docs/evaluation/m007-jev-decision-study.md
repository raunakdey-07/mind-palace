# M007.1 Jev Decision Provider Benchmark & Teacher Study

Public release: Unreleased research

## Status

**BLOCKED — live Jev access and replay responses are unavailable in this
environment.**

This milestone adds the experiment boundary and reproducible offline harness.
It does not claim live Jev quality, cost, latency, or calibration results. The
deterministic provider remains the only offline/default behavior.

## Controlled comparison

The benchmark uses the same 412 development candidate rows grouped into the
same 40 question candidate pools:

* **A — baseline:** deterministic provider over the candidate pool
* **B — Jev:** the same pool through `JevDecisionProvider`
* **C — hybrid:** reserved for an explicit, documented disagreement policy
* **D — replay:** the same Jev adapter backed by `ReplayDecisionTransport`

The adapter does not invent a Jev HTTP or SDK schema. A real client or
transport must be injected by an experiment. Missing replay responses fail
explicitly. No live request is made by normal tests or CI.

The current deterministic benchmark is a harness smoke test, not an M006.75
result: its lexical candidate features are derived from development rows and
are not the production query evaluator. The frozen M006.75 baseline remains
unchanged and must be reported separately.

## Provider and replay contract

Each transport request includes:

```json
{
  "schema_version": "jev-decision-v1",
  "operation": "choice | score | probability",
  "question": "...",
  "options": ["opaque candidate ids"],
  "state": {}
}
```

Responses must provide a bounded `confidence` and either `value` or
`decision`. Malformed responses, missing replay entries, transport timeouts,
and transport failures are errors; they are not abstentions or successful
fallbacks.

Replay keys are canonical JSON with sorted keys and compact separators. A
replay artifact can therefore be checked into a future experiment without a
network call or API key. Provider/model version and request schema version are
recorded in traces.

## Decision provenance

Decision provenance is represented by `DecisionTrace` and is separate from
memory provenance and authority resolution:

```json
{
  "question": "Which claim is relevant?",
  "candidate": "migration.database.strategy",
  "decision": "select",
  "confidence": 0.91,
  "provider": "jev",
  "provider_version": "replay-1",
  "request_schema_version": "jev-decision-v1",
  "policy_version": "m00675-baseline-candidate-pool",
  "candidate_features": {"candidate_count": 10},
  "authority_resolution": null,
  "evidence": ["authored evidence quote"]
}
```

The optional timestamp is excluded from default serialized comparisons and is
never used for deterministic equality. No public API or memory schema changed.

## Disagreement and adjudication

`compare_runs` classifies agreement and selection/abstention disagreements.
`build_review_queue` prioritizes abstention disagreements and then other
selection disagreements. This is a candidate adjudication queue, not a gold
label set. Independent review must classify rows as `SELECT`, `REJECT`,
`ABSTAIN`, `AMBIGUOUS`, or `NEEDS_DOMAIN_REVIEW`.

No adjudicated rows exist yet. No Jev output is treated as ground truth, and
baseline non-selection is not converted into a negative label.

## Metrics and safety gate

Once live or replay responses exist, report only measured metrics:
labeled decision accuracy/precision/recall/F1, abstention quality,
calibration where sample size supports it, agreement, useful adjudicated
disagreements, and latency p50/p95. Live mode must additionally record
model/version, request count, errors, network latency, and cost if exposed.

The safety comparison must independently report false-current promotions,
provenance failures, conflict closure, temporal failures, budget failures, and
corpus-isolation failures. A decision provider cannot alter temporal
resolution, authority, evidence, conflicts, deletion, restoration, or packing.

## Reproducibility

Run the offline harness with:

```bash
venvmp/bin/python -m pytest \
  tests/test_memory_decision_jev.py \
  tests/test_memory_decision_benchmark.py
```

The live Jev experiment is intentionally **unexecuted**. A future run must
persist sanitized replay responses, provider/model version, request schema,
policy version, candidate IDs, decisions, confidence, latency, and errors.

## Conclusion

This iteration cannot determine whether Jev is effective, mixed, or
unjustified because no independently reviewed Jev decisions are available.
The correct current conclusion is **D — BLOCKED**, not a fabricated
comparison.
