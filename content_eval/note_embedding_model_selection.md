---
title: "Note: Embedding Model Selection Guide"
date: 2024-04-05
tags: ["embeddings", "ml-fundamentals", "note"]
document_type: "note"
status: Complete
summary: "Choosing sentence embedding models: dimensions, MTEB, latency, and multilinguality"
---

# Embedding Model Selection

Companion to the vector-database note; how to choose the model itself.

## Evaluation

- **MTEB** benchmark for broad task coverage; but always validate on your own retrieval set
- Domain vocabulary matters more than leaderboard rank — test with real queries

## Practical Dimensions

- **384-dim (MiniLM)**: fast, small, strong baseline; Mind Palace's default
- **768-dim (mpnet, BGE)**: better ceiling, ~2× memory and latency
- **1024+ (bge-large, e5-large)**: diminishing returns below millions of vectors
- Multilingual needs: multilingual-e5 or LaBSE regardless of size

## Operational Notes

Changing models invalidates every stored vector — re-embedding is a full-corpus migration, so record the model name and version alongside the index.
