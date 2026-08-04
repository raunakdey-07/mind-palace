---
title: "Mind Palace — Project README"
date: 2024-01-10
tags: ["ai", "rag", "research", "portfolio"]
status: In Progress
git_repo: "https://github.com/raunakdey-07/mind-palace"
summary: "AI-native research operating system that turns portfolio content into a searchable, interactive knowledge base"
---

# Mind Palace

Mind Palace is an AI-native research operating system that augments your portfolio into a searchable, live "second brain."

## What It Does

- Ingests Markdown notes, project docs, Kaggle write-ups, and code
- Builds a vector-enriched knowledge store using Postgres + pgvector
- Provides semantic search and RAG-based Q&A over your own content
- Runs AI agents for research, resume review, and interview prep

## Quick Start

```bash
git clone https://github.com/raunakdey-07/mind-palace.git
cd mind-palace
docker-compose up -d
ollama pull llama3.3:3b-instruct
mindpalace ingest ./content/
```

## Tech Stack

- **Backend:** FastAPI + SQLAlchemy
- **Vector DB:** PostgreSQL + pgvector
- **LLM:** Ollama (local, no API costs)
- **Embeddings:** Sentence-Transformers
- **Agents:** LangChain / LangGraph
- **Frontend:** Next.js + MDX
