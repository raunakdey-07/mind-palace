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
| M013 | Retrieval research: measured arms and rejected rewrites | `v0.8.0` |
| M014 / M014.1 | Verifiable Memory: portable proofs, receipt contract, CLI and packaging | `v0.8.0` |
| M014.2 | Memory Receipt, the three-property trust model, historical receipts | `v0.8.0` |
| M015 / M015.1 | Receipts on every public surface, cross-surface equivalence, release | `v0.8.0` |

## Current release

`v0.8.0` is the current public release. It packages Verifiable Memory: the
portable Memory Pack reader, the standard-library-only proof and receipt
verifiers, the `mindpalace-proof` CLI, and opt-in Memory Receipts on the REST,
Python SDK and MCP memory-query surfaces. A developer can retrieve
authoritative memory, receive a receipt describing what came back, export the
Memory Pack, and verify the artifact later from a clean environment with no
server, database, embedding model or network.

Verification establishes integrity and recorded provenance. It does not
establish that the original source was factually correct, and it does not
establish authenticity unless a digest pinned out of band is supplied.

Retrieval is unchanged by this release. M013's measured arms stay rejected, and
no tokenizer, threshold or acceptance-policy behaviour was modified.

Upgrade from `v0.7.0` is additive. No migration is required, the Memory Pack
schema stays at version 1, and existing REST, SDK, MCP and feed behaviour is
unchanged. Callers that do not request a receipt see no new field.

## Numbering rules

- Use `M###` or `M###.#` for development milestones and evaluation reports.
- Use semantic versioning (`vMAJOR.MINOR.PATCH`) for public releases.
- Record the public release in each milestone document instead of renaming
  historical milestone identifiers.
- A milestone marked **Unreleased** must not be described as stable product
  functionality.
