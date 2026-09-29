# Milestone and release map

Milestone identifiers describe engineering and evaluation work. Semantic
version tags describe public repository releases. A milestone is not renamed
when it is included in a release; the mapping below is the authoritative
relationship between the two numbering systems.

| Milestone | Scope | Public release |
|---|---|---|
| M004 | Persistent versioned memory foundation | `v0.2.0` |
| M005 | Public REST, Python SDK, MCP, and CLI memory interfaces | `v0.4.0` |
| M006 | Persistent-memory evaluation infrastructure | `v0.5.0` |
| M006.5 | Natural-language memory query and relevance layer | `v0.5.0` |
| M006.75 | Frozen reproducible memory-query benchmark | `v0.5.0` |
| M007 / M007.1 | Independent-adjudication handoff infrastructure | `v0.5.1` |
| M009 | Durable corpus-scoped operational memory feed | `v0.6.0` |
| M010 | Unified authoritative `context()` product surface | `v0.7.0` |
| M011 | Memory Pack portability and rebuildability proof | `v0.7.0` |
| M011.5 | Retrieval architecture experiments and rejected rewrites | `v0.7.0` |
| M012 / M012.3 | Claim-representation cache, cached-query fix, release gate | `v0.7.0` |

## Current release

`v0.7.0` is the current public release. It packages the unified authoritative
`context()` surface, the portable Memory Pack reader, the claim-representation
cache, and a cached-query fix, while leaving M007 scientific evaluation and
M008 downstream work pending. M006.75 remains the frozen empirical benchmark
and is unchanged by this release. Upgrade from `v0.6.0` applies two additive
migrations, `006_snapshot_membership_seal` and `007_claim_embedding_cache`;
neither rewrites existing rows. The Memory Pack schema stays at version 1.

## Numbering rules

- Use `M###` or `M###.#` for development milestones and evaluation reports.
- Use semantic versioning (`vMAJOR.MINOR.PATCH`) for public releases.
- Record the public release in each milestone document instead of renaming
  historical milestone identifiers.
- A milestone marked **Unreleased** must not be described as stable product
  functionality.
