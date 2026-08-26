---
title: "Paper Notes: ColBERT Efficient Late Interaction"
date: 2024-03-30
tags: ["paper", "retrieval", "ranking"]
document_type: "paper"
status: Reading
summary: "Khattab & Zaharia 2020 — token-level late interaction bridging dense and exact retrieval"
---

# Paper: ColBERT (Khattab & Zaharia, 2020)

The middle ground between single-vector dense retrieval and full cross-encoder reranking.

## Core Idea

Encode queries and documents with BERT but keep **per-token embeddings**; score with MaxSim — each query token attends to its best-matching document token. Precompute document encodings offline.

## Key Findings

- Quality close to cross-encoder reranking at a fraction of the online cost
- Document encodings are precomputed, so online latency is near single-vector speed
- Token-level matching recovers exact-match signal that pooled vectors lose

## Relevance

The natural candidate if Mind Palace ever needs reranker quality without reranker latency. It changes the storage schema (multiple vectors per chunk), which is why it is a paper note rather than a roadmap item.
