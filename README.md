# Mind Palace

[![Release](https://img.shields.io/github/v/release/raunakdey-07/mind-palace)](https://github.com/raunakdey-07/mind-palace/releases)
[![License](https://img.shields.io/badge/license-Apache%202.0-blue.svg)](LICENSE)
[![CI](https://github.com/raunakdey-07/mind-palace/actions/workflows/ci.yml/badge.svg)](https://github.com/raunakdey-07/mind-palace/actions/workflows/ci.yml)

**Mind Palace is a persistent memory layer for AI applications.** It turns an evolving corpus into versioned, evidence-backed memory for current knowledge, history, changes, provenance, and reproducible context—not a promise of perfect truth.

```python
from mindpalace_sdk import MindPalace

mp = MindPalace("my-corpus")
mp.sync("./docs")  # Markdown with explicitly authored claims

current = mp.memory.current(query="streaming")
print(current.current_memories)
print(current.conflicts)  # conflicting sources are retained, not silently resolved
pack = mp.memory.pack(query="streaming", budget=8000)
print(pack.canonical_json())  # complete JSON bounded in Unicode characters
```

## What problem it solves

AI applications need your knowledge — documentation, notes, research, project files — as context. Wiring that up usually means gluing together a parser, chunker, embedding pipeline, vector database, and retrieval logic, then hoping the results are trustworthy.

Mind Palace provides that corpus-to-context layer, plus an append-only archive for
explicitly authored claims:

- **Point it at a corpus** (a folder of Markdown files)
- **It ingests, chunks, embeds, and indexes** into PostgreSQL + pgvector
- **Your AI application asks questions** and receives bounded, attributable, model-ready context
- **The corpus persists** — it survives restarts and updates incrementally
- **Authored assertions evolve** — same-document replacements, conflicts across
  sources, deletion/restoration, as-of reads, and committed snapshot replay retain
  attributable evidence

Memory is not independent fact verification. Exact source containment proves
provenance, not truth or semantic entailment. Public memory queries use lexical
AND matching; `query="streaming"` matches the structured key
`architecture.streaming`, while an arbitrary natural-language question may not.

It is not a chatbot, not an agent framework, and not a generic vector database. It is the smallest, clearest layer between a corpus and an AI system that needs to remember it.

## What it does not do

- No chat UI, no agent loops, no LLM calls where deterministic code suffices
- Not agent memory (conversations, preferences) — corpus memory only
- Local/self-hosted is the first-class path; embedding weights may need an initial
  Hugging Face download (cached weights can run offline)
- Not tied to any AI vendor: works with OpenAI-compatible apps, Anthropic apps, local models via Ollama, plain Python, plain HTTP, or MCP clients

---

## Quick Start

### 1. Install & start infrastructure

```bash
git clone https://github.com/raunakdey-07/mind-palace.git
cd mind-palace
python -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt && pip install -e .

docker compose up -d postgresql          # or podman-compose up -d postgresql
```

### 2. Migrate (creates schema + provisions pgvector/pgcrypto/pg_trgm)

```bash
make migrate
```

### 3. Create a corpus, sync documents, get context

```bash
export DATABASE_URL=postgresql://mpadmin:secret@localhost:5432/mindpalace

python - <<'EOF'
from mindpalace_sdk import MindPalace

mp = MindPalace("my-first-corpus")
summary = mp.sync("examples/docs")
print(summary)                # added/changed/unchanged/deleted counts

pack = mp.context("How does authentication work?", budget_tokens=2000)
print(pack.context)
for s in pack.sources:
    print(f"  source: {s.title} ({s.path})")
EOF
```

This is the live retrieval loop. `context()` is token-estimated live retrieval;
`memory.pack()` is character-bounded archived assertion memory. They are separate
contracts, not interchangeable budget units.

### 4. Run the evolving memory demo (M005)

With the same installed environment and migrated database:

```bash
python examples/memory_demo.py
```

The script creates a unique dedicated corpus, stages source copies in a temporary
folder, and checks **Redis Streams → Kafka → Kafka managed → deletion → restoration**.
Two active authentication documents deliberately disagree under the same key. It
archives SDK current/history/changes/evidence/as-of/snapshot/replay/pack responses,
checks exact authored evidence and complete-envelope pack bounds, and prints actual
IDs, observation times, and stage measurements only when run.

Use `python examples/memory_demo.py --corpus m005-dispatch-my-first-run` for a
user-selected **new** name. Every existing name is refused to protect user data;
there is no automatic corpus deletion. Output goes to
`examples/evolving-project/runs/<corpus>/` (Git-ignored). No external paid LLM is
called; sync still uses local embeddings, which may require cached/downloaded
Hugging Face weights. This is an assertion-driven example, not a performance
benchmark or production-readiness claim.

See [the walkthrough and exact setup commands](examples/MEMORY.md),
[the authored engineering fixtures](examples/evolving-project/README.md), and
[the implemented SDK/REST/CLI/MCP memory contract](examples/MEMORY_API.md).

---

## The lifecycle

```text
create corpus
    ↓
sync / ingest corpus        (added / changed / unchanged / deleted reconciliation)
    ↓
inspect corpus              (counts, last ingestion time)
    ↓
search                      ("what is relevant?")
    ↓
pack context                ("what should I give the model?" — bounded, attributed)
    ↓
AI application consumes context
```

Search and context packing are deliberately separate: search ranks relevance;
context packing decides what fits in a prompt within an explicit token budget.

## Corpus isolation

Corpora are explicit namespaces. Documents, chunks, and embeddings belong to
exactly one corpus:

- searches never leak across corpora (enforced in SQL, proven by tests)
- ingestion cannot overwrite another corpus's documents
- the same relative path may exist in multiple corpora independently
- corpus names scope queries, but **are not authentication or authorization**
- M004 append-only archive guards currently block deletion of corpora with archive
  rows; corpus deletion is not an archive cleanup or erasure mechanism

## Synchronization

`sync` reconciles indexed state against the source directory:

| State | Action |
|---|---|
| added | new files are chunked, embedded, indexed |
| changed | files are reprocessed; stale chunks replaced |
| unchanged | skipped via content-hash manifest |
| deleted | removed from source → removed from live index; archived sources receive tombstones |

A failed document never leaves a false "successfully indexed" state.

## Context packing

`context()` returns a structured pack:

```json
{
  "query": "...",
  "context": "...bounded evidence text...",
  "sources": [{"title": "...", "path": "...", "doc_id": "..."}],
  "chunks": [{"text": "...", "score": 0.87, "rank": 1, "source": {...}}],
  "token_estimate": 1234,
  "strategy": "hybrid_rrf",
  "truncated": false
}
```

Budgets are enforced, never silently overflowed. Strongest evidence is kept
first; overflow drops from the bottom. Sources derive only from retained
evidence.

## Interfaces

### Python SDK (`mindpalace_sdk.py`)

```python
from mindpalace_sdk import MindPalace

mp = MindPalace("corpus-name")       # created if missing
summary = mp.sync("./docs")
results = mp.search("query", k=5)
pack = mp.context("question", budget_tokens=4000)
```

### REST API

```
POST   /api/corpora                  create a corpus
GET    /api/corpora                  list corpora
GET    /api/corpora/{name}           inspect one corpus
DELETE /api/corpora/{name}           delete a corpus (blocked when M004 archive rows exist)
POST   /api/corpora/{name}/sync      sync with a directory
GET    /api/search?q=&k=&hybrid&rrf&rerank   raw search
GET    /api/context?q=&corpus=&budget_tokens=  model-ready context pack
POST   /api/query/ask                grounded answer (configured LLM)
POST   /api/memory/current           current assertions and conflicts
POST   /api/memory/history           assertion history
POST   /api/memory/changes           before/after events
POST   /api/memory/evidence          evidence for a claim
POST   /api/memory/as-of             assertions at an aware observation cutoff
POST   /api/memory/snapshot          commit whole-corpus immutable references
POST   /api/memory/replay            replay a saved snapshot
POST   /api/memory/pack              complete JSON bounded in Unicode characters
```

Backend unavailability returns **503**, never an empty success.

### MCP server

Expose corpora as tools to any MCP-compatible client:

```json
{
  "mcpServers": {
    "mind-palace": {
      "command": "python",
      "args": ["-m", "mcp_server"],
      "env": {"DATABASE_URL": "postgresql://..."}
    }
  }
}
```

Tools: `context`, `search`, `sync`, `list_corpora`, plus `memory_current`,
`memory_history`, `memory_changes`, `memory_evidence`, `memory_as_of`,
`memory_snapshot`, `memory_replay`, and `memory_pack`.

### CLI memory reads

`python -m cli.main memory current --corpus my-corpus --query streaming` uses the
REST service at `http://127.0.0.1:8000` (override with `--base-url`). All eight
memory operations have CLI commands. See [MEMORY_API.md](examples/MEMORY_API.md)
for selectors, errors, and differences between adapter signatures and accepted
operations. Memory pack budgets range from 512 to 128000 Unicode characters;
an in-range budget may still fail if the complete response envelope cannot fit.

---

## Retrieval architecture

```text
Markdown → parse (frontmatter + heading paths) → chunk (structure-aware)
        → embed (all-MiniLM-L6-v2, 384-dim, normalized)
        → PostgreSQL + pgvector (HNSW)
        → hybrid retrieval (vector + pg_trgm keyword)
        → Reciprocal Rank Fusion
        → optional cross-encoder reranking (opt-in)
        → context packing (budgeted, attributed)
```

**Default strategy: hybrid + RRF.** Measured on a 202-document benchmark,
all non-reranked strategies are statistically indistinguishable on Recall@3
(paired bootstrap, 95% CI). RRF is preferred because rank fusion needs no
score calibration as the corpus evolves. Vector-only (~6 ms) is an equivalent-
quality fast path. Reranking shows point-estimate gains but costs ~1.7 s per
query — opt-in only.

See `eval/EVALUATION.md` for methodology, confidence intervals, failure
analysis, and superseded-results history.

---

## Evaluation

The repository ships a 202-document evaluation corpus and a 98-query benchmark
across 14 categories (lexical, semantic, terminology-mismatch, negative,
metadata-filtered, similar-title discrimination, section-level, multi-document).

Ground truth is hand-labeled and mechanically validated against corpus
metadata. Strategy comparison uses paired bootstrap confidence intervals.

```bash
python -m cli.main eval strategies --candidates 10,20,50 --details
```

Honest scope: this validates regression safety and exposes failure classes.
It does not prove superiority over other retrieval systems.

## How Mind Palace compares

Honest positioning against adjacent categories:

| Category | What it gives you | What you still have to build | Mind Palace's difference |
|---|---|---|---|
| Vector databases (pgvector, Pinecone, Qdrant) | storage + similarity search | parsing, chunking, sync, context assembly, attribution | Mind Palace uses pgvector but owns the corpus→context pipeline |
| RAG frameworks (LangChain, LlamaIndex) | composable retrieval abstractions | your own evaluation, quality gates, provenance guarantees | Mind Palace provides the ingestion-to-context pipeline plus explicit assertion history |
| Agent memory (mem0, Zep) | conversation/episodic memory | corpus ingestion at scale | different problem: durable knowledge vs evolving experience |
| MCP memory servers | protocol transport | everything else | Mind Palace exposes retrieval and memory tools over MCP with budgets and provenance |
| Document search (search engines) | keyword/lexical matching | embeddings, LLM-readiness, budgets | hybrid retrieval tuned and benchmarked for LLM consumption |

The one-sentence version: **Mind Palace is not a database and not an agent framework. It is a focused corpus-memory layer that turns arbitrary knowledge into durable, attributable, model-ready context through simple APIs and standard AI integration.**

The retrieval core has a separate benchmark (`eval/EVALUATION.md`) and the live
context contract has tests (`tests/test_api_contract.py`). The evolving demo checks
specific authored transitions; it does not establish perfect truth, general
natural-language recall, or production readiness.

---

## Security posture

> **Corpus content is data, not instructions.**

Retrieved text must never be treated as trusted system instructions by a
consuming application. Mind Palace attributes all evidence so downstream
systems can verify provenance, but prompt-injection defense belongs in the
consuming application's design.

**Authentication is not implemented. Corpus namespaces are not access control.**
Restrict REST and MCP access to trusted callers and supply authentication and
authorization in your deployment before exposing sensitive data.

Database credentials come from `DATABASE_URL`; local defaults target disposable
containers only. The demo requires an explicit URL. Archive tables are append-only
under ordinary DML, not tamper-proof against the database owner. Retention/erasure
policy is unresolved; archived corpus deletion is currently blocked by M004 guards.

---

## Project structure

```text
api/
├── routers/            # REST surface (corpora, ingest, search, query, context)
├── models/schemas.py   # Pydantic request/response contracts
└── services/
    ├── corpora.py          # corpus namespaces
    ├── ingestion.py        # sync/reconciliation pipeline
    ├── parser.py           # frontmatter + heading-path extraction
    ├── embedder.py         # sentence-transformers wrapper
    ├── retrieval.py        # vector/hybrid/RRF/reranked search
    ├── reranker.py         # cross-encoder (opt-in)
    ├── context_packer.py   # budgeted, attributed context packs
    ├── evaluation.py       # Recall/Precision/MRR/nDCG metrics
    ├── benchmark.py        # strategy comparison runner
    └── confidence.py       # bootstrap CIs, paired differences

mindpalace_sdk.py     # Python SDK
mcp_server.py         # MCP integration
cli/                  # Typer CLI
content_eval/         # 202-doc evaluation corpus
eval/                 # benchmark dataset + EVALUATION.md report
migrations/           # Alembic migrations (self-provisioning extensions)
tests/                # unit, contract, integration, migration tests
examples/             # minimal runnable examples
```

---

## Development

```bash
pytest -q                       # unit suite (DB-backed tests skip without DATABASE_URL)
DATABASE_URL=... pytest -q      # full suite incl. integration tests
make migrate                    # apply migrations
black --check api cli tests migrations --line-length 100
flake8 api cli tests migrations --max-line-length=100
python -m cli.main eval strategies
```

---

## Known limitations

- Grounding of `/ask` answers is prompt-based, not formally verified
- Reranking latency (~1.7 s) is unresolved; it remains opt-in
- Source-format support is Markdown-only today (plain text/code planned)
- Deletion propagation requires an explicit `sync` call; archive evidence remains
- Memory queries are lexical AND, not natural-language semantic retrieval
- Conflicts require same-key differing values across active documents; no general
  contradiction detection or independent truthfulness verification
- Whole-directory sync is per-document atomic, not one atomic directory snapshot
- No built-in authentication; no supported archived-corpus erasure policy
- Benchmark differences between top strategies remain within statistical noise


---

## Contributing

Contributions welcome. Please open an issue before large architectural changes.
Retrieval changes must include benchmark evidence.

## License

Apache License 2.0. See [LICENSE](LICENSE) and [NOTICE](NOTICE).
