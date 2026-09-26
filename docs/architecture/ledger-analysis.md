# Ledger layering analysis

Scope: an audit of the L0/L1/L2 split in the Mind Palace memory ledger, run at
commit `a882dae`. The question is whether Mind Palace already separates raw
source (L0), authoritative claims and evidence (L1), and derived artifacts (L2),
and what the smallest missing piece is that would make the "durable substrate"
claim testable rather than asserted.

Nothing in `api/`, `tests/`, `docs/m009/`, or git history was modified. The only
file this audit added is this one. Probes ran in throwaway schemas inside a
rolled-back transaction, following the pattern already used by
`tests/test_memory_ingestion.py` and `tests/test_memory_core.py`.

## What the three layers are today

| Layer | Tables | Append-only | Written by |
|---|---|---|---|
| L0 raw source | `memory_versions.content` (full file bytes, YAML frontmatter included) | Yes | `memory.record_version` |
| L0 identity and lineage | `memory_documents`, `memory_versions` | Yes | `memory.record_version`, `memory.record_deletion` |
| L1 claims and evidence | `memory_claims`, `memory_evidence`, `memory_chunks` | Yes | `memory.record_version` |
| L1 temporal references | `memory_snapshots`, `memory_snapshot_versions`, `memory_snapshot_seals` | Yes | `memory.snapshot` |
| L2 derived | `documents`, `chunks` (text plus embeddings), `ingestion_manifest`, `idx_chunks_embedding` | No | `api/services/ingestion.py` |

L0 and L1 are the same PostgreSQL cluster as L2, so "separate layers" here means
separate tables with separate write and mutation rules, not separate systems.
`migrations/versions/004_memory.py` installs a statement-level trigger on the
seven archive tables that raises on `UPDATE`, `DELETE` and `TRUNCATE`.
`migrations/versions/006_snapshot_membership_seal.py` extends the freeze to
snapshot membership.

In short, all three layers exist, L1 is the strongest part of the system, and
L2 is genuinely disposable. The gap is not a missing layer. It is that no code
path rebuilds L2 from L0, and L0 itself is only written for some corpora.

## Measured invariants

Probe setup: isolated schema, all six migrations applied, three Markdown
documents with authored claims, one modification, one delete, one snapshot, one
restore. Six archived versions (five with content, one `DELETED` tombstone), four
claims, four evidence rows, three live documents, four live chunks.

### Current-state reconstruction holds without L2

Every public memory operation was run with full L2, then all L2 rows were deleted
and `idx_chunks_embedding` was dropped, then all eight operations were run again
and the canonical JSON compared byte for byte.

| Operation | Canonical JSON length | Identical with empty L2 |
|---|---|---|
| `current` | 4414 | Yes |
| `changes` | 9029 | Yes |
| `evidence` | 1758 | Yes |
| `history` | 9952 | Yes |
| `as-of` | 4439 | Yes |
| `query` | 3340 | Yes |
| `pack` | 7831 | Yes |
| `replay` | 10217 | Yes |

The `query` operation is worth calling out. It routes through
`memory_query.select`, which builds its own vectors from claim text with
`Embedder()` and never reads live chunk embeddings. The module docstring says so
explicitly. So this is not a lexical-only path.

One control matters here. The first attempt reported differences in six of eight
operations. That was the probe, not the product: `memory_public.execute_in_session`
defaults `valid_at` to `datetime.now(timezone.utc)`, so two captures seconds apart
embed different clocks in `State.valid_at`. Pinning `valid_at` to the snapshot
cutoff isolates the L2 dependency and the result is 8 of 8 identical.

The reason is structural. `memory.py` and `memory_public.py` reference only
`memory_*` tables. `documents`, `chunks` and `ingestion_manifest` appear in the
memory read path nowhere, confirmed by search and by the byte comparison above.

### Provenance completeness holds and is enforced by the database

- Claims: 4. Evidence rows: 4. Claims with no evidence row: 0.
- Evidence rows whose quote does not match the archived chunk at the recorded
  offsets. Recomputed in SQL, the count is 0.
- Non-tombstone versions with no archived chunks: 0.
- A direct `INSERT INTO memory_evidence` with a quote that appears nowhere in the
  target chunk was rejected with SQLSTATE `23514`. The check is a trigger, so it
  holds for writers that bypass the service.
- `memory_public.project` raises `503 memory_unavailable` rather than returning a
  claim with no evidence.

### L2 is disposable, and L0/L1 resist mutation

| Statement | Result |
|---|---|
| `DELETE FROM memory_versions` | Blocked, `23514` |
| `UPDATE memory_claims` | Blocked, `23514` |
| `TRUNCATE memory_chunks` | Blocked, `0A000` |
| `DELETE FROM corpora` while archive rows exist | Blocked, `23503` |
| `DELETE FROM chunks` | Allowed |
| `DELETE FROM documents` | Allowed |
| `DROP INDEX idx_chunks_embedding` | Allowed |

