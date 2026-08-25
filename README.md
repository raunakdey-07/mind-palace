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

* **v0.2.1 released**
* **Phase 2 (Retrieval Quality) in progress**
* Local-first deployment working
* PostgreSQL + pgvector backend implemented
* Hybrid retrieval and RRF implemented
* Evaluation harness implemented

---

## Features

### Implemented

* Markdown ingestion with YAML frontmatter
* Deterministic document IDs
* Incremental ingestion via manifest tracking
* PostgreSQL + pgvector storage
* Semantic vector search
* Hybrid retrieval (vector + keyword)
* Reciprocal Rank Fusion (RRF)
* Structured RAG responses with citations
* Pluggable LLM provider interface
* Local inference via Ollama
* Typer-based CLI
* Retrieval evaluation benchmarks
* Containerized local development stack
* GitHub Actions CI
* Alembic database migrations

### In Progress

* Cross-encoder reranking
* Retrieval metrics (Recall@k, MRR, nDCG)
* Latency instrumentation
* Query diagnostics tooling

### Planned

* Next.js frontend
* Research / resume / interview agents
* Knowledge graph
* MCP server
* Advanced observability

---

## Architecture

```text
Markdown / MDX / Repositories
              │
              ▼
       Ingestion Service
              │
              ▼
      Metadata Extraction
              │
              ▼
      Embedding Generation
              │
              ▼
     PostgreSQL + pgvector
              │
              ▼
        Hybrid Retrieval
      (Vector + Keyword)
              │
              ▼
         RRF Ranking
              │
              ▼
        LLM Generation
              │
              ▼
 API / CLI / Portfolio UI
```

---

## Tech Stack

| Layer              | Technology                    |
| ------------------ | ----------------------------- |
| Backend            | FastAPI, SQLAlchemy, Pydantic |
| Database           | PostgreSQL + pgvector         |
| Embeddings         | sentence-transformers         |
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

> **Note:** installing the Python `pgvector` package does **not** install the
> PostgreSQL `vector` extension — that must come from your database image or
> package manager. Use a pgvector-enabled image such as `pgvector/pgvector:pg15`
> (CI) or `ankane/pgvector` (docker-compose). The initial Alembic migration
> provisions `vector`, `pgcrypto`, and `pg_trgm` extensions itself via
> `CREATE EXTENSION IF NOT EXISTS`, so any fresh database works as long as
> the image ships them.

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
(`vector`, `pgcrypto`, `pg_trgm`) on a fresh database.

### 6. Verify the setup

```bash
mindpalace doctor
pytest -q
```

---

## CLI Usage

### Ingest content

```bash
mindpalace ingest content/
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
mindpalace interview birdclef
```

### Run retrieval evaluation

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
├── routers/        # API endpoints
├── models/         # Pydantic schemas
└── services/       # Business logic

cli/                # Typer CLI

content/            # Markdown knowledge base

eval/               # Retrieval benchmarks

migrations/         # Alembic migrations

tests/              # Unit and integration tests
```

---

## Development Workflow

### Run tests

```bash
pytest -q
```

### Run linting

```bash
ruff check .
```

### Run type checking

```bash
mypy api cli
```

### Run evaluation

```bash
mindpalace eval retrieval
```

---

## Reproducibility Test

Verify that a fresh clone works:

```bash
git clone https://github.com/raunakdey-07/mind-palace.git /tmp/mp-test
cd /tmp/mp-test

python -m venv .venv
source .venv/bin/activate

python -m pip install -r requirements.txt
python -m pip install -e .

# Docker
docker compose up -d

# or Podman
podman-compose up -d

pytest -q
mindpalace doctor
mindpalace eval retrieval
```

---

## Contributing

Contributions are welcome. Please open an issue before large architectural changes.

---

## License

Mind Palace is licensed under the **Apache License 2.0**.

See [LICENSE](LICENSE) for the full license text and [NOTICE](NOTICE) for attribution information.

The project is intentionally released under a permissive license to encourage learning, experimentation, research, and commercial adoption while providing explicit patent rights to users and contributors.