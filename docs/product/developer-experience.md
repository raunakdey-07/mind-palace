# Developer experience audit

This audit follows the paths a new developer actually uses against the local
PostgreSQL 15 service. The current follow-up keeps those paths intact while
closing concrete error-reporting and recovery gaps.

The [first-use audit](first-use-audit.md) records the separate question of
whether a new developer can reach the product at all. This document covers
whether the paths that exist behave correctly.

## Paths that work

| Surface | Command | Result |
|---|---|---|
| Migrations | `python -m alembic -c migrations/alembic.ini upgrade head` | applies `007_claim_embedding_cache` |
| Readiness | `GET /health/ready` | `200` when the database is ready |
| Check setup | `mindpalace init` | storage, schema and corpus checked; next command printed |
| Remember | `mindpalace remember "<statement>" [--key K]` | authoritative memory, or the same identity again |
| Recall | `mindpalace recall "<question>"` | answer, source and time |
| Explain | `mindpalace explain "<question>"` | source, time, evidence offsets, history, verification |
| History | `mindpalace history "<key>"` | the supersession chain, oldest first |
| Receipt | `mindpalace receipt "<question>" -o receipt.json` | a self-contained receipt |
| Verify | `mindpalace verify receipt.json` | `VERIFIED`, or `REJECTED` with a reason, offline |
| Corpus list | `mindpalace corpora` | every readable and writable corpus |
| Corpus create | `POST /api/corpora` | `201` |
| SDK write | `client.remember("...", key="datastore")` | `MemoryResult` with `unchanged` |
| SDK read | `client.recall(...)` / `client.explain(...)` | the same response the CLI renders |
| SDK sync | `mp.sync("examples/docs")` | machine-readable summary |
| SDK context | `mp.context("How does authentication work?")` | bounded pack with sources |
| REST search | `GET /api/search?q=...&corpus=...` | corpus-scoped results |
| Memory operations | `current`, `history`, `evidence`, `as-of`, `snapshot`, `replay`, `pack`, `query` | typed memory envelopes |
| Feed | `GET /api/memory/feed` | cursor, page, and `has_more` |
| MCP | `mindpalace-mcp` | `memory_recall`, `memory_explain`, and the archive operations |
| Local eval | `python -m cli.main eval memory --repetitions 1` | no REST server required |
| Reindex | `mindpalace reindex --corpus <name>` | rebuilds L2 from the archive |
| Warm runtime | `mindpalace runtime start [--wait]` / `stop` / `status [--json]` | a model already loaded, used automatically by read commands |
| Model-free read | `MIND_PALACE_LEXICAL=1 mindpalace recall "..."` | the same response, ranked by token overlap, with no model import |

Two behaviours worth knowing before writing code against the SDK:

- **One failure, one exception class.** `local`, `runtime` and `remote` all raise
  `MemoryClientError`, carrying the same `code`, `message` and `status_code`. Which
  one you needed used to depend on whether a runtime happened to be running. A genuine
  defect is still raised as itself, so it is not disguised as an API failure.
- **`MIND_PALACE_LEXICAL=1` is opt-in and explicit.** It bypasses the runtime on
  purpose, prints one line on stderr, and does not always select the same keys as
  semantic ranking — see [operations](../operations.md#model-free-lexical-mode).

A document without authored `claims:` is indexed for live retrieval but does not
create authoritative memory. That is still a product decision rather than a
model-generated claim, and `mindpalace remember` is now the intended way to write
memory that is meant to be recalled.

## Live dependency failures

Search, context, ask, and ingestion translate embedder, reranker, and
optional-model import failures into a sanitized `503`. The response does not
include a provider path, traceback, or model error string.

The reranker is lazy. `Reranker()` does not construct a `CrossEncoder`; the
first non-empty `score()` call loads it behind a lock. `RERANKER_MODEL` selects
the model name. An offline deployment must provision both the embedding model
and the reranker model when it enables `rerank=true`.

`remember` is the one writer that does not need a model: it passes
`require_embeddings=False`, so the embedder is never loaded — not attempted and
caught, which would still pay for a first-run download. `import sentence_transformers`
alone costs 10.8 s on battery (5.3 s on mains) on the machine these numbers come
from, so a write that merely
reaches for a cached model is not free either.

The consequence is that a corpus authored through `remember` starts with a cold
L2 read cache, so the read path warms it instead: `memory_query` writes back the
vectors it had to embed. That is a read that writes, which the previous contract
forbade outright, and it is now bounded rather than merely intended — its own
`SAVEPOINT`, skipped on a read-only session, best-effort, and discarded when the
lexical fallback runs. The cache holds no authority, so every one of those
boundaries degrades to latency and nothing else.

Bulk ingestion deliberately keeps `require_embeddings=True`, so accepting a
directory it cannot index stays a visible failure rather than a silent
unsearchable corpus.

## Storage boundary of the write path

`mindpalace remember --file` and the SDK's `file=` read a local file; a trusted
operator at their own machine makes that call. `POST /api/memory/remember`
deliberately does **not** accept a path: the endpoint has no authentication, so a
relative path would let any caller read a file from the server's working
directory and recall its contents. The field is rejected as an unknown key.
Importing documents over HTTP is `POST /api/ingest/file` and `sync_repo`, which
take content explicitly.

## Failure text

Every product command routes failures through one place, which turns a stable
error code into what happened, why, and what to run. `mindpalace init` and any
command that cannot connect use the shared storage text.

```text
Mind Palace needs persistent storage, and it could not reach it.

Why: it could not connect to PostgreSQL at localhost:5432.

What to do:
  docker compose up -d postgresql
```

`--verbose` appends the underlying exception. Nothing is discarded; the order
changes so the useful part comes first.

## Idempotency

`remember` returns `changed=false` and the same `version_id` for an identical
statement. Four concurrent identical writes produce exactly one version and one
claim, enforced by the existing corpus advisory lock and content fingerprint.
`tests/test_m015_golden_path.py` asserts all three cases against real PostgreSQL.

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

- Authentication and per-corpus authorization are a documented contract, not
  implemented code. See [operations](../operations.md#authentication).
- Archive export and import do not exist; a Memory Pack is the export.
- Recall loads the authoritative projection for the whole corpus, so latency grows
  linearly past a few hundred statements. Measured and stated in
  [performance](../performance/five-minute-path.md).
- Signed receipts do not exist. `--trusted-digest` covers authenticity against a
  value you pinned yourself; asymmetric signing is deliberately not built until a
  deployment asks for it.
- Automatic extraction does not exist. Every claim is authored.
- `mindpalace explain` traces at most five source documents and says so when it
  stops; there is no flag to raise the bound yet.