This is the asymmetry the thesis depends on, and it is real.

## Gaps found

### 1. L0 is conditional, and the gap is silent

`ingestion.py` decides per document:

```python
use_memory = self.memory_enabled or "claims" in metadata or await memory.enabled(db, corpus_id)
```

`IngestionService()` defaults to `memory_enabled=False`, and every production
entry point constructs it that way: `api/routers/ingest.py:14`,
`api/routers/corpora.py:90`, `mindpalace_sdk.py:356`. Measured on a corpus with
memory off:

- A document with no `claims:` in frontmatter produced 0 archived versions. Its
  body survives only as live chunk text. Original byte order and the raw
  frontmatter are gone.
- A second document in the same corpus that did carry `claims:` was archived,
  because the frontmatter forced the memory path for that document only. The
  corpus ended up with one archived version out of two live documents.
- Turning memory on later and re-ingesting produced a second version. Nothing
  records the earlier ingestions. The archive simply starts at the moment memory
  was switched on.

The live `documents` table cannot substitute for L0. Its columns are
`content_hash, corpus_id, created_at, date, document_type, git_repo, id,
indexed_at, ingested_at, last_indexed, path, status, summary, tags, title,
updated_at`. There is no body column. Chunk text is a derived segmentation of
the body, and frontmatter is reduced to the handful of fields above.

So for any corpus ingested with the default flag, there is no L0 at all, and the
"disposable L2" claim cannot be exercised. This is the first thing that weakens
the thesis, and it is a configuration outcome, not an error.

### 2. Losing L2 is silent

`RetrievalService.search` filters on `c.embedding IS NOT NULL`. With `chunks`
empty the query returns zero rows and no error. A wiped or never-rebuilt index is
indistinguishable from an empty corpus at the API surface. The probe confirmed
this. No exception, no warning, empty result set.

### 3. No code path rebuilds L2 from L0

Searching the repository for `reindex`, `rebuild`, `rehydrate` and `reconstruct`
returns no implementation. `chunks` rows are only ever written by the ingestion
service, through `sync_repo` and `ingest_file`, and both read from a filesystem
directory. The Makefile has no target for it, and neither does the CLI.
`cli/main.py` offers
`ingest`, `ingest_repo`, `search`, `ask`, `summarize`, `interview`, `related`,
`timeline`, `doctor` and `serve`, and `cli/memory.py` has the ten memory
operations. Nothing rebuilds the live index from the archive.

This matters because the archive holds everything needed. Re-deriving the probe
corpus from `memory_versions.content` alone, using the existing
`parse_markdown` → `extract_sections_with_paths` → `chunk_with_heading_paths`
chain:

- 4 re-derived chunks matched the 4 archived `memory_chunks` rows exactly on
  path, order index, heading path and text.
- The same 4 re-derived chunks matched the 4 live `chunks` rows captured before
  deletion, exactly.
- The document projection matched on title, document type, tags, date, summary
  and `content_hash`.

So the derivation is sound and deterministic. It simply is not wired up.

### 4. The default-corpus document id is frozen, not derived

For a non-default corpus, `repository.upsert_document` computes
`content_hash(f"{corpus_id}:{path}")`, which is stable. For `DEFAULT_CORPUS_ID` it
computes `sha256(path + ":" + body)`, and `ON CONFLICT DO UPDATE` keeps the
original row id. Measured, the stored id `a463591adeb7…` stayed constant across a
body change, while a from-scratch recompute against the new body gives
`adba7e1a9222…`. The archive records the true id in `memory_versions.document_id`.

Any rebuild that recomputes ids rather than reading them from the archive would
silently change `Source.document_id` and `Evidence.document_id` in later
replays. That is an evidence-identity regression, which is the class of change
this project treats as a hard boundary.

### 5. Embedding provenance is not archived

`chunks` carries `embedding_model`, `embedding_dimension` and
`embedding_version`, and `memory_chunks` carries none of them. Ingestion passes
the literal `"1.0"` as the version at `ingestion.py:135` and
`ingestion.py:206`. After a model change, a rebuilt index would hold vectors from
two models with nothing in the rows to tell them apart. This weakness already
exists; a rebuild is what makes it visible.

### 6. `memory_chunks` is narrower than `chunks`

`memory_chunks` stores `corpus_id, heading_path, id, order_index, text,
version_id`. `chunks` also stores `token_count`, `source_url`, `language`,
`tags`, `document_type` and the embedding columns. `token_count` is derivable
(`parser.estimate_tokens`), and `language` and `source_url` are never populated
by any current writer, so nothing is lost today. A rebuild that later needs
`source_url` would have nowhere to read it.

## Candidate change: rebuild the live index from the archive

