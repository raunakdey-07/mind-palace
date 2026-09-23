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

## Current release

`v0.5.1` is the current public release. It packages the M007.1
independent-adjudication handoff while leaving M007 scientific evaluation and
M008 downstream work pending.

## Numbering rules

- Use `M###` or `M###.#` for development milestones and evaluation reports.
- Use semantic versioning (`vMAJOR.MINOR.PATCH`) for public releases.
- Record the public release in each milestone document instead of renaming
  historical milestone identifiers.
- A milestone marked **Unreleased** must not be described as stable product
  functionality.
