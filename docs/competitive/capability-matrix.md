# Capability matrix

Scope: what four external memory systems do, read from public sources on
2026-09-26 and pinned to the revisions below. This is a record of observed
architecture, not a ranking. No head-to-head run against any of these systems
exists in this repository, so nothing here says one system is better, faster or
more accurate than another.

Every cell is one of four things:

- **Yes**, the reviewed material describes the capability.
- **Partial**, the capability exists in a different form or with a narrower
  contract than Mind Palace's.
- **Not found**, the reviewed material does not describe it. This is a
  statement about the review, not a claim that the system lacks it.
- **Unknown**, the material needed to answer was not reached.

## Pinned sources

| System | Repository | Revision reviewed | Package version | Files in tree at pin |
|---|---|---|---|---:|
| Graphiti | `github.com/getzep/graphiti` | `ba4a9cb32495b6864160616f8dfa2b898f4a500c`, 2026-09-25 | `graphiti-core` 0.30.2 on PyPI, tag `v0.30.2` | 384 |
| Mem0 (OSS) | `github.com/mem0ai/mem0` | `94c3fe9f238f3dbf29c9ce98643bd71eb13077cd`, 2026-09-25 | `mem0ai` 2.2.1 on PyPI, tag `v2.2.1` | 1835 |
| Letta / MemFS | `github.com/letta-ai/letta` | `5bcdd177d70fa2b31a754cfcd801e77b2e1ab16a`, 2026-09-10 | none published under that name | 12 |
| Letta current source | `github.com/letta-ai/letta-code` | `5e420db9f2871bc3106ba14372d4f8d94c5e63ae`, 2026-09-26 | `letta` 0.33.2 on PyPI, tag `v0.33.2` | not counted |
| Letta documentation | `github.com/letta-ai/letta-docs-md` | `691c48b202a1efa3dddbe585d5cebe709cfee202`, 2026-09-25 | not applicable | 593 |
| MemPalace | `github.com/MemPalace/mempalace` | `8c4865f70c49b6346c53474a9e5684c5f17d3fa9`, 2026-09-24, branch `develop` | `mempalace` 3.10.0 on PyPI, tag `v3.10.0` | 731 |

Notes on the pins. The `milla-jovovich/mempalace` path resolves to the same
repository as `MemPalace/mempalace` through a GitHub redirect, so the
organisation rename is one repository, not a fork. MemPalace's README names
three official sources and warns about impostor domains: the GitHub repository,
the PyPI package, and `mempalaceofficial.com`.

The `letta-ai/letta` repository no longer contains server code. Its README
states the current source is `letta-ai/letta-code` and that the retired V1 API
server sits on an `archive` branch. At the pinned commit the repository holds
12 files: policies, contribution guides and a citation file. The memory model
below therefore comes from the documentation mirror, not from reading memory
source code.

An earlier audit in this repository pinned Graphiti at
`47f648213da99aa96ec190c61dbb3e0d163e1250` and Mem0 at
`8d6c001966573786d36908bfe4dfd52935749200` with `mem0ai` 2.2.0. Both pins
remain valid; both are now behind. `graphiti-core` 0.30.2 is unchanged.
`mem0ai` moved to 2.2.1 on 2026-09-25.

## Raw and verbatim preservation

Mind Palace archives the exact source bytes per version and requires a claim to
be a literal substring of a chunk that was archived. The trigger enforcing that
is in `migrations/versions/004_memory.py`.

| System | Finding |
|---|---|
| Mind Palace | Yes. Raw content per version, append-only under ordinary DML by statement-level trigger. Archived in `memory_versions`. |
| Graphiti | Partial. The README describes episodes as "the raw data as ingested" and as the ground truth stream that every derived fact traces back to. Edges carry episode UUIDs, not quotes or offsets. The episode node schema was not read at this pin, so whether raw text is retained verbatim is Unknown. |
| Mem0 (OSS) | Partial, off by default. `docs/core-concepts/how-it-works.mdx` states that by default Mem0 stores extracted memories, not a verbatim transcript, and that `infer=False` stores raw content exactly as provided. |
| Letta / MemFS | Yes for files, and separate for messages. Memory is Markdown with YAML frontmatter addressed by path, committed to the agent's own git repository. Archives and passages store text, `created_at`, metadata, tags and an embedding. |
| MemPalace | Yes for the storage default. The README says MemPalace stores conversation history as verbatim text, does not summarize, extract or paraphrase, and that the index is drawers of original content. |

