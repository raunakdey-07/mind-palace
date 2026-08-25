---
title: "Note: Vector Databases and Embedding Search"
date: 2024-03-22
tags: ["ml-fundamentals", "embeddings", "vector-search", "note"]
document_type: "note"
status: Complete
summary: "ANN indexes, similarity metrics, and the pgvector vs dedicated vector DB tradeoff"
---

# Vector Databases and Embedding Search

Foundation note for Mind Palace's storage decision.

## Index Types

- **Flat**: exact search, fine below ~100k vectors
- **IVF**: cluster-then-probe; tunable recall/speed
- **HNSW**: graph-based, high recall, memory-hungry — what pgvector provides and Mind Palace uses

## Metrics

Cosine vs inner product vs L2: with normalized embeddings, cosine and inner product are equivalent — which is why the embedder normalizes.

## pgvector vs Dedicated Vector DBs

Postgres keeps vectors next to relational metadata, enabling filtered search in one query. Dedicated vector DBs win at billion scale or with heavy write churn. At portfolio scale, pgvector removes an entire operational system.
