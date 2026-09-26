# M010: unifying retrieval into the product

## Question

`context()` was documented as the primary product surface, but the bake-off showed it
answered a different question from the authoritative path: 5/7 against 7/7, and the
failures were structural rather than ranking mistakes. Should `context()` become the
authoritative product, and at what cost?

## What changed

`api/services/context_service.py` is now the single path behind `context()` for REST,
the SDK and MCP. It runs in this order:

1. Ask whether the corpus has an archive. `memory.enabled` already answered this, so it
   is reused rather than a new query.
2. If it has one, resolve the question through the `query` operation. That response is
   the pack.
3. If the resolution found nothing relevant, return empty and say so. Retrieval is not
   called, because the archive has made a decision and chunks must not fill the gap.
4. If there is no archive, retrieval is the only source and `status` reports that.
5. Otherwise rank live chunks and attach them as raw material.

`api/services/context_packer.py` grew the authoritative half of the pack and keeps the
retrieval-only half, so both paths return one shape. The route no longer holds an
`Embedder`; the service builds one only when retrieval is actually reached.

The invariant that makes step 5 safe: retrieval results are only ever `chunks`. They
carry attribution, they can be truncated, and they never become a `memories` entry.
Authority is decided before relevance runs.

## Contract

Additive, so nothing existing breaks. `query`, `context`, `sources`, `chunks`,
`token_estimate`, `strategy` and `truncated` are unchanged. New: `status`,
`budget_unit`, `as_of`, `valid_at`, `memories`, `conflicts`, `changes`.

`status` is what a caller branches on: `resolved`, `conflicting`, `no_relevant_memory`,
`empty_corpus`. Absence is a value here, not an empty list.

## Evidence

Three adapters, one corpus, real PostgreSQL 15.4, real migrations, real ingestion, real
cached `all-MiniLM-L6-v2`, twelve question classes.

| | A old `context()` | B authoritative `query` | C unified `context()` |
|---|---:|---:|---:|
| Cases passed | 10/12 | 12/12 | 12/12 |
| Warm mean, median of 4 runs | 15.0 ms | 46.3 ms | 58.2 ms |
| Warm mean, range | 14.4 to 15.6 | 42.2 to 48.2 | 55.6 to 59.0 |
| Cold, fresh process | 4882.7 ms | 4947.8 ms | 4927.6 ms |

Scores were identical on all four runs. Cold is a single observation per adapter,
because a cold-start number cannot be averaged; it is dominated by the 4.9 s model
load, so the spread between adapters is noise.

A's two failures are the two the milestone named. It cannot express supersession, and
it returns four confident chunks for a question the archive cannot answer.

C costs about 12 ms more than B warm, which is the retrieval pass for raw material. At
cold that 12 ms is invisible against a 4.9 s model load, so first use is unchanged.

Without a model, measured by a fresh process asking for a name that cannot load:

| Surface | Result |
|---|---|
| `current`, `history`, `changes` | OK |
| authoritative `query` | OK, 1 memory, 1 conflict, 3 evidence |
| `context()` old | FAIL, raw dependency error |
| `context()` unified | OK, `status=conflicting`, 1 memory, 1 conflict, 0 chunks |

The unified pack costs the model nothing: it reports the conflict and the evidence and
simply returns no chunks.

## Two defects found while measuring

**A budget-truncated pack was indistinguishable from an empty one.** `bounded_pack`
returns a valid but empty response when the character budget cannot hold the resolved
claims, and `select` marks genuine absence with a `NO_RELEVANT_MEMORY` constraint. The
first version of the service read emptiness, so a small budget made the product claim
the archive had nothing to say. Absence is now read from the marker, not the length.

**`previously` was not a historical signal.** `"What did we use previously?"` fell
through to the current intent, so the superseded statement never came back. The
interpreter now matches adverb forms, and `context()` accepts an explicit `intent` so a
caller is not at the mercy of phrasing. That closed the one case where C scored below
B.

## Limitations

- Twelve questions on one corpus of five versions. It measures whether each product can
  express an answer, not ranking quality at scale. `Recall@k` is not reported because
  the corpus is too small for it to mean anything.
- A is scored on substring presence, which flatters it wherever the right string happens
  to appear. Its conflict and provenance passes are string matches, and the artifact
  records that it has no field for either.
- Four runs for latency, which place the three adapters but do not support percentiles.
  The spread within an adapter is under 7 ms.
- C is tested against a live-index corpus for the retrieval-only path and an archived
  corpus for the authoritative path. A corpus that is archived but whose archive the
  model cannot rank is covered by the lexical fallback, not by this table.
- The relevance floor is inherited unchanged. A micro-corpus whose every claim shares
  the subject word will still see that word subtracted as non-discriminating, and the
  question will look irrelevant. That is the documented gate, and re-tuning it is a
  separate decision with its own evidence requirement.

## Decision

ADOPT, for the archived-corpus case. C matches B on every case, keeps the whole
existing context contract, and survives a missing model where A does not. The cost is
about 12 ms warm and nothing measurable cold.

The retrieval-only path is unchanged behaviour for a corpus with no archive, so this is
not a break for anyone using `context()` today. It is a break for anyone parsing
`pack.context` expecting ranked chunk prose, because the text is now rendered from
authority. `status` is additive and absent consumers are unaffected, but the text
itself is the migration. The contract is documented in the README section
"Context: the product surface".
