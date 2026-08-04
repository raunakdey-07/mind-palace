# Mind Palace: AI-Research OS (Specification)

**Executive Summary:** *Mind Palace* is an AI-native research operating system that augments your portfolio into a searchable, live “second brain.” It ingests all your Markdown notes, project docs, Kaggle write-ups, code, and other content into a unified backend. A local LLM (via Ollama) plus vector-store retrieval (Postgres+pgvector, Qdrant/Chroma fallback) provides semantic search, Q&A, and AI agents over your own data. This spec details a **production-ready, local-first architecture** covering components, data models, APIs, CI/CD, deployment (Docker & K8s), scaling, observability, security, and development workflows. We prioritize open-source tools: **PostgreSQL+pgvector** primary vector DB (with Qdrant/Chroma as optional backends), Ollama-hosted open LLMs (Llama3, Qwen3, Gemma, etc.), and embedding models (Sentence-Transformers and INSTRUCTOR) to ensure no API costs. The result is a fully integrated “Mind Palace” platform embedded in your existing [portfolio](https://raunak-dey.vercel.app/) — effectively turning your published work into an interactive AI assistant and knowledge graph.  

**Key Takeaways:** Mind Palace indexes all portfolio content into Postgres (text, metadata, vectors). A FastAPI backend provides RAG APIs and AI agent endpoints. The UI (Next.js/MDX) serves static pages plus interactive search and chat. Agents leverage LangChain/LangGraph for tasks (project summarization, QA, interview prep). We automate ingestion on git commits, evaluate retrieval (Precision@K, latency, hallucination rate), and deploy with Docker Compose/Kubernetes. Citations are used extensively for technical choices (e.g. local LLMs via Ollama, vector DB trade-offs, chunking best practices, RAG metrics). 

## Architecture Overview

Mind Palace comprises four main layers: **Content Layer (Markdown & assets)**, **Storage Layer (Postgres with pgvector, optional object storage)**, **AI Layer (LLMs, embeddings, agents)**, and **User Interface Layer (frontend & APIs)**. The figure below depicts the high-level design, adapted from enterprise RAG patterns (creative rendition of the architecture):

```mermaid
flowchart LR
  subgraph Ingestion 
    A[Markdown Files (frontmatter+content)] --> B[Parser/Chunker]
    B --> C[Embedding Service]
    C --> D[Knowledge Store]
  end
  subgraph Knowledge_Store
    D[(PostgreSQL + pgvector)]
    D --> E[Knowledge Graph/Metadata]
    D --> F[Vector Index]
    D --> G[Object Storage (optional)]
  end
  subgraph Retrieval_API
    H[FastAPI RAG Endpoint] -->|Query->| D
  end
  subgraph AI_Layer
    H --> I[LLM Server (Ollama, local LLaMA/Qwen/Gemma)]
    I -->|Context & answer| H
  end
  subgraph Frontend
    J[Next.js Portfolio] --> K[Search UI & Chat UI]
    K --> H
  end
  D --indexed by--> L[Vector DB (pgvector/Qdrant/Chroma)]
```

**Figure:** Mind Palace high-level architecture: Markdown content is ingested into a vector-enriched Postgres knowledge store (with optional object storage for large assets). A FastAPI backend provides query and agent APIs. Ollama serves local LLMs for generation. The Next.js frontend adds interactive search/chat capabilities on top of your existing portfolio. 

### Components

- **Content Layer:** Your portfolio’s Markdown files (blogs, project docs, Kaggle writeups, notes, resume, etc.) live in a Git repo (`/content` folder). Each file uses YAML frontmatter (e.g. `title`, `date`, `tags`, `difficulty`, `github`, etc.) to capture metadata (see *Data Model* below). This is the single source of truth (SSOT) for all knowledge.
- **Parser/Chunker:** A Python service scans new/changed Markdown files, extracts frontmatter and content, and splits long text into coherent “chunks” (e.g. by headers or fixed token size). Good chunking ensures each piece makes sense standalone.
- **Embedding Service:** Each chunk is passed to an embedding model. We recommend open-source Sentence-Transformer models (e.g. `all-MiniLM-L6-v2`) for general text, and optionally INSTRUCTOR models (e.g. `hkunlp/instructor-base`) for domain-adapted embeddings. Embeddings (float vectors) are stored in the knowledge store.
- **Knowledge Store (Postgres + pgvector):** This is the central database. It holds raw text/chunks, metadata, and vector embeddings. Postgres+pgvector is ACID, supports SQL joins and metadata filtering, and allows horizontal and vertical scaling on larger instances. All content (files, previews, versioning) is linked here for traceability. Optionally, large binaries or assets (images, PDFs) can reside in an object store (e.g. local filesystem or S3-compatible) and be referenced.
- **Vector Index:** pgvector (and pgvectorscale) build ANN indexes (HNSW, IVF, etc.) for fast nearest-neighbor search. For higher scale or performance, we can offload vectors to Qdrant or Chroma as an external vector store (see *Tech Choices*).
- **Retrieval Layer (FastAPI):** A stateless REST API written in FastAPI. Endpoints include: `/ingest` to push new chunks (used by workers/CI), `/search` for semantic queries (returns top-𝑘 chunks with scores), `/query` for full RAG Q&A, plus specialized endpoints (e.g. `/questions`, `/agent-callback`). This layer enforces auth and rate limiting, and ties together vector retrieval with prompt generation.
- **AI/Agents Layer:** Ollama hosts local LLMs (e.g. Llama-3.3B, Qwen-8B, Gemma-7B, etc.) with quantization for efficiency. We’ll run an Ollama daemon accessible via API. On query, the FastAPI backend calls Ollama (or another model) with the retrieved context. For multi-step agents (research assistant, code reviewer, interview coach, etc.), we use LangChain/LangGraph to chain tool calls, memory, and planning. Agents have limited access to the knowledge store via the FastAPI (read-only).
- **Frontend (Next.js + MDX):** Your existing portfolio becomes the user interface. We add pages/components for “Mind Palace”: a search bar, chat window, and Knowledge Graph explorer. We can reuse MDX to render docs, and embed the Mind Palace UI seamlessly. Users can browse content normally, or invoke the AI assistant. The frontend also consumes some APIs for live search/autocomplete.

### Technology Stack

We prioritize open-source, free tools that run locally:

- **Programming Language:** Python (FastAPI, LangChain, Pandas, SQLAlchemy). Type hints and Pydantic for models.
- **Web Framework:** Next.js (React) for frontend; FastAPI for backend.
- **Vector DB:** Primary: **PostgreSQL + pgvector + pgvectorscale**. Fallback/alternative: **Qdrant** or **Chroma DB**. (We choose based on scale – see *Tech Trade-offs* below).
- **Embeddings:** **Sentence-Transformers** (e.g. `all-MiniLM-L6-v2`) and **INSTRUCTOR** models for domain/task tuning.
- **LLM Models:** **Ollama** for local serving (supports Llama3, Qwen3, Gemma3, Mixtral, etc.). Models quantized to 4-bit (Q4_K_M) for ~8-10GB VRAM on consumer GPU. All operations offline, no API key.
- **Agents/Chains:** LangChain 0.0x or LangGraph for RAG pipelines, agent orchestration, and custom tools.
- **Search & Index:** pgvector for SQL+vector queries; optionally **Qdrant** (Rust, high throughput) or **Chroma** (lightweight, Pythonic) for specialized use.
- **Storage:** SQLite (for prototyping); **Postgres** cluster for production (with pgvector). Possibly Redis for caching or Celery broker.
- **CI/CD:** GitHub Actions for tests, lint, container build. Vercel (free tier) for Next.js frontend; Docker Hub/GitHub Container Registry for backend images.
- **Deployment:** Docker Compose for local dev; Kubernetes (Helm charts) for scaling on cloud/on-prem.
- **Observability:** Prometheus + Grafana (metrics), OpenTelemetry/Jaeger (tracing), ELK or Loki for logs.
- **Security/Auth:** OAuth (GitHub/Twitter login) for UI; JWT or API keys for backend. HTTPS everywhere (TLS termination at ingress or ALB).
  
## Technology Trade-offs

| Component         | Option A (Primary)                                         | Option B (Fallback)                             | Trade-offs / Notes                                                      |
|-------------------|------------------------------------------------------------|-------------------------------------------------|------------------------------------------------------------------------|
| **Vector Store**  | PostgreSQL + pgvector (with pgvectorscale)     | Qdrant (standalone) | *Postgres:* Fully ACID, SQL joins, one system for vectors+metadata, high throughput (471 QPS at 99% recall). Scales vertically (single instance). *Qdrant:* Specialized vector DB, sub-ms latency, horizontal sharding. Better at large scales, supports filtering and hybrid search. Requires separate service. *Chroma (not shown):* Easy pip install, Apache2, great for dev, limited scaling (vertical, no true sharding). |
| **LLMs (Text)**   | Local (Ollama + Llama/Gemma/Qwen)              | Cloud (OpenAI/GPT)                               | *Local-first:* No API cost, full data privacy, control over quantization/models. Models can run on consumer GPU (e.g., Llama3-7B Q4 uses ~4GB VRAM). *Cloud:* More compute, better models (GPT-4o etc.), but has cost and privacy trade-offs. For spec, we use local. |
| **Embeddings**    | Sentence-Transformers (frozen)             | Instructor-tuned (frozen)        | *SentenceT:* Widely used, fast, general-purpose. *Instructor:* Instruction-finetuned on many tasks, yields more relevant vectors for specific tasks. Using both covers general vs task-adapted retrieval. |
| **Search Index**  | Combined SQL+pgvector (Postgres)           | External vector DB (Qdrant/Chroma)               | *pgvector:* Query with SQL, strong ecosystem, but single-node. *Qdrant/Chroma:* High-performance ANN search engines, can handle larger corpora and filtering. Consider starting with pgvector, switching to Qdrant if scale demands. |
| **Agents**        | LangChain/LangGraph (open-source)                          | Proprietary (Azure AI, etc.)                     | *LangChain:* Extensible Python framework for agents and RAG. Support for function-calling, toolkits. Self-hosted. *Proprietary:* e.g. Azure AI Agents, ChatGPT Plugins – not required here. |
| **DB vs Graph**   | Relational (Postgres) + vector embeddings                  | Knowledge Graph (Neo4j, RDF)                     | *Relational+vector:* Quick to implement, no upfront schema. *Graph:* Explicit entities/relationships (e.g. linking Projects–Tags, Kaggle–Topics). Graph excels at multi-hop queries but adds modeling overhead. We may add a small graph overlay for entity linking later; primary storage is still Postgres. |
| **Deployment**    | Docker Compose / Kubernetes (self-hosted)                  | Cloud services (Heroku, AWS ECS, etc.)          | *Self-hosted:* Low cost, full control, fits local-first ethos. *Cloud:* easier managed DBs/AI, but costs and API reliance. We default to local stack. |

## Data Model & Schemas

We keep a single Postgres database with tables for documents, chunks, embeddings, metadata, and (optionally) a simple knowledge graph.

- **documents:**  Stores each source file or logical document (e.g. a Kaggle competition page).  
  - *Schema:* `(id UUID PK, title TEXT, path TEXT, type TEXT, date DATE, summary TEXT, ... )`  
  - *Frontmatter fields:* We parse YAML frontmatter into columns (title, date, tags ARRAY, models ARRAY, repos, etc.).  

- **chunks:**  Stores each text chunk of a document.  
  - *Schema:* `(id UUID PK, doc_id FK->documents.id, order INT, text TEXT, embedding vector)`.  
  - We chunk by headings or fixed size (e.g. 512 tokens). Each chunk keeps its origin (doc_id, section header).

- **vectors:** (or integrated in chunks) We can embed the vector in `chunks.embedding` via pgvector type. pgvectorscale builds the index.  
  - *Index:* Using `CREATE INDEX ON chunks USING ivfflat (embedding vector_l2_ops);` or HNSW with pgvectorscale.  
  - Alternatively, use a table `embeddings (chunk_id, vector)`.

- **metadata:**  Depending on needs, additional tables for things like **entities** or **tags**. E.g. `tags(tag TEXT, doc_id)`, `authors(author TEXT, doc_id)`, or a graph of `concepts`.

- **knowledge_graph (optional):**  A simple triple store for linking concepts. E.g. `edges(source_id, target_id, relation)` where source/target could be doc, tag, or entity. Could be stored as adjacency table. This overlays pg.

- **User/Session:** For auth, a simple table of users and tokens (if self-hosted login).

All tables include `timestamps` and versioning fields to support incremental re-indexing. For example, `documents` has `hash` of content and `last_indexed TIMESTAMP`, so we reprocess only changed files. The **deterministic ingestion** principle ensures every update is tracked by version or hash.

Frontmatter YAML is parsed with a library (e.g. PyYAML or `frontmatter`). Example frontmatter template for a Kaggle project:

```yaml
---
title: "BirdCLEF 2023: Audio Classification"
date: 2023-07-15
tags: ["kaggle", "audio-classification", "ensemble"]
competition: "BirdCLEF 2023"
status: Completed
git_repo: "https://github.com/raunak-xyz/birdclef"
notebook: "birdclef_analysis.ipynb"
summary: "Audio classification using CNN + transformers"
---
```

Each of these fields becomes a column (tags as text array, etc.) in `documents`, and is fully searchable via SQL or the vector index (for text fields like summary).

## API Design (FastAPI)

We define REST endpoints for ingestion, search, and agent interactions. Example endpoints:

- **POST `/api/ingest/file`** – Ingest a Markdown file (body: multipart/form-data or JSON with content). Parses frontmatter, chunks text, generates embeddings, and upserts into DB. Returns success or list of created chunk IDs. (Used by CLI/CI on git push.)

- **POST `/api/ingest/repo`** – Scan a local repo path or GitHub link to batch ingest all Markdown/notes. Useful for initial load.

- **GET `/api/search`** – Semantic search. Query param `q=<text>`, `k=<int>`. Returns top-k chunks with context: `{ text, source_title, source_id, score }`. (Implements vector `knn` query + optional metadata filter.)

- **POST `/api/query`** – RAG Q&A. Request JSON `{ "question": "...", "k": 5 }`. Backend retrieves top-k context, then calls LLM to generate an answer (with citations/links to source docs). Returns `{ answer: "...", sources: [doc_ids], chunks: [text...] }`.  

- **POST `/api/agent/research`** – Example agent: takes `{"topic": "BirdCLEF"}`, and runs a multi-step research chain (tool calls for web search, docs). Returns structured research report.

- **POST `/api/agent/resume-review`** – Takes a resume (or uses stored resume), analyzes missing skills vs job descriptions, returns suggestions.

- **GET `/api/graph/related`** – Given an entity or doc_id, return related nodes (via the knowledge graph edges).  

- **GET `/api/metrics`** – (Internal) Returns JSON of system stats (API hits, QPS, avg latency, top queries). For dashboards.

- **Auth:** Endpoints require an `Authorization: Bearer <token>`. Use OAuth 2.0 (GitHub) for web login; issue JWTs for API clients.

Each endpoint’s request/response uses Pydantic models for validation. For example:

```yaml
# /api/query request model (OpenAPI schema)
InsightQuery:
  type: object
  properties:
    question:
      type: string
    k:
      type: integer
  required: [question]
```

```yaml
# /api/query response model
InsightResponse:
  type: object
  properties:
    answer:
      type: string
    sources:
      type: array
      items: { type: string }  # doc IDs or URLs
    snippets:
      type: array
      items: { type: string }
```

Endpoints are documented via OpenAPI (auto-generated by FastAPI). We’ll enforce rate limits and CORS policies as needed.

## CI/CD & GitHub Actions

We use **GitHub Actions** for continuous integration and deployment:

1. **On Pull Request**: 
   - Checkout code.
   - Run `pip install -r requirements.txt`.
   - Lint (flake8, mypy), format checks (black).
   - Run unit tests (`pytest`).
   - Build Docker images (backend, ingest workers).
   - Optionally run integration tests (docker-compose startup, a test query).

2. **On Push to `main`**:
   - Repeat above tests.
   - If all pass, deploy:
     - **Frontend:** Push to Vercel (via GitHub integration).
     - **Backend:** Push Docker image to container registry; trigger deployment (e.g. GitHub Actions step to `docker push`, then `kubectl apply` on K8s cluster).
   - **Content Ingestion:** We can either ingest at runtime or pre-index:
     - Option A: In GitHub Actions, after tests, run a script that ingests all new markdown (if manageable volume) into the staging DB/container before publishing.
     - Option B: Rely on the system itself to auto-index on access (via on-the-fly parsing or a small background job).

3. **Infrastructure as Code:** Use GitHub Secrets and Actions to manage creds. Terraform or Helm charts (in `./infra/`) could provision Postgres (with pgvector) and optional object storage in a Kubernetes cluster if on the cloud.

Example **Docker Compose** snippet (from a proven setup):

```yaml
version: "3.8"
services:
  postgresql:
    image: ankane/pgvector
    environment:
      POSTGRES_DB: mindpalace
      POSTGRES_USER: mpadmin
      POSTGRES_PASSWORD: secret
    volumes:
      - pgdata:/var/lib/postgresql/data
    ports: ["5432:5432"]

  ollama:
    image: ollama/ollama
    container_name: ollama
    volumes: ["ollama_data:/root/.ollama"]
    ports: ["11434:11434"]

  backend:
    build: .
    command: uvicorn main:app --host 0.0.0.0 --port 8000
    volumes: ["./data:/app/data"]
    ports: ["8000:8000"]
    depends_on:
      - postgresql
      - ollama

volumes:
  pgdata:
  ollama_data:
```

Running `docker-compose up -d` will launch a Postgres+pgvector, Ollama, and our FastAPI backend all local (see Revold blog for context). 

For production, we’ll containerize similarly and run on Kubernetes with deployments and services:

- **K8s Deployment (Backend):** A Deployment with N replicas, env vars (DB URL, etc.), using `app/backend/Dockerfile`.
- **K8s StatefulSet (Postgres):** With persistent volume claim, init script to enable `pgvector`.
- **K8s Deployment (Ollama):** If using Ollama CLI (Linux) in a container, or else use an official image; volumes for models.
- **Ingress:** Nginx/Traefik for routing (`/api` to backend, `/` to frontend).
- **Secrets:** Store DB password, etc.

## Scaling & Performance

- **Postgres:** Scale up by increasing machine size (RAM, CPU) for pgvectorscale heavy loads. Postgres can also run read replicas. For very large vector sets (>50M vectors), consider offloading to Qdrant cluster.
- **Qdrant/Chroma:** If using Qdrant, spin up a cluster with sharding (horizontal scale). Qdrant offers GPU support for very high QPS. Chroma can be single-node or a LightDB cluster (in preview).
- **Backend:** Run FastAPI behind Uvicorn/Gunicorn with multiple worker processes. Use a load balancer (K8s service) and auto-scale based on CPU or latency.
- **Caching:** Use Redis to cache frequent queries (e.g. search results for common queries) and session data.
- **Batching:** Embed new docs in batch jobs (Celery workers) so ingestion keeps up. If many files, chunk processing across workers.
- **Failure Recovery:** All writes to DB should use transactions. On restart, the ingestion pipeline should resume from last state (using file hashes or Git SHAs).
- **Search Tuning:** Monitor query latencies. If slow, try smaller chunk sizes, or enable `quantize_enabled` (IVF) in pgvector. Possibly use hybrid search (BM25+ANN).
- **Resource Monitoring:** Keep an eye on GPU memory for models (quantize to Q4_K_M to fit 13B in ~8GB VRAM).
  
## Content Ingestion Pipeline

1. **File Watch/CI Trigger:** When a Markdown file in `content/` is added/updated (detected via Git or file watcher), the ingestion job runs.
2. **Parsing:** Use a Markdown frontmatter parser (e.g. `python-frontmatter`) to extract metadata. The remaining Markdown is converted to plain text (remove code fences if needed).
3. **Chunking:** Use a text splitter (LangChain’s `RecursiveCharacterTextSplitter` or our own) to break by headings or fixed token lengths. For Markdown specifically, splitting on headers (`#`, `##`) yields semantically coherent chunks. We ensure no chunk exceeds model context (e.g. 1024 tokens).
4. **Embeddings:** Each chunk’s text is sent through the embedding model (Sentence-Transformer or INSTRUCTOR). We perform batched embedding (dozens at once) for efficiency. Embeddings are normalized (cosine).
5. **Index/Store:** Insert or update chunks in Postgres: 
   - If doc exists, delete old chunks for that doc (or mark outdated) and replace. 
   - Each chunk record gets its `embedding` (vector), text, order, and a pointer to the parent document. We also store chunk-level metadata if needed.
6. **Cross-Linking:** Optionally, run NER or heuristic to extract entities (technologies, topics) and update a simple knowledge graph: e.g., link `tech`→`doc` or `doc`→`doc` if multiple references. This enables “Find related projects by tag” via graph queries.
7. **Indexing:** pgvector automatically indexes the new vectors (HNSW). For fallback vector DBs (Qdrant), we’d bulk insert these vectors via its API.
8. **Consistency Checks:** After ingestion, calculate a hash of the final vector set. If running on k8s, we might reload the RAG API’s memory or flag to use new vectors. All docs get a version number or last indexed time for incremental updates.

Throughout ingestion, we log to the observability system (e.g. Prometheus metrics: `ingest_jobs_total`, `chunks_indexed_total`, `avg_chunk_size_bytes`).

## Agents & Tools

Mind Palace will include specialized AI agents built atop the RAG foundation:

- **Research Agent:** Given a query (e.g. a problem, paper title, or competition name), this agent can chain multiple tools: web search, PDF retrieval (if allowed), arithmetic calculations, and knowledge base retrieval. It uses a planning prompt to decide whether to, say, call a stock API or do a math calculation. We’ll implement it with LangChain’s `AgentExecutor` and tool wrappers. It can format answers as reports with bullet points and citations.

- **Documentation Agent:** Reads a code repository or notebook (via a tool), summarizes each function/class in Markdown, and writes a formatted doc. For example, running `POST /api/agent/doc-review` with a GitHub URL could trigger this pipeline.

- **Resume Agent:** Compares your resume content (embedded) against a job description (entered by user). It uses embeddings to find missing skills or generate interview questions. It might call `wiki_tool("define X")` to explain terms, or `search_tool("common interview Qs for ML role")` to prepare Q&A.

- **Interview Agent:** Simulates an interview about one of your projects. It asks you questions (multi-turn chat) and evaluates your answers. It uses the knowledge base as the ground truth. We treat it as a chat chain with a custom system prompt.

- **Portfolio Agent:** A maintenance agent that reviews your portfolio. It could run on a schedule, analyzing backlinks, outdated projects, or generating new content recommendations (e.g. “You wrote about BirdCLEF last year, maybe update with new model results?”). It might generate blog drafts from recent chat logs.

**Agent Implementation:** Each agent is a LangChain Chain or Agent with a **prompt template**, one or more tools, and output parser. Tools include:
```python
def search_tool(query: str) -> str: ...     # wraps semantic search API
def fetch_tool(doc_id: str) -> str: ...      # returns full text of a doc
def web_search(query: str) -> str: ...      # optional internet search
def math_tool(question: str) -> str: ...    # for arithmetic (e.g. Wolfram plugin)
def code_exec(code: str) -> str: ...        # sandboxed Python execution
```
We register these with LangChain’s `initialize_agent`. The retrieval RAG function itself is a “tool” for QA.

**Knowledge Graph:** Optionally, we incorporate a graph database (e.g. Neo4j or an embedded library like NetworkX for small graph) to store entities (e.g. Kaggle competition names, ML concepts). Agents can query the graph (via Cypher/SQL) for navigation (e.g. “What models did I use in related competitions?”).

## Evaluation and Metrics

We must monitor both system performance and AI quality:

- **Retrieval Metrics:** Compute Precision@K and Recall@K on sample queries using held-out “ground truth” data (e.g. known relevant docs). Precision@K measures how many of the top-K retrieved chunks were actually relevant. We log P@1, P@5, P@10 over time for drift detection. We also track **Mean Reciprocal Rank (MRR)** and **NDCG** on graded relevance (if available).

- **Latency:** Instrument all API calls for timing (FastAPI Prometheus plugin). Track avg/p95/p99 latency for search (vector query) and LLM generation. Monitor “AI response time” and set alerts if it rises above a threshold. Also track throughput (QPS) on endpoints. 

- **Hallucination Rate:** We estimate hallucination by sampling answers: e.g. automatically verify answer facts against the retrieved chunks (LLM-based consistency check). Any unsupported claim counts as a hallucination. We can use a secondary model (e.g. an instruction-tuned GPT-4o or a verification agent) to flag probable hallucinations. Dashboard shows "% answers with detected hallucination."

- **Cost/Resource Usage:** Even local, monitor GPU memory usage, CPU/RAM by database, etc. Use Grafana to chart server load, model GPU usage (if accessible via NVIDIA DCGM exporter), vector DB memory, etc.

- **User Analytics:** Since this is personal, we may not need heavy analytics. But for improvement, optionally record search queries (anonymized) and clicks to measure coverage and satisfaction.

All metrics feed into a Grafana dashboard. For example, a dashboard panel could plot *P@5* over time, with a SLA line, and *avg query latency* below it.

## Observability & Logging

- **Tracing:** Use OpenTelemetry (OTel) to trace requests across services. For example, trace from HTTP request → DB query → LLM call → response. Send to Jaeger.

- **Logging:** Structured JSON logs (e.g. via python `structlog`) with fields: timestamp, component, level, message, request_id. Log all important events (ingest jobs, agent runs, errors). Sensitive data (e.g. contents of docs) should not be logged in full (use redaction).

- **Monitoring:** Prometheus exporters for:
  - FastAPI (via `PrometheusFastAPIInstrumentator`)
  - PostgreSQL (via `postgres_exporter`)
  - Grafana for dashboards.
  - Possibly a custom exporter for Ollama (no official one, but we can wrap LLM calls).
  - Export metrics like `chunks_indexed_total`, `queries_total`, `llm_tokens_total`, `inference_duration`.

- **Health Checks:** Expose `/health` endpoint for Kubernetes readiness. Check DB connectivity, vector index status, etc.

## Security Considerations

- **Data Privacy:** All data is your data. No third-party APIs means no leak of personal code/content. Nonetheless, secure local network (SSH or VPN) and proper firewall.
- **Authentication/Authorization:** Protect APIs with OAuth2 (e.g. GitHub login for web UI, then JWT for API). Only allow trusted users (likely just you) to query the system. Possibly IP whitelist if local.
- **Input Sanitization:** Never eval arbitrary code from users. The only code execution is in a controlled `code_tool` sandbox with restricted libraries (no filesystem or network).
- **Network Security:** If deploying remotely, use HTTPS/TLS. Use a reverse proxy (Ingress) with certs. Database and vector store should not be exposed publicly – only the backend can reach them.
- **SQL Safety:** Use parameterized queries or ORM to avoid injection.
- **Resource Limits:** In Kubernetes, set CPU/memory limits on pods. Prevent a runaway query from exhausting DB or GPU. For LLMs, limit max tokens.
- **Attack Surface:** The FastAPI should not allow open upload of arbitrary files without size limits (limit Markdown size). Audit all dependencies regularly for vulnerabilities.
- **Auditing:** The knowledge store can record every change (document versions, user actions). This aligns with the “auditable, controlled knowledge base” principle.
  
## Developer Workflow

- **Content as Code:** You edit Markdown in your local repo (`content/`). Frontmatter is the only metadata needed. The documentation agents (Markdown parser) can even generate frontmatter boilerplate.
- **Git-Based Ingestion:** Add a new file or update one, commit, and push to GitHub. A GitHub Action triggers the ingestion pipeline to parse and index that content.
- **CLI Tool:** Provide a `mindpalace` CLI script. Examples:
  - `mindpalace ingest ./content/` – Manually index all files (useful for offline/demo).
  - `mindpalace search "term"` – Quick terminal search using the API.
  - `mindpalace chat` – Starts an interactive shell with the AI assistant.
  - `mindpalace serve` – Launches local server (backend + web UI) for testing.
- **Auto-generation Hooks:** On successful ingest, the system could auto-generate certain content:
  - E.g. auto-create or update a JSON/CSV index of all tags and projects for the frontend to render tag clouds.
  - Automatically generate "Related Links" section for each doc using the knowledge graph.
- **Local Dev Setup:** Simply run `git clone`, then `docker-compose up -d`. All components come up. Then open `http://localhost` to see UI and use the API.
- **Project Structure:** 
  - `/api/` – FastAPI backend code, models, routers.
  - `/frontend/` – Next.js code (MDX pages + custom pages for Mind Palace).
  - `/embedding/` – Embedding service code (if separate from backend).
  - `/workers/` – Celery or Python scripts for ingestion.
  - `/content/` – Markdown files (Git-owned).
  - `/charts/` – Kubernetes manifests (Helm).
  - `/docker-compose.yml` – local setup file.
  - `/README.md` – Usage instructions (see below).
  
Developers (you) primarily interact via Git and CLI; the heavy lifting (embedding, indexing) is automated. The dependency injection pattern (FastAPI) ensures components (vector store, LLM) are injected cleanly, making it easy to test and swap parts.

## Roadmap & Milestones

A phased rollout is recommended:

1. **Phase 1 (2–3 weeks): Core Indexing & Search**
   - Set up Postgres+pgvector locally.
   - Implement Markdown parser and ingestion (frontmatter → DB).
   - Create FastAPI `/search` (without LLM). Use a simple UI to query. 
   - Use local embedding (Sentence-Transformer).
   - **Milestone:** Basic semantic search over your portfolio content.

2. **Phase 2 (2–4 weeks): RAG Q&A & LLM Integration**
   - Integrate Ollama (install, test basic ChatUI).
   - Develop `/query` endpoint that retrieves context (top-k) and calls local LLM for answer.
   - Ensure streaming responses (async FastAPI) for a chat UI.
   - **Milestone:** Chatbot that answers questions using your indexed portfolio (e.g. “Tell me about my Finalysis project.”).

3. **Phase 3 (3–5 weeks): Agents & Advanced Features**
   - Build specialized agents (e.g. research, resume, interview). Define tools and LangChain flows.
   - Add knowledge graph features (entity extraction from docs, simple graph search).
   - Add additional content types (e.g. PDF imports, code snippets).
   - **Milestone:** At least two working agents (e.g. Resume Advisor and Research Assistant).

4. **Phase 4 (2–3 weeks): Frontend & UI Integration**
   - Enhance Next.js site: add search page, AI chat widget, UI for agent tools.
   - Use MDX to embed answers or generated content.
   - Integrate user auth (OAuth).
   - **Milestone:** Public-facing portfolio with “Ask Raunak AI” features seamlessly.

5. **Phase 5 (Ongoing): Optimization & Hardening**
   - Performance tuning (db indexes, model quantization).
   - Implement CI/CD pipelines fully (as above).
   - Add monitoring dashboards.
   - Write documentation (README, usage guides).
   - **Milestone:** Stable, documented release ready for interviews/jobs.

Each phase includes testing at both unit and integration level. Early phases prioritize functionality; later phases focus on polish and reliability.

## Tables and Example Configurations

**Table: Vector Store Comparison**

| Feature                      | PostgreSQL + pgvector | Qdrant       | Chroma DB         |
|------------------------------|---------------------------------------------------|------------------------------------------|----------------------------------|
| *Deployment*                 | Built-in to Postgres, no extra service            | Standalone service (Docker/K8s)          | Library or service (pip, Docker) |
| *Scalability*                | Vertical (more CPU/RAM); single-node              | Horizontal sharding (multi-node)         | Vertical (experimental multi)    |
| *Query Language*             | SQL (JOINs, filters + vector ops) | gRPC/REST API with filters               | Python API (embeddings + filters)|
| *Performance (search)*       | High throughput (471 QPS @99% recall), sub-100ms latency | Low latency (sub-10ms at 90% recall) | Moderate (depends on data size)   |
| *Features*                   | ACID, mature ecosystem, built-in analytics        | Filterable HNSW, GPU support, payload indexing | Simple HNSW, good notebook UX    |
| *Persistence/ACID*           | Yes (relational guarantees)                       | Yes (DB-like durability)                 | Yes (with Delta Lake backend)    |
| *License*                    | Open-source (Postgres), Apache (pgvector)         | Open-source (Apache)                     | Open-source (Apache)             |
| *When to use*                | Small/medium data; relational+vector needs; single DB | Large-scale vectors; complex filters; enterprise | Prototyping; local dev; moderate scale |

**Table: LLM & Embedding Model Choices**

| Component    | Primary (Local)                                            | Secondary (Optional)                    | Trade-offs                                                      |
|--------------|------------------------------------------------------------|-----------------------------------------|-----------------------------------------------------------------|
| **LLM Engine** | Ollama (with quantized models)              | Cloud LLM (OpenAI, Azure)               | Ollama: offline, private, free; requires GPU/DRAM. Cloud: more powerful, paid, less control. |
| **Model Family** | Meta LLaMA 3 (3.3B or 7B), Qwen-8B | GPT-4o via API                         | LLaMA/Qwen: high performance local, good reasoning. GPT-4o: SOTA but black box.            |
| **Embedding**  | Sentence-BERT (e.g. `all-MiniLM-L6-v2`), INSTRUCTOR | CLIP (for multimodal), OpenAI embeddings | SBERT: well-rounded. INSTRUCTOR: task-aware embeddings (SOTA on 70 tasks). CLIP adds vision. OpenAI: costly API. |

**CI/CD Workflow (GitHub Actions)**

```yaml
name: CI

on: [push, pull_request]

jobs:
  tests:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v3
      - name: Set up Python
        uses: actions/setup-python@v4
        with: python-version: '3.10'
      - name: Install dependencies
        run: pip install -r requirements.txt
      - name: Lint & Test
        run: |
          flake8 src/
          pytest --maxfail=1 --disable-warnings -q
      - name: Build Docker Image
        run: |
          docker build . -t mindpalace:latest
  deploy:
    if: github.ref == 'refs/heads/main'
    needs: tests
    runs-on: ubuntu-latest
    steps:
      - name: Push Docker image
        run: |
          echo "${{ secrets.DOCKER_PASS }}" | docker login -u "${{ secrets.DOCKER_USER }}" --password-stdin
          docker tag mindpalace:latest ${{ secrets.DOCKER_USER }}/mindpalace:latest
          docker push ${{ secrets.DOCKER_USER }}/mindpalace:latest
      # Deploy to K8s or run additional scripts (kubectl apply, Vercel)
```

This CI ensures code quality and builds the Docker image. CD can trigger on pushes to `main` to update the live system.

## Setup & Usage (Local-First)

1. **Prerequisites:** Machine with Docker (and optional GPU). Clone the repo.
2. **Docker Compose:** Run `docker-compose up -d`. Services: Postgres+pgvector, Ollama (LLM server), FastAPI backend, Redis (optional). Verified stacks like Revold’s show this is 10-min easy.
3. **Load Model:** Inside Ollama container or host, run `ollama pull llama3.3:3b-instruct` (for example) to download a model. The guide indicates how.
4. **Content:** Add `.md` files under `content/`. Each with YAML frontmatter.
5. **Indexing:** Trigger manual indexing: `mindpalace ingest`. This runs the parser/embedding and populates Postgres.
6. **Run Server:** Backend is already running. Visit `http://localhost:8000/docs` for API docs, or go to the Next.js frontend (e.g. `http://localhost:3000`) to see your portfolio enriched.
7. **Search/Chat:** Use the UI search bar or call APIs. Try queries like “Tell me about my BirdCLEF project.” or enter the chat.

All components run locally; no credit cards required. The Ollama+Postgres stack image demonstrates this setup is practical and private. You can even run entirely offline. 

## Observability and Metrics

- **Prometheus:** FastAPI metrics (requests/sec, latencies) via [PrometheusFastAPIInstrumentator]. Postgres exporter for DB stats. Custom metrics for embed indexing. 
- **Grafana:** Dashboards for API performance and retrieval effectiveness. For example, plot *Precision@5* (computed nightly on a validation set) and *avg query latency*. 
- **Alerting:** Set alerts if error rate >1%, or QPS drops, or hallucination rate (e.g. >10% answers flagged).
- **Logging:** All services log to standard output in JSON. Use filebeat/Loki to centralize logs if needed.
- **Audit Trail:** Every agent session and ingestion job is logged. We could store conversation transcripts (with user consent) for offline review and improvement.

## Testing Plan

- **Unit Tests:** For parser (metadata extraction), chunker, embedding utils, API endpoint functions (mock DB), and any core logic. E.g. test that given a sample Markdown, the frontmatter and chunks parse correctly.
- **Integration Tests:** Bring up the full stack in CI (using Docker Compose). Tests include:
  - Ingest a small set of MD files, then query a known term returns expected docs.
  - API contract tests: e.g. `POST /api/ingest` with sample payload yields `200 OK`.
  - End-to-end: `POST /api/query` on a known question returns answer containing expected keyword from docs.
- **End-to-End (User Stories):** 
  - *Search:* A user enters “GitHub embeddings” and sees relevant Kaggle project snippets.
  - *Agent:* The interview agent asks a question drawn from your portfolio (e.g. “Explain the method used in Finalysis.”) and expects you to answer similarly to your docs.
- **Load Testing:** Simulate many concurrent queries (tools like Locust) to ensure the system scales and does not crash under load (for a personal project, maybe not urgent).
- **Evaluation Tests:** As mentioned, run nightly batch to compute P@K/Recall using a small labeled dataset (e.g. questions from readme or notes marked relevant). Verify no regressions.

Automate these tests in CI; critical ones (lint/tests) on every PR. Longer E2E or evaluation runs on main branch merges.  

## Security & Privacy Audit

- Review all inputs: Markdown content and user queries go into LLM prompts. Sanitize prompts to avoid injection (though GPT-like models are immune to SQL injection, but be cautious if rendering HTML).
- Inspect dependencies for vulnerabilities (dependabot on GitHub).
- Use secrets manager for tokens and never hardcode keys.
- Optionally run OWASP ZAP on the API for common vulnerabilities.
- Keep the machine updated and limit network ports (only 80/443 open, DB port internal).
  
## Conclusion

*Mind Palace* transforms your static portfolio into a dynamic AI-enhanced research assistant. By combining proven RAG architecture principles with local-first tools (Ollama, pgvector), we ensure the system is scalable, cost-free, and fully under your control. The spec above outlines all required components and steps to build, test, and deploy this system. Implementing it will cover all key GenAI skills – RAG pipelines, LangChain agents, LLM deployment, vector databases, system design – making your resume **AI-engineer-ready**. 

**Sources:** Our design draws on best practices in AI/ML engineering: embedding pipelines, vector DB trade-offs, local LLM hosting, and RAG evaluation metrics, all cited above.   

