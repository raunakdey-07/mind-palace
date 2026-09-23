# Why persistent corpus memory?

**Mind Palace is a persistent, portable memory layer for AI applications working
with an evolving corpus.** It is not a model of human memory, a conversation
store, or an agent that decides what is true. Markdown remains the authored
source; PostgreSQL retains versions, assertions, and exact supporting evidence;
SDK, REST, CLI, and MCP expose a shared typed contract to consuming applications.
Portable means model-independent interfaces and attributable context—not a
promised archive export/import or erasure facility.

## Why not just RAG?

Live retrieval answers **“What text is relevant now?”** That remains useful.
But replacing indexed chunks on each update loses the explicit relationship
between a former assertion, its replacement, a contradictory source, and a
source that was deleted. Similarity scores do not encode those relationships.
A model may infer them from prose, but inference is not a durable record.

Corpus memory adds a different set of questions:

| Application needs to know | Explicit memory operation |
|---|---|
| What do the latest observed sources assert? | `current` |
| What did they assert before? | `history`, `as-of` |
| Was this a wording edit, replacement, deletion, or restoration? | `changes` |
| Which exact archived quotes support this claim? | `evidence` |
| Which sources disagree, without silently choosing a winner? | Conflict groups in memory responses |
| Can I replay the same captured state after later syncs? | `snapshot`, `replay` |
| What evidence-backed selection fits this envelope budget? | `pack` |

`/search`, `context()`, and default `/api/query/ask` still use the live index.
`memory.pack()` does not replace semantic retrieval: it projects explicitly
authored claims using lexical AND matching. See the [API contract](MEMORY_API.md).

## A concrete evolving corpus, not invented output

The [A–G Dispatch fixture](evaluation/corpus/) and
[39 hand-authored queries](../eval/memory_benchmarks.yaml) describe a service:

- A starts with Redis Streams, JWT authentication, and a provisioned six-partition
  Kafka migration target. PostgreSQL is the authoritative order store.
- B clarifies ownership prose without changing its claim mapping.
- C replaces streaming/auth assertions with Kafka and OIDC in the same documents.
- D expands Kafka to twelve partitions.
- E adds a stale incident runbook asserting JWT, creating an auth conflict.
- F deletes that source from the live index while retaining history and evidence.
- G restores identical bytes and reintroduces the same semantic conflict pair.

The storage document authors **one claim with two exact evidence references**.
Two agreeing ownership documents are instead two claims, each with its own
reference. Neither agreement nor exact containment independently proves truth.
Hostile-looking strings in the security fixture remain verbatim source data;
archiving them is not executing them or proving an LLM can resist them.

Run the [rolled-back demo](MEMORY.md#rolled-back-a–g-evaluation-demo-m006) to inspect
these transitions and execute assertions, without creating a persistent corpus.

## What the evaluation does—and does not—establish

The recorded cached-model run reported **39/39 tailored lexical cases passing**.
Replacing those lookup terms with the authored natural-language question yielded
**0/24 correct exact current-claim sets for nonempty labels**. That is a material
product limitation, not a footnote: a semantics gate is not question understanding.
Empty-set successes do not demonstrate retrieval of knowledge.

The live hybrid+RRF baseline's **79.17% text coverage** measures literal expected
text recoverability, **not semantic accuracy** or correct current/history framing.
The default fixture embedding mode uses artificial deterministic hash vectors;
it downloads no model and provides no learned retrieval-quality evidence.
`--embeddings cached` loads the real existing model offline and fails if its
weights are missing—there is no fallback to fixture vectors.

Generation A (memory-pack prompt) and B (the actual default `/api/query/ask` route)
have opt-in harnesses. **Both are NOT RUN in the recorded evaluation: no LLM
service was available.** No generated-answer quality, prompt-injection resistance,
M006 completion, or deployment readiness follows from the semantics results.
See the maintainer-authored [measured M006 report](../docs/evaluation/m006.md) for
actual results, environment, and performance; this document does not invent them.

## Conservative guarantees and open work

Given the **same frozen state, validity cutoff, selectors, and budget**, canonical
memory packs are identical. Separate wall-clock `current`/`pack` calls are not
promised byte-identical: default validity time changes, and writers may update
the corpus. Replay fixes both temporal cutoffs and uses saved version references.

History loads are unbounded; a small output budget does not bound archive-read
cost. Memory has no semantic ranking, general contradiction detection, independent
fact verification, pagination, or production load-test guarantee. A synthetic
5,000-version cap is a safety bound, not a measured scale result.

Namespaces scope data but **are not authentication or authorization**. Source
removal appends tombstones, not archive erasure; retention remains unresolved.
Use trusted access and a deliberate deployment policy. These constraints are
part of the product's current contract, not details a downstream AI can repair.