MemPalace carries a second, non-default encoding called AAAK, which its own
documentation describes as lossy and irreversible. The AAAK page states it
scored 84.2% R@5 against 96.6% for raw verbatim mode and is not the storage
default. That is a useful, self-reported negative result.

## Authoritative structure

The question here is who decides what a stored statement means, and whether a
reader can inspect that decision.

| System | Finding |
|---|---|
| Mind Palace | Authored. Claims come from the document front matter, never from a model. `api/services/memory.py` rejects a claim whose text is not present in a supporting chunk. |
| Graphiti | Extracted and then structured. Entities, facts, communities and optional developer-defined ontology. Entity summaries evolve as new episodes arrive. |
| Mem0 (OSS) | Extracted by an LLM by default. The documented sequence is context lookup, LLM fact extraction, deduplication and embedding, then entity extraction. The path is additive: a new fact is stored without silently rewriting an old one, and `update` or `delete` is the explicit correction route. |
| Letta / MemFS | Agent-authored. The agent edits its own files with ordinary file tools and commits. Structure is a directory tree the agent chooses, and the tree itself is in the system prompt. |
| MemPalace | Mixed. Wing and room assignment is deterministic: room routing scores folder path, then filename, then room keywords, and a wing name derives from the project directory name. Optional model paths exist alongside it, including general extraction into five types, entity detection, closets and fact checking. |

MemPalace is explicit that its taxonomy is metadata filtering in the underlying
vector store, not a retrieval mechanism of its own, and that a closet is a
summary that points back to the drawer while the persisted path remains the
drawer text. Contradiction checking is documented as planned rather than
shipped: the page carries an experimental warning and says the codebase holds
the graph primitives but no complete tool exposed through the CLI or MCP
server.

## Evidence and provenance

| System | Finding |
|---|---|
| Mind Palace | Exact quote plus character offsets into a named archived chunk, enforced by a `BEFORE INSERT` trigger, with `end_offset = start_offset + char_length(quote)` as a table check. Evidence is mandatory at commit time through a deferred constraint trigger. |
| Graphiti | Episode-level. Every entity and edge lists the episode UUIDs that produced it. No quote, span or offset concept appeared in the material read. |
| Mem0 (OSS) | History-level. The library default keeps a history store at `~/.mem0/history.db`. The internal history schema was not read, so the granularity of what is retained is Unknown. Entities extracted at write time are stored for search-time matching. |
| Letta / MemFS | Commit-level. Every memory edit becomes a git commit, which gives version history, conflict resolution and a boundary between saved and unsaved memory. The docs also state that the memory workflow backs up the repository before splitting, merging or restructuring. |
| MemPalace | Source-level. Drawers record the file they were mined from and a `filed_at` time. `mempalace/source_identity.py` records the inode of the mining directory and explains at length why device numbers cannot separate bind mounts. No quote or offset concept appeared in the material read. |

## Temporal semantics

| System | Finding |
|---|---|
| Mind Palace | Two clocks held apart. `observed_at` is the service's clock at insert; `valid_from` and `valid_until` are authored and half-open. Status comes from both. See `docs/architecture/temporal-semantics.md`. |
| Graphiti | Bi-temporal per edge. `EntityEdge` carries `valid_at`, `invalid_at`, `expired_at` and `created_at`, and `search_filters.py` builds date filters on each of those plus `edge_uuids`. The README states old facts are invalidated, not deleted, so history is preserved. |
| Mem0 (OSS) | Write-time metadata. Retrieval uses recency plus time metadata extracted at write time against the query's temporal intent. Per-memory expiration and decay are documented under the Platform documentation tree, not the open-source tree. |
| Letta / MemFS | Version order, not validity windows. Files are ordered by git history. Per-assertion validity bounds were not found in the material read. |
| MemPalace | Filetime window. `mempalace/date_window.py` defines `since` as inclusive and `before` as exclusive over drawer `filed_at`, compares wall clock and timezone naive, and excludes any row whose `filed_at` is missing or unparseable whenever a bound is active. |

