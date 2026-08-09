# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 Raunak Dey

# mind-palace

**AI-Native Research Operating System** — turns your portfolio content into a searchable, interactive knowledge base powered by local LLMs and vector search.

## Quick Start

```bash
# 1. Clone
git clone https://github.com/raunakdey-07/mind-palace.git
cd mind-palace

# 2. Initialize database (run once)
alembic upgrade head

# 3. Start the stack
docker-compose up -d

# 4. Pull an LLM (inside the Ollama container or host)
docker exec ollama ollama pull llama3.3:3b-instruct

# 5. Ingest your content
docker exec mindpalace-backend-1 mindpalace ingest ./content/

# 6. Open the API docs
# http://localhost:8000/docs
```

## What It Does

- **Ingests** Markdown files (with YAML frontmatter) into Postgres + pgvector
- **Searches** your content semantically via vector similarity with hybrid search and RRF
- **Answers questions** using RAG (retrieval-augmented generation) with a local LLM
- **Agents** for research, resume review, and interview prep
- **Evaluation** suite for retrieval quality
- **Health checks** via `mindpalace doctor`

## Tech Stack

| Layer       | Tool                                    |
|-------------|-----------------------------------------|
| Backend     | FastAPI, SQLAlchemy, Pydantic           |
| Vector DB   | PostgreSQL + pgvector                   |
| Embeddings  | sentence-transformers (`all-MiniLM-L6-v2`) |
| LLM         | Ollama (local, no API costs)            |
| Services    | Modular service boundary (ingestion, retrieval, LLM, etc.) |
| Migrations  | Alembic                                 |
| Frontend    | Next.js + MDX (Phase 3)                 |
| CLI         | typer                                   |
| CI/CD       | GitHub Actions                          |
| Observability | Prometheus + Grafana                  |

## Project Structure

```
api/          - FastAPI backend (routers, models, services)
├── routers/  - API endpoints
├── models/   - Pydantic schemas
├── services/ - Business logic services (ingestion, retrieval, LLM, etc.)
cli/          - Typer-based CLI
content/      - Markdown files (your portfolio content)
migrations/   - Alembic migration scripts
eval/         - Evaluation benchmarks
tests/        - Unit and integration tests
docs/         - Design documents
```

## Phases

1. **Core Indexing & Search** — ingest + semantic search with hybrid/RRF
2. **RAG Q&A** — LLM-powered answers with citations and structured responses
3. **Agents** — research, resume, interview agents
4. **Frontend** — Next.js UI with chat and search
5. **Hardening** — CI/CD, monitoring, K8s deployment

## License

Mind Palace is licensed under the Apache License 2.0.

The project is intentionally released under a permissive license to encourage learning, experimentation, research, and commercial adoption while providing explicit patent rights to users and contributors.

See [LICENSE](LICENSE)