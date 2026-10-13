# Operational memory feed

A durable, corpus-scoped read interface over immutable `memory_versions` rows.
It is intended for synchronization and reconciliation clients, not for
message-broker workloads.

If you are looking for the background process that makes repeated commands fast,
see [the local runtime](runtime.md). If you are putting this on a network, see
[authentication and deployment](deployment.md).

## Release contract

### Ordering

Rows are strictly ordered by:

```text
(observed_at ASC, version_id ASC)
```

`version_id` is the deterministic tie-breaker. The feed emits archive versions
with lifecycle events `NEW`, `MODIFIED`, `DELETED`, and `RESTORED`; an unchanged
ingestion does not create a version and therefore does not create a feed item.

### Cursor

A cursor is an opaque, URL-safe token containing an HMAC-SHA256-signed, versioned
boundary. Clients must treat it as opaque. It is not encrypted: the signed
boundary is not confidential.

A valid cursor is:

- integrity-protected and rejected when its signature is changed;
- bound to one corpus and rejected for another corpus;
- independent of process memory and usable across database sessions and service
  restarts when the same signing secret is configured;
- versioned and bound to the `memory_versions` feed, so a cursor for an
  incompatible endpoint/payload is rejected;
- restricted to a bounded length and strict base64/JSON/types.

The first request omits `cursor`. A page with `has_more=true` returns
`next_cursor`; pass it unchanged to the next request. A final page currently has
`has_more=false` and `next_cursor=null`. Therefore a client that reaches the
current tail and wants to poll later must start a new traversal and deduplicate
against its previously observed version IDs, or retain a separately obtained
boundary according to its application policy. M009 does not invent a hidden
process-local tail cursor.

### Pagination

The service uses a parameterized keyset predicate:

```sql
(v.observed_at, v.id) > (:observed_at, :version_id)
```

It requests `page_size + 1` rows, trims the extra row after determining
`has_more`, and never uses `OFFSET`, Python-side history loading, or a
process-local cursor store. Valid page sizes are 1 through 500; the default is
50.

### Consistency under concurrent writes

Each request is a PostgreSQL statement/MVCC read. Successive requests may see
successive committed states; M009 does not hold one snapshot across independent
HTTP requests.

During an active traversal, a committed row whose `(observed_at, version_id)`
is strictly after the issued boundary can be returned in a later page. Equal
timestamps are ordered by `version_id`. A row committed after a cursor was
issued but whose observation key is at or before that cursor is not returned by
that continuation; clients needing a complete late-arrival guarantee must
perform periodic full reconciliation. This is the measured M009 guarantee, not
an exactly-once or broker guarantee.

M009 does **not** guarantee:

- exactly-once delivery or processing;
- Kafka, CDC, or message-broker semantics;
- transactional event delivery;
- a globally consistent snapshot across independently requested pages;
- absence of late commits behind an already-issued keyset boundary.

## Interfaces

All three public interfaces use the same service query and `FeedResponse`
contract. Incidental JSON whitespace is not part of the contract; clients should
compare typed values or the SDK model.

### REST

```text
GET /api/memory/feed?corpus=project&page_size=50&cursor=<opaque>
```

`corpus` is required and uses the existing 1–128 character corpus-name rules.
`page_size` is an integer from 1 through 500. A successful response has this
shape:

```json
{
  "schema_version": 1,
  "corpus": "project",
  "items": [
    {
      "version_id": "opaque-version-id",
      "document_id": "document-id",
      "corpus": "project",
      "path": "docs/decision.md",
      "version_number": 1,
      "status": "NEW",
      "observed_at": "2026-01-01T00:00:00Z",
      "predecessor_id": null
    }
  ],
  "has_more": true,
  "next_cursor": "<opaque>",
  "page_size": 50
}
```

`document_id` is the authored/source document identifier when present and falls
back to the stable archive document identity for legacy rows. The
`memory_document_id` relationship remains internal to the service.

Invalid requests and cursors return `422`; a missing corpus returns `404`;
database, schema, and transport failures return a sanitized `503`. Error bodies
use:

```json
{"detail":{"code":"invalid_cursor","message":"Invalid change-feed cursor"}}
```

They do not include SQL, credentials, connection strings, tracebacks, or
filesystem paths.

### Python SDK

```python
from mindpalace_sdk import MindPalace

client = MindPalace(base_url="http://127.0.0.1:8000")
page = client.memory.feed(corpus="project", page_size=50)
while page.has_more:
    page = client.memory.feed(
        corpus="project", page_size=50, cursor=page.next_cursor
    )
```

Remote SDK errors preserve the server's `code`, `message`, and HTTP status.
Timeouts, transport failures, and invalid successful responses use the SDK's
`timeout`, `transport_error`, and `invalid_response` codes respectively.

### CLI

```bash
mindpalace memory feed --corpus project --page-size 50
mindpalace memory feed --corpus project --page-size 50 --cursor "$CURSOR"
```

The CLI prints the compact canonical `FeedResponse` JSON and exits nonzero for a
remote service error. `--base-url` defaults to `http://127.0.0.1:8000`.

## MCP decision: deliberately excluded (Option B)

M009 does **not** add `memory_feed` to MCP. The existing MCP tools are ordinary
memory operations over the `MemoryRequest`/`MemoryResponse` contract. The feed
has a different cursor, page-size, error, and operational synchronization
contract. Exposing it would create a second MCP feed schema and would broaden
an unauthenticated tool surface without a demonstrated agent use case.

REST, SDK, and CLI are the supported M009 feed interfaces. A future MCP feed
would require a separately versioned contract, authorization review, telemetry,
and parity tests; it is not an unfinished M009 implementation.
