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

## Current release

`v0.6.0` is the current public release. It packages the M009 durable
operational memory feed while leaving M007 scientific evaluation and M008
downstream work pending. M006.75 remains the frozen empirical benchmark. M009
release evidence is under [`docs/m009/RELEASE_READINESS.md`](m009/RELEASE_READINESS.md);
the release does not alter M006.75, M007, or M008 research artifacts.

## Numbering rules

- Use `M###` or `M###.#` for development milestones and evaluation reports.
- Use semantic versioning (`vMAJOR.MINOR.PATCH`) for public releases.
- Record the public release in each milestone document instead of renaming
  historical milestone identifiers.
- A milestone marked **Unreleased** must not be described as stable product
  functionality.