MemPalace's exclusion rule is the part worth keeping in mind. A date-filtered
result never silently includes rows of unknown age, which is the same instinct
behind Mind Palace refusing to promote an out-of-window claim to `CURRENT`. The
difference is what the filter applies to: a filing time in MemPalace, an
authored validity window plus an observation time in Mind Palace.

## Retrieval

| System | Finding |
|---|---|
| Mind Palace | Authored policy over a time-scoped archive, then ranking. A relevance score can include or exclude an authored key and cannot change status. Output is bounded in Unicode characters of the complete canonical JSON, from 512 to 128000. |
| Graphiti | Hybrid, described in the README as semantic embeddings plus BM25 keyword plus graph traversal, with no reliance on LLM summarization. Reranking by graph distance is in the quickstart. |
| Mem0 (OSS) | Vector search with configurable filters and an optional reranker. As a library the default vector store is a local Qdrant under `/tmp/qdrant`; the self-hosted server defaults to Postgres with pgvector. |
| Letta / MemFS | No index by default. The MemFS page states plainly that MemFS has no semantic or vector index and that agents find memory with ordinary file search and read tools. A MemFS Search mod adds keyword search, and semantic or hybrid modes require QMD to be installed and indexed. Conversation-history search is separate: full-text, vector and hybrid on cloud, full-text only on local backends. |
| MemPalace | Vector search over verbatim drawers by default, with a hybrid mode adding keyword, temporal and preference boosts. Closet and room metadata act as filters. |

## Feed and export

| System | Finding |
|---|---|
| Mind Palace | Keyset feed over `memory_versions` ordered by `(observed_at, version_id)` with an HMAC corpus-bound cursor, exposed through REST, SDK and CLI and deliberately absent from MCP. No archive export or import is implemented. |
| Graphiti | Not found. Episode management covers add, retrieve and delete. No change feed or archive interchange was described. |
| Mem0 (OSS) | Retrieval-level only. `get_all` returns unranked records for export and audit. The structured export cookbook is marked as working with the Platform `MemoryClient`, and the memory export, expiration and decay pages sit under the Platform documentation tree. |
| Letta / MemFS | Repository-level. Archives and passages have their own API, agents have an `export_file` method, and `letta-ai/agent-file` is published as an open file format for serializing stateful agents. |
| MemPalace | Both. The logstream is a small append-only event log in `logstream.sqlite3` next to the palace, storing bodies verbatim with a SHA-256, returning a `seq` on every event and resuming with `since_event_id`; artifacts carry `sha256` and `size_bytes` and are capped at 4 MiB in version 1. Separately, `mempalace/exporter.py` writes a browsable folder of Markdown files in paginated batches, and `mempalace/backups.py` prunes timestamped backups to `MempalaceConfig.max_backups`, default 10. |

The MemPalace logstream is the closest match in this set to the operational
feed, and it makes stronger durability promises than this repository does: it
states that corrections are new events referencing prior ones rather than
edits, and that nothing exists only in a socket. Mind Palace's feed is a
successive-MVCC read with no lossless late-arrival guarantee, which its own
documentation says plainly.

## Interfaces

| System | Finding |
|---|---|
| Mind Palace | REST, Python SDK, remote CLI and MCP over one typed boundary. The feed is REST, SDK and CLI only. |
| Graphiti | Python library plus an MCP server under `mcp_server/` with Docker Compose configurations. |
| Mem0 (OSS) | Python and Node SDKs, a self-hosted Docker Compose server with per-user API keys and a request audit log, a REST API, an OpenAI-compatible surface, editor and agent plugins, and a CLI. |
| Letta / MemFS | npm `@letta-ai/letta-code`, `letta server` App Server, desktop app for three platforms, a browser chat, Slack, Telegram, Discord and custom channels, a TypeScript Agent SDK, and separate Python and Node API SDKs. |
| MemPalace | 45 documented MCP tools, a CLI, a Python API, a container image at `ghcr.io/mempalace/mempalace`, and skills or plugins for Claude Code, Codex, Cursor, Antigravity, OpenClaw and Hermes. |

## Local-first and self-hosting

