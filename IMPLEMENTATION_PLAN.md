# Mind Palace — Implementation Plan

> Derived from `mind palace.md` (spec) and `docs/00_Foundation.md`.  
> This plan is actionable, phased, and designed so each phase produces a working, demo-able increment.

---

## Repo State (as of 2026-08-04)

- Remote: `https://github.com/raunakdey-07/mind-palace.git` — connected ✅
- Existing files: `LICENSE`, `README.md`, `mind palace.md`, `docs/00_Foundation.md`, `docs/01_Technical_Decisions.md`
- No code yet — this plan kicks off development.

---

## Phase 1 — Core Indexing & Semantic Search (2–3 weeks)

**Goal:** A working local stack where Markdown content is ingested and searchable via vector similarity.

### Deliverables
- `docker-compose.yml` with Postgres+pgvector + Ollama
- FastAPI backend with `/api/ingest/file`, `/api/search`, `/api/health`
- Markdown parser + chunker + embedding pipeline
- Pydantic models for request/response
- Basic CLI: `mindpalace ingest ./content/`, `mindpalace search "query"`
- Unit tests for parser, chunker, embedding utils

### File Tree (new)
```
/api/
  __init__.py
  main.py              # FastAPI app, lifespan, router includes
  routers/
    ingest.py          # POST /api/ingest/file, POST /api/ingest/repo
    search.py          # GET /api/search
    query.py           # POST /api/query (stub for Phase 2)
  models/
    schemas.py         # Pydantic models (InsightQuery, InsightResponse, etc.)
  services/
    parser.py          # Markdown frontmatter extraction
    chunker.py         # RecursiveCharacterTextSplitter / heading-based
    embedder.py        # Sentence-Transformer wrapper
    db.py              # SQLAlchemy engine, session, table creation
    repository.py      # CRUD for documents + chunks
  config.py            # Settings (DB URL, Ollama URL, model names)
/cli/
  main.py              # Typer-based CLI
/content/              # User's Markdown files (git-owned)
/tests/
  test_parser.py
  test_chunker.py
  test_embedder.py
  conftest.py          # Shared fixtures
/docker-compose.yml
/Dockerfile            # Backend image
/requirements.txt
```

### Key Decisions
- **Vector DB:** PostgreSQL + pgvector (primary, per spec)
- **Embeddings:** `sentence-transformers` (`all-MiniLM-L6-v2`)
- **LLM:** Ollama (stubbed in Phase 1, live in Phase 2)
- **CLI:** `typer` (Python) for `mindpalace` command

### Acceptance Criteria
- `docker-compose up -d` brings up Postgres + Ollama
- `mindpalace ingest ./content/` populates the DB
- `curl http://localhost:8000/api/search?q=...&k=3` returns relevant chunks
- All unit tests pass

---

## Phase 2 — RAG Q&A & LLM Integration (2–4 weeks)

**Goal:** Full RAG pipeline — query retrieves context, calls local LLM, returns cited answer.

### Deliverables
- `/api/query` endpoint with streaming response
- Ollama integration (chat/completions API)
- Prompt template with retrieved context + citations
- Chat UI component (Next.js or simple HTML/JS)
- E2E test: query a known topic, verify answer cites correct doc

### New Files
```
/api/routers/query.py   # Live RAG endpoint
/services/rag.py        # Retrieval + prompt assembly + LLM call
/frontend/              # Next.js app (initial)
  app/page.tsx
  components/SearchBar.tsx
  components/ChatWindow.tsx
```

### Acceptance Criteria
- `POST /api/query` returns answer with `sources` and `snippets`
- Streaming response works in the chat UI
- Answer is grounded in retrieved chunks (no pure hallucination on known facts)

---

## Phase 3 — Agents & Advanced Features (3–5 weeks)

**Goal:** At least two working LangChain agents.

