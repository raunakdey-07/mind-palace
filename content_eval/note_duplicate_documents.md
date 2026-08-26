---
title: "Note: Handling Duplicate and Near-Duplicate Documents"
date: 2024-07-01
tags: ["retrieval", "data-engineering", "note"]
document_type: "note"
status: Complete
summary: "Exact and near-duplicate detection before indexing, and why duplicates corrupt retrieval evaluation"
---

# Duplicate Documents in Retrieval

Duplicates are a retrieval-quality problem, not just storage waste.

## Why They Hurt

- Top-k results fill with copies of one document, crowding out genuinely diverse evidence
- Evaluation metrics assume distinct documents; duplicates inflate precision and distort nDCG
- RRF amplifies the problem: a document ranking high in both signal lists gets double-boosted

## Detection

- **Exact**: content hashes — Mind Palace's ingestion already prevents exact re-ingestion via manifest
- **Near-duplicate**: SimHash/MinHash over shingles, or embedding-similarity thresholds with human review

## Evaluation Impact

Document-level metric deduplication (collapsing multiple chunks of one document) is mandatory — without it, chunk-heavy documents dominate every top-k list regardless of true relevance.
