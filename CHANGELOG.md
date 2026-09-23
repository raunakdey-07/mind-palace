# Changelog

All notable public releases are listed here. Milestone identifiers are
preserved inside each release entry and map to the public semantic version
through [`docs/release-map.md`](docs/release-map.md).

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

[v0.5.0]: https://github.com/raunakdey-07/mind-palace/releases/tag/v0.5.0
[v0.4.1]: https://github.com/raunakdey-07/mind-palace/releases/tag/v0.4.1
[v0.4.0]: https://github.com/raunakdey-07/mind-palace/releases/tag/v0.4.0
