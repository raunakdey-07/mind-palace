# First-use audit

Run as a developer who has never seen Mind Palace, against a clean checkout and a
fresh PostgreSQL 15 database. Every finding below was reproduced, not inferred.
The command transcripts are the evidence; nothing here describes intent.

## The twelve questions

| # | Question | Answer a newcomer actually gets | Verdict |
|---|---|---|---|
| 1 | What is this? | A 48 KB README that opens on "Project status", then architecture, then a 200-case evaluation. No first interaction until line 401. | **Fails** |
| 2 | Why would I use it? | Buried under "Why memory beyond RAG?", after seven sections of capability inventory. | **Fails** |
| 3 | How do I install it? | `git clone`, `venv`, `pip install -r requirements.txt`, `pip install -e .`, `docker compose up`, `export DATABASE_URL`, `export MIND_PALACE_CURSOR_SECRET`, `alembic upgrade head`. | **Passes, but 8 steps before anything works** |
| 4 | How do I create memory? | There is no `remember`. A plain sentence does not create memory. See finding A. | **Fails** |
| 5 | How do I retrieve memory? | `mindpalace memory query --corpus X --query Y` prints canonical JSON. `--corpus` is mandatory even though a `default` corpus exists and is seeded by migration `002`. | **Partial** |
| 6 | How do I know what was stored? | Nothing human-readable. No `inspect`, no `explain`. The receipt exists only inside the JSON. | **Fails** |
| 7 | How do I ask for historical state? | `mindpalace memory history --corpus X --as-of <aware ISO-8601>`. Correct semantics, JSON only. | **Partial** |
| 8 | How do I explain an answer? | `memory_explain` exists on MCP only. No CLI, no SDK name. | **Fails** |
| 9 | How do I export it? | No CLI. Hand-assemble a Memory Pack, then `mindpalace-proof receipt --pack ... --claim-key ...`. | **Fails** |
| 10 | How do I verify it? | `mindpalace-proof verify receipt.json --pack pack.json` — excellent, dependency-free, but unreachable from the product flow. | **Partial** |
| 11 | How do I connect an agent? | `python -m mcp_server` with 14 tools, 4 of them legacy retrieval. | **Partial** |
| 12 | What happens when something goes wrong? | `transport_error: Memory service is unavailable`. | **Fails** |

## Blocking findings

**A. A plain sentence creates no memory.** This is the central problem. The
authoritative model requires `claims:` in YAML frontmatter; ordinary text is
indexed for live retrieval only. Reproduced:

```python
await IngestionService().ingest_file(
    content="Production uses PostgreSQL.", path="note.md", corpus_id=...)
# -> {'success': True, 'chunk_count': 1, 'message': 'Ingested 1 chunks'}

await execute("query", MemoryRequest(corpus="audit", query="What database does production use?"))
# -> current_memories=[], constraints=['NO_RELEVANT_MEMORY']
```

A newcomer types their first fact into the only write path available and gets a
success message for memory that does not exist. Authored claims exist, but
authoring one requires knowing the frontmatter contract, which the README does
not present as the primary write path.

**B. The model is mandatory for a write.** `_ingest_memory_content` calls
`embedder.embed(...)` unconditionally, so the first write needs a Hugging Face
download. The read path already degrades to lexical when the model is missing
(`memory_query.query`); the write path does not. Embeddings are L2 and provably
disposable, so a first memory that cannot be written offline is a product defect,
not a safety property.

**C. The corpus is an obstacle on the first command.** Every `memory`
subcommand requires `--corpus`. A corpus named `default` is seeded by migration
`002` and works end to end, but nothing tells the newcomer it exists.

**D. Errors do not say what to do.** Reproduced with the database stopped:

```
$ mindpalace memory query --corpus audit2 --query x
transport_error: Memory service is unavailable
```

No port, no reason, no recovery command. The local `reindex` path leaks
`OperationalError` outright.

**E. Explain is on the wrong surface.** The single most valuable operation —
"why was this returned?" — is reachable only by an agent over MCP. A developer
using the CLI or the SDK has no equivalent.

## The UX backlog

Ordered by how much each unblocks. Level 1 items are mandatory for the product
to exist; Level 2 items are what make it trustworthy; Level 3 items are what make
it portable. Every item below was closed by this release; each names where it
lives and the test that holds it.

### Level 1 — simple memory

- [x] `mindpalace remember "<text>"` — a statement becomes authoritative memory
      with evidence, through the existing ingestion and archive semantics.
      `api/services/remember.py`.
- [x] `mindpalace remember --file <path>` — a Markdown file; authored claims kept.
- [x] `mindpalace init` — verify storage, create the corpus, print the next command.
- [x] `mindpalace recall "<question>"` — human answer, source and time by default;
      the canonical JSON only on `--json`.
- [x] Corpus defaults to `default`; `--corpus` remains for scoping.
- [x] SDK `client.remember(...)` mirroring the CLI.
- [x] Writes succeed without an embedding model, and never load one. L2 fills in
      later via `mindpalace reindex`.
- [x] REST `POST /api/memory/remember`, deliberately statement-only: an
      unauthenticated endpoint must not read the server's filesystem.

### Level 2 — explainable memory

- [x] `mindpalace explain "<question>"` — ANSWER / SOURCE / TIME / EVIDENCE /
      HISTORY / VERIFICATION, from the live response and its receipt.
- [x] `mindpalace history "<key>"` — the supersession chain, oldest first.
- [x] `--explain` on `recall`, and `client.explain(...)`.
- [x] Errors answer what happened, why, and what to run next.

### Level 3 — verifiable memory

- [x] `mindpalace receipt "<question>"` — a self-contained receipt file.
- [x] `mindpalace verify <file>` — offline: no PostgreSQL, no model, no server.
- [x] The same file verifies on a bare `pip install mindpalace-os`, through
      `mindpalace-proof`, because the CLI is an optional extra.
- [x] The receipt contract itself is unchanged; this is reachability, not design.

### Cross-surface

- [x] `memory_recall` and `memory_explain` as the primary MCP tools.
- [x] REST, SDK, MCP and CLI proven to return the same authoritative memory.
- [x] README opens on the five commands, not on architecture.

### What the tests hold

`tests/test_m015_golden_path.py` is the permanent contract for all of the above.
The properties it protects, in the order a developer meets them: a statement
becomes memory with exact-substring evidence; recall answers and abstains; a
multi-line statement and a Markdown file both work; the same write twice,
concurrently, or after a lost response is one memory; the same key supersedes;
`as_of` answers from the past; every surface agrees; failures name the fix.

## What must not change

The archive, the receipt schema, Memory Pack v1, and the supersession,
temporal and conflict semantics are correct. The problem is that a person cannot
reach them. So this milestone adds reach, not architecture: `remember` composes
the existing ingestion path, `explain` renders the existing response, and
`verify` calls the existing verifier. No new persistence, no new write semantics,
no new trust model.

The notable exception, and the only place where an existing behaviour changes:
the authoritative write path stops requiring an embedding model. This is
deliberate. Embeddings are L2; `rehydrate` rebuilds them from the archive; the
read path already treats them as optional. A write that fails without them
contradicts the architecture the project already documents.