# Mind Palace

[![Release](https://img.shields.io/github/v/release/raunakdey-07/mind-palace)](https://github.com/raunakdey-07/mind-palace/releases)
[![License](https://img.shields.io/badge/license-Apache%202.0-blue.svg)](LICENSE)
[![CI](https://github.com/raunakdey-07/mind-palace/actions/workflows/ci.yml/badge.svg)](https://github.com/raunakdey-07/mind-palace/actions/workflows/ci.yml)

**Open-source memory infrastructure for AI applications. Give it a corpus; get reliable, attributable, model-ready context.**

```python
from mindpalace_sdk import MindPalace

mp = MindPalace("my-corpus")
mp.sync("./docs")

pack = mp.context("How does authentication work?", budget_tokens=4000)
print(pack.context)   # bounded evidence text, ready for your prompt
print(pack.sources)   # where every piece came from
```

## What problem it solves

AI applications need your knowledge — documentation, notes, research, project files — as context. Wiring that up usually means gluing together a parser, chunker, embedding pipeline, vector database, and retrieval logic, then hoping the results are trustworthy.

Mind Palace is that layer, done once and done properly:

- **Point it at a corpus** (a folder of Markdown files)
- **It ingests, chunks, embeds, and indexes** into PostgreSQL + pgvector
- **Your AI application asks questions** and receives bounded, attributable, model-ready context
- **The corpus persists** — it survives restarts, updates incrementally, and stays yours

It is not a chatbot, not an agent framework, and not a generic vector database. It is the smallest, clearest layer between a corpus and an AI system that needs to remember it.

## What it does not do

- No chat UI, no agent loops, no LLM calls where deterministic code suffices
- Not agent memory (conversations, preferences) — corpus memory only
- No cloud dependency: local/self-hosted is the first-class path
- Not tied to any AI vendor: works with OpenAI-compatible apps, Anthropic apps, local models via Ollama, plain Python, plain HTTP, or MCP clients

---

## Quick Start

### 1. Install & start infrastructure

```bash
git clone https://github.com/raunakdey-07/mind-palace.git
cd mind-palace
python -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt && pip install -e .

docker compose up -d          # or podman-compose up -d
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

That is the entire product loop. Everything below adds detail.

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
- deleting a corpus removes exactly its data

## Synchronization

`sync` reconciles indexed state against the source directory:

| State | Action |
|---|---|
| added | new files are chunked, embedded, indexed |
| changed | files are reprocessed; stale chunks replaced |
| unchanged | skipped via content-hash manifest |
| deleted | removed from source → removed from index |

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
DELETE /api/corpora/{name}           delete a corpus
POST   /api/corpora/{name}/sync      sync with a directory
GET    /api/search?q=&k=&hybrid&rrf&rerank   raw search
GET    /api/context?q=&corpus=&budget_tokens=  model-ready context pack
POST   /api/query/ask                grounded RAG answer (requires Ollama)
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

Tools: `context`, `search`, `sync`, `list_corpora`.

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
| RAG frameworks (LangChain, LlamaIndex) | composable retrieval abstractions | your own evaluation, quality gates, provenance guarantees | Mind Palace is a tested product with measured retrieval, not a toolkit |
| Agent memory (mem0, Zep) | conversation/episodic memory | corpus ingestion at scale | different problem: durable knowledge vs evolving experience |
| MCP memory servers | protocol transport | everything else | Mind Palace exposes its full pipeline over MCP with budgets and provenance |
| Document search (search engines) | keyword/lexical matching | embeddings, LLM-readiness, budgets | hybrid retrieval tuned and benchmarked for LLM consumption |

The one-sentence version: **Mind Palace is not a database and not an agent framework. It is a focused corpus-memory layer that turns arbitrary knowledge into durable, attributable, model-ready context through simple APIs and standard AI integration.**

This claim is validated by the repository itself: the retrieval core is benchmarked (`eval/EVALUATION.md`), the context contract is tested (`tests/test_api_contract.py`), and the quick start runs without cloud services.

---

## Security posture

> **Corpus content is data, not instructions.**

Retrieved text must never be treated as trusted system instructions by a
consuming application. Mind Palace attributes all evidence so downstream
systems can verify provenance, but prompt-injection defense belongs in the
consuming application's design.

Database credentials come exclusively from `DATABASE_URL`; local defaults
target disposable containers only.

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
- Deletion propagation requires an explicit `sync` call
- Benchmark differences between top strategies remain within statistical noise

## Roadmap

1. Retrieval quality gate in CI (benchmark vs baseline on every PR)
2. Additional source formats (plain text, code, HTML, PDF)
3. Embedding-model abstraction with versioned re-indexing
4. Reranker cost engineering (smaller models, ONNX, caching)

---

## Contributing

Contributions welcome. Please open an issue before large architectural changes.
Retrieval changes must include benchmark evidence.

## License

Apache License 2.0. See [LICENSE](LICENSE) and [NOTICE](NOTICE).
