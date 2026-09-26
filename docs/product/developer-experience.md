# Developer experience audit

This audit follows the paths a new developer actually uses against the local
PostgreSQL 15 service. The current follow-up keeps those paths intact while
closing concrete error-reporting and recovery gaps.

## Paths that work

| Surface | Command | Result |
|---|---|---|
| Migrations | `python -m alembic -c migrations/alembic.ini upgrade head` | applies `006_snapshot_membership_seal` |
| Readiness | `GET /health/ready` | `200` when the database is ready |
| Corpus create | `POST /api/corpora` | `201` |
| SDK sync | `mp.sync("examples/docs")` | machine-readable summary |
| SDK context | `mp.context("How does authentication work?")` | bounded pack with sources |
| REST search | `GET /api/search?q=...&corpus=...` | corpus-scoped results |
| Memory operations | `current`, `history`, `evidence`, `as-of`, `snapshot`, `replay`, `pack`, `query` | typed memory envelopes |
| Feed | `GET /api/memory/feed` | cursor, page, and `has_more` |
| CLI | `mindpalace memory ...` | canonical JSON or a stable error |
| MCP | `python -m mcp_server` | ordinary memory tools, no feed tool |
| Local eval | `python -m cli.main eval memory --repetitions 1` | no REST server required |
| Reindex | `mindpalace reindex --corpus <name>` | rebuilds L2 from the archive |

The Quick Start still has one product sharp edge. A document without authored
`claims:` is indexed for live retrieval but does not create authoritative
memory. That behavior is documented as a product decision, not hidden behind a
model-generated claim.

## Live dependency failures

Search, context, ask, and ingestion now translate embedder, reranker, and
optional-model import failures into a sanitized `503`. The response does not
include a provider path, traceback, or model error string.

The reranker is lazy. `Reranker()` does not construct a `CrossEncoder`; the
first non-empty `score()` call loads it behind a lock. `RERANKER_MODEL` selects
the model name. An offline deployment must provision both the embedding model
and the reranker model when it enables `rerank=true`.

## Sync diagnostics

A failed document no longer reduces to `failed=1`. The service, REST response,
and SDK `SyncSummary` carry an `errors` list. Each item names the relative path
and a bounded, sanitized reason. The existing counters and aggregate `message`
remain unchanged for machine consumers.

The underlying transaction is still per document. Earlier successful documents
remain committed, and a failed document leaves no manifest or archive claim.

## Corpus deletion

A corpus with durable archive history returns `409` with a stable message. The
delete does not emit a foreign-key traceback. A corpus without archive history
deletes its live documents and manifest rows together.

The rule is conservative. An empty snapshot still references the corpus, so it
also produces `409`. Deciding whether an empty snapshot should permit deletion
is a separate retention and erasure policy question.

## Frozen benchmark verification

`./scripts/benchmark/m00675.sh --verify` is the read-only check. It does not
overwrite `eval/m00675/result.json`. The frozen dataset, policy, model, and
migration boundary through `005` remain the published identity. A later source
change can still produce a source-fingerprint mismatch, which is reported rather
than hidden by regenerating the artifact.

## Remaining developer gaps

- L0 archiving is still opt-in per document or internal ingestion flag.
- There is no authentication or per-corpus authorization.
- Archive export and import do not exist.
- Memory Pack has no external consumer contract.
- The documented setup uses more than one supported Python environment name, so
  benchmark commands may require `PYTHON`.
- A simple `memory.add()` facade is deferred until authored and derived memory
  have an explicit product contract.
