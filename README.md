# Mind Palace

**AI-Native Research Operating System** — turns your portfolio content into a searchable, interactive knowledge base powered by local LLMs and vector search.

## Quick Start

```bash
# 1. Clone
git clone https://github.com/raunakdey-07/mind-palace.git
cd mind-palace

# 2. Start the stack
docker-compose up -d

# 3. Pull an LLM (inside the Ollama container or host)
docker exec ollama ollama pull llama3.3:3b-instruct

# 4. Ingest your content
docker exec mindpalace-backend-1 mindpalace ingest ./content/

# 5. Open the API docs
# http://localhost:8000/docs
```

## What It Does

- **Ingests** Markdown files (with YAML frontmatter) into Postgres + pgvector
- **Searches** your content semantically via vector similarity
- **Answers questions** using RAG (retrieval-augmented generation) with a local LLM
- **Agents** for research, resume review, and interview prep

## Tech Stack

| Layer       | Tool                                    |
|-------------|-----------------------------------------|
| Backend     | FastAPI, SQLAlchemy, Pydantic           |
| Vector DB   | PostgreSQL + pgvector                   |
| Embeddings  | sentence-transformers (`all-MiniLM-L6-v2`) |
| LLM         | Ollama (local, no API costs)            |
| Agents      | LangChain / LangGraph                   |
| Frontend    | Next.js + MDX (Phase 4)                 |
| CLI         | typer                                   |
| CI/CD       | GitHub Actions                          |

## Project Structure

```
api/          - FastAPI backend (routers, models, services)
cli/          - Typer-based CLI
content/      - Markdown files (your portfolio content)
tests/        - Unit and integration tests
docs/         - Design documents
infra/        - Kubernetes Helm charts
```

## Phases

1. **Core Indexing & Search** — ingest + semantic search
2. **RAG Q&A** — LLM-powered answers with citations
3. **Agents** — research, resume, interview agents
4. **Frontend** — Next.js UI with chat and search
5. **Hardening** — CI/CD, monitoring, K8s deployment

## License

See [LICENSE](LICENSE)
