# Changelog

All notable public releases are listed here. Milestone identifiers are
preserved inside each release entry and map to the public semantic version
through [`docs/release-map.md`](docs/release-map.md).

## [v0.6.0] - 2026-09-24

### Added

- Durable PostgreSQL-backed corpus-scoped memory feed with keyset ordering,
  integrity-protected corpus-bound cursors, REST/Python SDK/CLI interfaces, and
  dependency-aware health endpoints.
- Isolated M009 release validation for live interface equivalence, concurrent
  insertion semantics, exact 100/1,000/10,000-version fixtures, p50/p95 timing,
  and PostgreSQL query plans.
- Explicit MCP exclusion decision for the operational feed.

### Guarantees and limitations

The feed provides bounded keyset continuation over `(observed_at, version_id)`
and documents its MVCC/late-commit behavior. It is not exactly-once messaging,
a broker, CDC, or transactional event delivery. The final page has no implicit
continuation cursor; polling clients must restart and deduplicate or maintain a
separate boundary policy. M009 does not change any M006.75/M007/M008 research
inputs, results, or claims.

Validation evidence is recorded in [`docs/m009/RELEASE_READINESS.md`](docs/m009/RELEASE_READINESS.md).

## [v0.5.1] - 2026-09-24

### Added

- M007.1 independent-adjudication handoff infrastructure.
- Blind 118-case review package with authoritative traceability.
- Ambiguity-preserving reviewer context for exact and ambiguous matches.
- Reviewer schema, guide, blank JSONL template, and reviewer-file validation.
- DecisionReceipt infrastructure with canonical fingerprints, explanation, replay,
  and structured drift detection.
- Deterministic synthetic semantic/property suites and executable M007–M008
  research runner.
- Archive reconstruction through the existing ingestion service.

### Validation

The latest full-suite validation snapshot before this release was:

```text
942 passed, 0 failed, 147 skipped, 10 warnings
```

Skipped tests are environment-gated integration tests and are not counted as
passes.

### Scientific status

- M006.75 remains the latest empirical benchmark: **47/60**.
- M007.1 is **ready for independent human adjudication**.
- M007 scientific results are pending two independent reviewer submissions.
- M008 evaluation remains downstream of the M007 scientific gate.

This release packages research/evaluation infrastructure only. It does not claim
M007 accuracy, agreement, calibration, or comparative improvement.

## [v0.5.0] - 2026-09-23

### Included milestones

- **M004:** persistent, versioned, corpus-scoped memory foundation.
- **M005:** public REST, Python SDK, MCP, and CLI memory interfaces.
- **M006:** persistent-memory evaluation infrastructure and lifecycle
  validation.
- **M006.5:** natural-language memory querying and bounded relevance access
  over the persistent memory model.
- **M006.75:** frozen, reproducible evaluation of current, historical,
  temporal, multi-topic, conflict/provenance, and abstention behavior.

### Added

- Immutable document/version history with deletion and restoration lifecycle.
- Authored claims, exact evidence, provenance, validity intervals,
  supersession, conflicts, snapshots, and replay.
- Multiple evidence references per claim.
- Bounded Memory Packs that preserve evidence and conflict closure.
- Public memory operations across REST, Python SDK, MCP, and CLI.
- Natural-language query planning with explicit abstention and temporal
  handling.
- Reproducible PostgreSQL-backed M006.75 benchmark runner and verification
  artifact.
- Frozen held-out corpus, frozen policy, source/dependency/model fingerprints,
  and canonical result validation.

### Evaluation

On the frozen 60-question M006.75 corpus:

| Category | Exact / full |
|---|---:|
| Overall | **47/60** |
| Current | 6/10 |
| Historical | 5/10 |
| Temporal | 10/10 |
| Multi-topic | 9/10 |
| Conflict / provenance | 7/10 |
| Abstention | 10/10 |

Safety results are reported separately: 0 false-positive retrievals, 0
false-current promotions, 0 provenance failures, 0 conflict failures, 0
temporal failures, 0 budget failures, and 0 execution failures.

Canonical result:

```text
881530c7c6e7ba29fb38eb5b27609071463f733c6a8cd173aeff04173a5d8dc8
```

### Limitations

The benchmark is a project-specific authored evaluation, not a claim of
universal memory-system superiority or production readiness. M007/M007.1
decision-provider and Jev research is intentionally unreleased.

## [v0.4.1] - 2026-09-17

- Stabilized the pre-M006 persistent-memory baseline and database URL handling.
- This release is the starting point for the M006 evaluation work.

## [v0.4.0] - 2026-09-17

- Exposed persistent memory through the public developer interfaces.

[v0.6.0]: https://github.com/raunakdey-07/mind-palace/releases/tag/v0.6.0
[v0.5.1]: https://github.com/raunakdey-07/mind-palace/releases/tag/v0.5.1
[v0.5.0]: https://github.com/raunakdey-07/mind-palace/releases/tag/v0.5.0
[v0.4.1]: https://github.com/raunakdey-07/mind-palace/releases/tag/v0.4.1
[v0.4.0]: https://github.com/raunakdey-07/mind-palace/releases/tag/v0.4.0
