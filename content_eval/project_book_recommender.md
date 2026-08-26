---
title: "Project: Book Library Semantic Recommender"
date: 2024-02-22
tags: ["python", "embeddings", "streamlit"]
document_type: "project"
status: Completed
git_repo: "https://github.com/raunakdey-07/book-recs"
summary: "Personalized book recommendations from reading history using description embeddings"
---

# Book Library Recommender

Embedding-based recommendations over a personal reading history — the smallest useful retrieval system I built.

## Design

- **sentence-transformers** embeddings over book descriptions
- Taste profile = weighted average of read-book embeddings, recency-weighted
- Recommendations by cosine similarity excluding already-read titles
- Explanation layer: nearest read book shown for each recommendation

## Findings

The explanation layer changed perceived quality more than the ranking itself. Showing *why* ("because you liked X") made mediocre recommendations acceptable and good ones compelling.
