# Changelog

All notable public releases are listed here. Milestone identifiers are
preserved inside each release entry and map to the public semantic version
through [`docs/release-map.md`](docs/release-map.md).

## [Unreleased]

### Added

- **Verifiable Memory: portable proofs of recorded memory state, provenance,
  evidence, temporal validity and supersession.** A proof is a small canonical
  artifact naming the authoritative claim, the document version that carried it,
  its evidence, its temporal position and its supersession lineage, together with
  an `authoritative_digest` over the Memory Pack and a `proof_digest` over the
  proof itself. The proof reuses `MemoryPack.canonical_json` exactly, so there is
  one canonicalisation contract, not two.
- `memory_proof.py`, a standard-library-only builder and verifier. It is shipped
  in the distribution alongside `memory_pack.py`, so a consumer can verify a
  recorded memory with **no server, no database, no embedding model, no network
  and no API credentials**.
- `mindpalace-proof`, a dependency-free CLI with three commands:
  `verify` checks a proof against its authoritative artifact, `prove` mints one
  from an existing Memory Pack, and `explain` shows the recorded provenance,
  evidence, temporal state and supersession lineage for a proven memory.
  `verify` also accepts `--json` for machine-readable verdicts. Exit codes are
  stable: `0` verified, `1` rejected, `2` malformed input.

### Trust boundary

Verification establishes **integrity and recorded provenance** — that the
artifact still represents the state the proof describes, and that no covered
record has been altered since. It does **not** establish that the original source
was factually correct, and it is not a third-party signature. `explain` states
this in its own output.

### Verified

Verification checks the proof digest, the artifact digest, claim identity and
content, evidence ownership (each record must belong to the named claim *and*
version, with unaltered text), document provenance, temporal validity, observation
state at `as_of`, and supersession consistency in both directions, so neither
hiding a supersession nor inventing one passes. Tampering with the claim text,
evidence text, a timestamp, a document identity or the proof itself is rejected
with the failed invariant named. Verified from a built wheel installed into a
fresh virtual environment, run outside the source tree.

## [v0.7.0] - 2026-09-28

### Added

- Unified authoritative `context()` surface across REST, the Python SDK and MCP.
  The archive is resolved first and ranked live chunks are attached as raw
  material, so a conflicted key is reported as a conflict rather than as the
  latest writer's text. The response keeps its existing fields and adds
  `status`, `memories`, `conflicts` and `changes`; existing fields are unchanged.
- `memory_pack.py`, a standard-library-only reader for the Memory Pack. It is
  shipped in the distribution so a consumer can read a pack with no database,
  ORM, embedding model or server present.
- Claim-representation cache (migration `007_claim_embedding_cache`) and a
  `mindpalace reindex` command that rebuilds the derived layer from the archive.
- `as_of` and explicit `intent` selectors on `GET /api/context`, and a lexical
  relevance fallback so authoritative query answers when the embedding model
  cannot load.
- Dependency-free Memory Pack reading, verified from a clean install.
- **Corpus scoping for live retrieval.** `/api/search`, `/api/query` and
  `/api/context` now resolve an explicit corpus before retrieving. With a single
  corpus the parameter may be omitted; with several, omitting it returns **422**
  and naming an unknown corpus returns **404**, rather than silently searching
  across namespaces. Callers that relied on the previous cross-namespace default
  must pass `corpus`.
- **Sync reports per-file failures.** A sync response now carries a bounded list
  of `errors` (at most 50, each a path and a non-sensitive reason) alongside the
  existing aggregate counters, exposed through the SDK and the CLI.
- Semantic loading stays lazy: neither the embedder nor the reranker loads a
  model until first use, and the reranker is a thread-safe singleton.
- The OpenAPI document now reports the installed package version, so it cannot
  disagree with the distribution.

### Fixed

- **Cached-vector queries got 22x slower on a long-lived connection.** After
  asyncpg prepared the statement, two large SQL array parameters dominated the
  load: roughly 380 ms for the first five executions and about 8,400 ms after.
  The load now selects by the indexed corpus, model and dimension prefix and
  filters in Python, which is also a stricter freshness check. Measured on the
  public query path with a warm cache: 10,168 ms to 1,745 ms at 5,000 claims,
  1,359 ms to 1,010 ms at 1,000, and no regression at 100.
- `bounded_pack` now preserves the `NO_RELEVANT_MEMORY` marker, so a bounded
  answer to an unanswerable question is distinguishable from an empty envelope.
- The archive load no longer ships chunk text to the public projection, which
  reads only a chunk's id and heading.
- **Deleting a corpus whose durable archive still references it is refused** with
  **409** and a named error, instead of failing on a foreign key or removing
  manifest rows before documents.
- **A missing semantic model is reported as 503**, not 500, on ingest, search,
  query and context, and the dependency detail is not echoed to the caller.
- **Feed cursors are validated and canonicalised** against the corpus name before
  use, so a malformed or foreign cursor is rejected rather than compared.
- `memory_rehydrate` preserves the document id the archive recorded, so a rebuild
  cannot change `Source.document_id` in a replay.
- The API description reads "The Durable AI Memory Substrate" and is pinned by a
  test, so it cannot drift back to the previous RAG framing.
- `memory_pack` is declared in the package, so `pip install` makes it importable.
- Runtime dependencies are declared in `pyproject.toml` rather than left empty.

### Changed

- Claim representations are tokenised once and memoised on the immutable claim
  text, and the common-term difference is computed once instead of per candidate
  and per topic. Neither showed a measurable end-to-end change on the measuring
  host and neither is presented as a performance result.
- The public projection no longer receives full chunk text.
- Process startup no longer registers a no-op ASGI lifespan handler. Startup
  remains independent of PostgreSQL connectivity, which was the handler's only
  stated purpose.

### Compatibility

Backward compatible for the Memory Pack schema, which stays at version 1, and for
every public response field. The one behaviour a caller can notice is corpus
scoping on live retrieval: where several corpora exist, `corpus` is now required
and an omitted value returns 422 rather than searching all namespaces.

### Upgrade

- Two additive migrations apply: `006_snapshot_membership_seal` and
  `007_claim_embedding_cache`. Neither rewrites existing rows. The Memory Pack
  schema stays at version 1 and no public response contract changed, so this is a
  minor release.

### Known limitations

- Measured warm p50 for the public query path: 47 ms at 100 claims, 1.0 s at
  1,000, 1.7 s at 5,000 and 4.4 s at 10,000, on one host with roughly 2x
  run-to-run variance. 25,000 claims and above were not measured.
- Above about 5,000 claims, projection dominates: it builds a model per archived
  claim and keeps roughly one. Reducing that requires the dependency closure to
  be solved first, because narrowing the archive before projection was measured to
  change 32 of 202 benchmark answers.
- The archive load is not a clean linear function of archive size between 1,000
  and 5,000 versions, and its sort spills to disk on a corpus of that size. The
  cause is not identified. JIT compilation and run-to-run variance are ruled out.
- Conflict detection is keyed, so a contradicting claim authored under a different
  key is not detected and is not labelled as drift.
- Relationship questions score 31 of 34. The failures are two ranking
  weaknesses, one of which is a vocabulary gap, and one missing authored
  representation. No multi-hop relationship reasoning is claimed.
- M006.75 frozen evidence is unchanged. No prior benchmark number was rewritten.

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