| System | Finding |
|---|---|
| Mind Palace | Self-hosted PostgreSQL with pgvector through the supplied compose file. The API image pins CPU-only Torch and runs as UID 10001. Corpus namespaces are not authorization and there is no built-in authentication. |
| Graphiti | Self-hosted, and it needs a graph database: Neo4j 5.26, FalkorDB 1.1.2, or Amazon Neptune with OpenSearch Serverless. Kuzu 0.11.2 is listed as deprecated. Python 3.10 or higher, and ingestion needs an LLM, with OpenAI the default. |
| Mem0 (OSS) | Self-hostable as a library or as a server. The library defaults reach OpenAI, so an air-gapped install means supplying a local provider. The server stack is Docker Compose with a dashboard and per-user API keys. |
| Letta / MemFS | Local agents commit to a repository on the current machine and the docs state you are then responsible for backing it up. Shared memory repositories are described as requiring cloud-hosted agents, and the docs note that self-hosted deployments can share memory by pointing agents at their own git remote. |
| MemPalace | Local-first by default. The README states nothing leaves the machine unless you opt in, and that the first embedding call downloads about 80 MB for the default model into the data directory. An opt-in shared-brain hub and a team server compose file exist. |

Graphiti's LLM dependency at ingestion is a real difference from Mind Palace,
which performs no inference and treats the semantic runtime as an optional
extension loaded on first use.

## Benchmark methodology

| System | Finding |
|---|---|
| Mind Palace | A frozen 60-question held-out corpus with hash-checked questions and policy, a canonical result hash, and safety invariants reported per run. The current result is 47 of 60 exact or full, zero false-current promotions, zero execution failures, and 60 of 60 on provenance, budget and conflict closure. A separate live retrieval study over 202 documents and 98 hand-labelled queries found every paired-difference interval for Recall@3 containing zero, so no strategy was separable at that sample size. No external system was run. |
| Graphiti | An evaluation directory with an end-to-end graph-building script and the public `longmemeval_oracle` dataset from Hugging Face. The dataset's own README states it was not created by the project. No published result was read, so Graphiti's scores are Unknown here. |
| Mem0 (OSS) | Publishes LoCoMo 92.5 and LongMemEval 94.4, plus BEAM at 1M and 10M tokens, at a top-200 retrieval budget and under 7,000 tokens per retrieval call, against full-context baselines the docs put at 25,000 or more. The documentation makes the methodological point that some benchmarks can be materially improved by retrieval strategy or context size alone, so it reports under a constrained budget. |
| Letta / MemFS | Separate evaluation repositories exist: `letta-ai/letta-evals`, `letta-ai/recovery-bench` and `letta-ai/letta-leaderboard`. None was read, so Letta's methodology and scores are Unknown here. |
| MemPalace | The most explicit methodology writing in this set. Scripts are committed, per-question result JSONL files are checked in, and the project states results are deterministic for the same data, script and split seed. It splits LongMemEval into 50 development and 450 held-out questions, reports 96.6% R@5 raw on the full 500 and 98.4% on the held-out 450, and calls the full-500 hybrid number "teaching to the test". It also retracts an earlier 100% LoCoMo figure that used `top_k=50` above the per-conversation session count, and it removed a cross-system table that had placed its own retrieval recall next to other projects' end-to-end QA accuracy. |

The MemPalace benchmark page states the reason directly: a system can have
perfect retrieval recall and poor answer quality, and the reverse. That is the
same separation of metrics that this repository's own README insists on, and it
is the reason a MemPalace number and a Mind Palace number cannot be placed in
one column. MemPalace publishes retrieval recall. Mem0 publishes end-to-end QA
accuracy. Mind Palace publishes a frozen 60-question exact-or-full result.
These are three different measurements.

## What could not be verified

- Whether Graphiti retains raw episode text verbatim. The README's description
  of episodes was read; the episode node schema was not.
- Graphiti's benchmark scores and its MCP tool list. Neither was read.
- Mem0's history store schema, and whether any quote or offset provenance
  exists inside `mem0/memory/main.py`. The file is 170 KB and was not opened.
- Letta's memory source code. The repository that used to hold it no longer
  does, and the replacement repository was identified but its memory internals
  were not read. The retired V1 server on the `archive` branch is unpinned here.
- MemPalace's ChromaDB and pgvector backend internals, its knowledge graph
  date handling, its closet generation, and the `logstream.py` implementation.
  The logstream behaviour above is taken from its concept page and tool
  reference, not from the module.
- Whether any of these four systems has been run against the Mind Palace
  corpus. None has. Every comparison in this repository remains architectural.