One new service module, `api/services/rehydrate.py`, plus one CLI command. It
reads the latest non-`DELETED` version per `memory_document_id` for a corpus,
takes `content`, runs the existing parser and chunker, embeds, and writes
`documents`, `chunks` and `ingestion_manifest`. Writes stay in L2: the command
never calls `record_version`, so it creates no new observations and no feed
events.

Why this one and not something else:

- Writer identity, trust classes and ACLs are already listed as gaps in
  `docs/performance/comparison/report.md` section 5. Each needs a migration, a
  new column and a contract change. None of them is about the layering claim.
- Archive export and import needs a format spec, schema compatibility rules and
  snapshot membership preservation. It is a program of work, not one step.
- A projection-specific archive loader is a performance change, and section 23 of
  that report already sets its gate to semantic equivalence. Rebuilding L2 is
  smaller, and its gate is the same kind: prove it is byte-equivalent.
- A verification scan is useful but diagnostic. It reports that L2 is gone; it
  does not put L2 back.

The change is small because every hard part already exists, starting with the
parser, the chunker, the embedder, `upsert_document`, `insert_chunks`,
`update_manifest` and the corpus writer lock. New code is a loop over archive
rows plus a decision about which document id to use.

## Risk

| Risk | Severity | Mitigation |
|---|---|---|
| Recomputed `documents.id` diverges from the id the archive recorded, changing `Source.document_id` and `Evidence.document_id` in replays | High | Take the id from `memory_versions.document_id`. Fall back to `upsert_document` only for a document with no archive row. Add a test that asserts the id is unchanged across a rebuild |
| A rebuild that calls `record_version` double-counts observations and pollutes the feed | High | Write L2 rows directly. The `004_memory.py` mutation trigger already blocks `UPDATE`/`DELETE`/`TRUNCATE` on the archive, so an accidental archive write fails loudly rather than silently |
| A chunker change is invisible to the archive, because the version fingerprint covers content and metadata but not segmentation | Medium | Expected by design, documented in `docs/architecture/memory-model.md`. The rebuild should report the chunker parameters it used, and a `max_chars` change should be an explicit operator action |
| Mixed-model vectors after a model change, with `embedding_version` hardcoded to `"1.0"` | Medium | Have the rebuild refuse to mix models, or write a real version string. Separate the text-only rebuild from the embedding rebuild so an operator can restore search without the model |
| Silent degradation persists while L2 is empty | Medium | The rebuild closes the gap for the future. It does not fix detection for corpora that have never been archived. A `doctor` check comparing `count(chunks)` against the count of non-tombstone versions would cover that separately |
| Rehydration cost is unbounded on a large corpus and needs the model | Low | Page by document, one transaction per document, reuse the existing corpus lock. This is an operator command, not a request path |

## Recommendation

Build the rehydration command. It is the smallest change that turns the
durable-base claim into something an operator can run, and the evidence above
says the derivation is already correct.

Two conditions on the work:

1. Gate it on byte-level equivalence, the same gate section 23 of
   `docs/performance/comparison/report.md` sets for the projection loader.
   Ingest a production-shaped corpus, record the canonical JSON of all eight
   memory operations, wipe L2, rebuild from L0, and require the JSON to be
   unchanged and the document ids to be unchanged. My probe is a working sketch
   of that test.
2. Decide the L0 completeness question explicitly, because it is a product
   choice and not an implementation detail. Today archiving is opt-in per corpus
   and silent. Either document that L0 exists only where archiving is on, or make
   the archive unconditional for corpora that are expected to be durable. The
   second option is the one that matches the thesis, and it is a one-line change
   at the call sites, but it changes what every ingest writes and should be
   chosen deliberately.

I would not bundle either change with M009 work. The rehydration command is
additive and touches no existing contract. The L0 default changes ingest cost for
every document, which is a real behavioural change and deserves its own
decision.

## Reproducing this

```text
MEMORY_TEST_DATABASE_URL=postgresql://mpadmin:secret@localhost:5432/mindpalace \
  ./venvmp/bin/python -m pytest \
  tests/test_memory_core.py tests/test_memory_public.py tests/test_memory_query.py \
  tests/test_memory_snapshot_seal.py tests/test_memory_multiple_evidence.py \
  tests/test_memory_ingestion.py tests/test_ingestion_lifecycle.py \
  tests/test_m009_feed.py tests/test_m009_feed_integration.py \
  tests/test_corpus_isolation.py tests/test_memory_http.py tests/test_sdk.py -q
```

Result: 360 passed.

One environment note. The first run of
`tests/test_migration_contract.py::test_migration_at_head` failed because the
local database was still at `005_multiple_evidence` while the repository head is
`006_snapshot_membership_seal`. Later in the same session the local database
reported `006` and `memory_snapshot_seals` present, and all six tests in that file
passed. I did not run `alembic upgrade` and did not change the database outside
rolled-back probe transactions. Treat that test as a report about local database
state, not about the code.
