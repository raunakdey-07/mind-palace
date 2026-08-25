# Mind Palace

[![Release](https://img.shields.io/github/v/release/raunakdey-07/mind-palace)](https://github.com/raunakdey-07/mind-palace/releases)
[![License](https://img.shields.io/badge/license-Apache%202.0-blue.svg)](LICENSE)
[![CI](https://github.com/raunakdey-07/mind-palace/actions/workflows/ci.yml/badge.svg)](https://github.com/raunakdey-07/mind-palace/actions)

**AI-Native Research Operating System**

Mind Palace turns your portfolio, projects, Kaggle work, research notes, and technical documents into a **searchable, interactive knowledge base** powered by local LLMs and vector search.

The project is designed as a **local-first AI platform**: no paid API keys are required for the reference setup.

## Why this exists

Most portfolios are static pages. Mind Palace treats a portfolio as a **living knowledge system**: every project, competition, note, and write-up becomes queryable, explainable, and reusable through semantic search and local LLMs.

---

## Current Status

* **v0.3.0 released** — reproducible foundation (database setup, ingestion lifecycle, retrieval evaluation)
* Local-first deployment working
* PostgreSQL + pgvector backend implemented
* Hybrid retrieval + RRF is the default strategy
* Cross-encoder reranking available as explicit opt-in
* Retrieval evaluation framework with measured baselines

---

## Pipeline

Mind Palace processes content through a single well-defined pipeline:

```text
Markdown files (content/)
      │
      ▼
Parsing            YAML frontmatter validation, body extraction
      │
      ▼
Chunking           heading-path-aware sections, size-bounded splits
      │
      ▼
Embedding          sentence-transformers (all-MiniLM-L6-v2, 384-dim, normalized)
      │
      ▼
Storage            PostgreSQL + pgvector (HNSW index), ingestion manifest
      │
      ▼
Retrieval          vector search, hybrid (vector + pg_trgm keyword), RRF fusion,
                   optional cross-encoder reranking
      │
      ▼
Generation         grounded RAG answers with source attribution (Ollama)
      │
      ▼
Evaluation         Recall@k / Precision@k / MRR / latency per strategy
```

### Retrieval strategies

| Strategy | When to use | Default |
|---|---|---|
| `vector` | pure semantic similarity | |
| `hybrid` | semantic + keyword signals, weighted blend | |
| `hybrid + RRF` | rank-fused; best measured quality at negligible cost | **yes** |
| `hybrid + RRF + reranker` | cross-encoder re-scoring of a candidate pool | opt-in (`?rerank=true`) |

**Decision rationale:** benchmark measurements on the current corpus show hybrid+RRF is the only strategy reaching Recall@3 = 1.00 at ~2 ms average latency. Cross-encoder reranking adds roughly two orders of magnitude of latency (~200 ms+) for a marginal MRR change and slightly worse Recall@3 on multi-relevant queries. It therefore remains available but off by default; this decision is revisited as the corpus grows.

### Evaluation

The evaluation framework lives in `eval/` and runs through the CLI:

```bash
mindpalace eval strategies --candidates 10,20,50 --details
```

It executes the same 19-query deterministic dataset against every strategy and reports Recall@3/5/10, Precision@3/5/10, MRR, and latency, plus per-query failure diagnostics (scores, ranks, chunk text).

**Scope caveat:** the current corpus is small (3 documents, 13 chunks). The benchmark is genuinely useful for **regression detection** — it caught real defects during development — but the corpus is far too small to claim broad retrieval superiority or to tune ranking parameters meaningfully. Candidate-pool-size experiments are inconclusive at this scale because every pool contains the entire corpus.

See `eval/EVALUATION.md` for full methodology, configuration, and measured results.

---

## Features

### Implemented

* Markdown ingestion with YAML frontmatter validation
* Deterministic document IDs; path-keyed upserts for changed documents
* Incremental ingestion via manifest tracking with stale-chunk replacement
* Heading-path-aware chunking with size bounds
* PostgreSQL + pgvector storage (HNSW index), self-provisioning migrations
* Semantic vector search
* Hybrid retrieval (vector + pg_trgm keyword) with Reciprocal Rank Fusion
* Optional cross-encoder reranking (explicit opt-in)
* Structured RAG responses with citations
* Pluggable LLM provider interface; local inference via Ollama
* Typer-based CLI (`python -m cli.main` or the `mindpalace` console script)
* Retrieval evaluation framework (Recall@k, Precision@k, MRR, nDCG, latency)
* Ingestion lifecycle and migration contract test suites
* Containerized local development stack
* GitHub Actions CI with database-backed integration tests
* Alembic database migrations

### Planned

* Next.js frontend
* Research / resume / interview agents
* Knowledge graph (under investigation — not yet justified by evidence)
* MCP server
* Advanced observability

---

## Tech Stack

| Layer              | Technology                    |
| ------------------ | ----------------------------- |
| Backend            | FastAPI, SQLAlchemy, Pydantic |
| Database           | PostgreSQL 15+ + pgvector     |
| Embeddings         | sentence-transformers (all-MiniLM-L6-v2) |
| Reranking          | sentence-transformers CrossEncoder (opt-in) |
| Retrieval          | pgvector + pg_trgm + RRF      |
| LLM Runtime        | Ollama                        |
| Migrations         | Alembic                       |
| CLI                | Typer                         |
| CI/CD              | GitHub Actions                |
| Containers         | Docker Compose                |
| Frontend (planned) | Next.js + MDX                 |

---

## Prerequisites

* Python **3.11+**
* PostgreSQL **15+** with the **pgvector server extension**
* Docker / Podman
* Ollama

> **Important:** installing the Python `pgvector` package does **not** install
> the PostgreSQL `vector` extension — that must come from your database image
> or package manager. Use a pgvector-enabled image such as `pgvector/pgvector:pg15`
> (used by CI) or `ankane/pgvector` (docker-compose). The initial Alembic
> migration provisions the `vector`, `pgcrypto`, and `pg_trgm` extensions via
> `CREATE EXTENSION IF NOT EXISTS`, so any fresh database works as long as the
> image ships them.

Install Ollama:

```bash
curl -fsSL https://ollama.com/install.sh | sh
ollama pull qwen3:8b
```
### Fedora / Podman users

Install Podman Compose if it is not already available:

```bash
sudo dnf install podman-compose
```

---

## Quick Start

### 1. Clone the repository

```bash
git clone https://github.com/raunakdey-07/mind-palace.git
cd mind-palace
```

### 2. Create a virtual environment

```bash
python -m venv .venv
source .venv/bin/activate
```

### 3. Install dependencies

```bash
python -m pip install --upgrade pip
python -m pip install -r requirements.txt
python -m pip install -e .
```

### 4. Start infrastructure

**Docker**

```bash
docker compose up -d
```

**Podman (Fedora default)**

```bash
podman-compose up -d
```

### 5. Apply database migrations

```bash
make migrate
# or: python -m alembic -c migrations/alembic.ini upgrade head
```

This creates the schema and provisions the required PostgreSQL extensions
(`vector`, `pgcrypto`, `pg_trgm`) on a fresh database. It is safe to re-run.

### 6. Ingest content

```bash
mindpalace ingest-repo content/
```

### 7. Verify the setup

```bash
mindpalace doctor
pytest -q
```

---

## CLI Usage

The CLI can be invoked either as the installed console script (`mindpalace`) or directly with `python -m cli.main`.

### Ingest content

```bash
mindpalace ingest-repo content/
```

### Semantic search

```bash
mindpalace search "feature leakage"
```

### Ask a question

```bash
mindpalace ask "What did I learn from BirdCLEF?"
```

### Generate interview questions

```bash
mindpalace interview <document-id>
```

### Run retrieval strategy comparison

```bash
mindpalace eval strategies                  # default candidate pool
mindpalace eval strategies --candidates 10,20,50 --details
```

Requires a live PostgreSQL + pgvector database (`DATABASE_URL`) with an ingested corpus; does not require the API server.

### Run simple retrieval check

```bash
mindpalace eval retrieval
```

### Summarize a document

```bash
mindpalace summarize <document-id>
```

### Find related documents

```bash
mindpalace related <document-id>
```

### View document timeline

```bash
mindpalace timeline
```

---

## API Usage

Start the API:

```bash
uvicorn api.main:app --reload
```

### Health check

```bash
curl http://localhost:8000/health
```

### Search (with optional hybrid / RRF / reranking)

```bash
curl "http://localhost:8000/api/search?q=feature+leakage&k=5"
curl "http://localhost:8000/api/search?q=feature+leakage&hybrid=true&rrf=true"
curl "http://localhost:8000/api/search?q=feature+leakage&hybrid=true&rrf=true&rerank=true"
```

### Ask a question

```bash
curl -X POST http://localhost:8000/api/query/ask \
  -H "Content-Type: application/json" \
  -d '{"question": "What is Finalysis?"}'
```

### Summarize a document

```bash
curl -X POST http://localhost:8000/api/query/summarize \
  -H "Content-Type: application/json" \
  -d '{"document_id": "<doc-id>"}'
```

### Find related documents

```bash
curl -X POST http://localhost:8000/api/query/related \
  -H "Content-Type: application/json" \
  -d '{"document_id": "<doc-id>"}'
```

### View timeline

```bash
curl -X GET http://localhost:8000/api/query/timeline
```

---

## Project Structure

```text
api/
├── routers/        # API endpoints (ingest, search, query)
├── models/         # Pydantic schemas
└── services/
    ├── parser.py       # frontmatter + heading-path extraction
    ├── ingestion.py    # parse → chunk → embed → store pipeline
    ├── embedder.py     # sentence-transformers wrapper
    ├── retrieval.py    # vector / hybrid / RRF / reranked search
    ├── reranker.py     # cross-encoder singleton (opt-in)
    ├── evaluation.py   # Recall@k / Precision@k / MRR / nDCG metrics
    ├── benchmark.py    # strategy comparison runner
    └── repository.py   # documents/chunks/manifest data access

cli/                # Typer CLI entry point

content/            # Markdown knowledge base

eval/               # Benchmark dataset + EVALUATION.md report

migrations/         # Alembic migrations (self-provisioning extensions)

tests/              # Unit, lifecycle, migration-contract tests
```

---

## Development Workflow

### Run tests

```bash
pytest -q
```

Tests run without a database (DB-backed integration tests skip automatically).
Set `DATABASE_URL` to a migrated PostgreSQL+pgvector database to run the full
suite including ingestion-lifecycle and migration-contract tests:

```bash
export DATABASE_URL=postgresql://mpadmin:secret@localhost:5432/mindpalace
make migrate
pytest -q
```

### Run linting and formatting

```bash
black --check api cli tests migrations --line-length 100
flake8 api cli tests migrations --max-line-length=100
ruff check .
```

### Run type checking

```bash
mypy api cli
```

### Run evaluation

```bash
mindpalace eval strategies
```

---

## Reproducibility Test

Verify that a fresh clone works end to end:

```bash
git clone https://github.com/raunakdey-07/mind-palace.git /tmp/mp-test
cd /tmp/mp-test

python -m venv .venv
source .venv/bin/activate

python -m pip install -r requirements.txt
python -m pip install -e .

docker compose up -d          # or podman-compose up -d
make migrate                  # schema + extensions on a fresh database
mindpalace ingest-repo content/

pytest -q
mindpalace doctor
mindpalace eval strategies
```

CI performs the same sequence against a fresh `pgvector/pgvector:pg15` service container on every push to `main`.

---

## Contributing

Contributions are welcome. Please open an issue before large architectural changes.

---

## License

Mind Palace is licensed under the **Apache License 2.0**.

See [LICENSE](LICENSE) for the full license text and [NOTICE](NOTICE) for attribution information.

The project is intentionally released under a permissive license to encourage learning, experimentation, research, and commercial adoption while providing explicit patent rights to users and contributors.
