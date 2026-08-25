---
title: "Project: RAG Chatbot for Documentation"
date: 2024-04-15
tags: ["python", "rag", "deep-learning", "fastapi"]
document_type: "project"
status: Completed
git_repo: "https://github.com/raunakdey-07/docs-chatbot"
summary: "Retrieval-augmented chatbot answering questions over technical documentation sets"
---

# RAG Chatbot for Documentation

A focused retrieval-augmented generation chatbot over arbitrary documentation trees — conceptually a single-purpose ancestor of Mind Palace.

## Design

- Ingestion: recursive text splitting with markdown-aware separators
- **Embeddings** via sentence-transformers in a local vector store
- Retrieval: top-k cosine similarity with source-window expansion
- Generation: Ollama with strict context-only prompting
- Streamlit chat UI with expandable source citations

## Evaluation

Built a 50-question golden set with hand-labeled relevant sections; measured retrieval recall before any prompt tuning.