### Deliverables
- Research Agent (web search + knowledge base + math tool)
- Resume/Interview Agent
- Knowledge graph overlay (optional, NetworkX-based)
- `/api/agent/research`, `/api/agent/resume-review` endpoints
- `mindpalace chat` interactive shell

### New Files
```
/agents/
  base.py              # Agent base class
  research.py          # Research agent
  resume.py            # Resume advisor agent
  interview.py         # Interview simulator agent
/tools/
  search_tool.py
  fetch_tool.py
  web_search.py
  math_tool.py
  code_exec.py
```

### Acceptance Criteria
- Research agent can answer "Tell me about my BirdCLEF project" using portfolio content
- Resume agent compares resume against a job description and returns gap analysis

---

## Phase 4 — Frontend & UI Integration (2–3 weeks)

**Goal:** Seamless "Mind Palace" UI embedded in the existing portfolio.

### Deliverables
- Next.js app with search page, chat page, knowledge graph explorer
- MDX support for rendering docs
- OAuth (GitHub) for auth; JWT for API
- Search bar + chat widget in portfolio layout

### New Files
```
/frontend/
  package.json
  next.config.js
  app/
    layout.tsx
    page.tsx
    search/page.tsx
    chat/page.tsx
    api/[...path]/route.ts   # Next.js API routes proxying FastAPI
```

### Acceptance Criteria
- Portfolio site at `localhost:3000` has Mind Palace search/chat
- Auth flow works (GitHub OAuth → JWT → API calls)

---

## Phase 5 — Optimization, CI/CD & Hardening (Ongoing)

**Goal:** Production-ready, monitored, documented.

### Deliverables
- GitHub Actions CI/CD (lint, test, build, deploy)
- Prometheus + Grafana dashboards
- OpenTelemetry tracing (Jaeger)
- Structured logging (structlog)
- Helm charts for K8s deployment
- README with setup instructions
- Performance tuning (quantization, IVF index, caching)

### New Files
```
/.github/workflows/ci.yml
/infra/
  helm/
    values.yaml
    deployment.yaml
/monitoring/
  prometheus.yml
  grafana-dashboard.json
```

### Acceptance Criteria
- CI runs on every PR; main branch deploys to staging
- Grafana dashboard shows P@5, latency, QPS
- `docker-compose up -d` works for local dev
- README has 1-command setup instructions

---

## Immediate Next Steps (This Week)

1. **Create project scaffolding** — directory structure, `requirements.txt`, `docker-compose.yml`
2. **Set up the FastAPI skeleton** — `/api/main.py`, `/api/models/schemas.py`, `/api/services/db.py`
3. **Write the Markdown parser** — `parser.py` using `python-frontmatter`
4. **Write the chunker** — `chunker.py` using LangChain's `RecursiveCharacterTextSplitter`
5. **Write the embedder** — `embedder.py` using `sentence-transformers`
6. **Add sample content** — 2–3 Markdown files in `/content/` with frontmatter
7. **Write unit tests** — parser, chunker, embedder tests
8. **Create the CLI** — `typer`-based `mindpalace` command
9. **Commit & push** — first working commit to GitHub

---

## Tech Stack Summary

| Layer       | Tool                                    |
|-------------|-----------------------------------------|
| Backend     | FastAPI, SQLAlchemy, Pydantic           |
| Vector DB   | PostgreSQL + pgvector                   |
| Embeddings  | sentence-transformers (`all-MiniLM-L6-v2`) |
| LLM         | Ollama (local)                          |
| Agents      | LangChain / LangGraph                   |
| Frontend    | Next.js + MDX                           |
| CLI         | typer                                   |
| CI/CD       | GitHub Actions                          |
| Deploy      | Docker Compose → K8s (Helm)             |
| Observability | Prometheus + Grafana + OpenTelemetry  |
| Auth        | OAuth2 (GitHub) + JWT                   |

---

*This plan follows the phased roadmap in the spec (Phase 1–5). Each phase is independently demo-able and testable.*
