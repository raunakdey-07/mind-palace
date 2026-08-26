---
title: "Project: Semantic Search over ArXiv Papers"
date: 2024-03-28
tags: ["python", "embeddings", "search", "nlp"]
document_type: "project"
status: Completed
git_repo: "https://github.com/raunakdey-07/arxiv-search"
summary: "Semantic search engine over arXiv abstracts with sentence embeddings and FAISS"
---

# Semantic Search over arXiv

Research-paper discovery via embedding similarity — the project that preceded and motivated Mind Palace.

## Design

- **sentence-transformers** embeddings of title+abstract
- **FAISS** IVF index for the ~2M paper corpus
- Query expansion with author-assigned keywords
- Recency boost blended with similarity for freshness-sensitive queries

## Findings

Pure similarity surfaced highly-cited older papers regardless of intent; the recency blend was essential. This project is where hybrid retrieval earned its place in my toolkit.
