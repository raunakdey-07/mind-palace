---
title: "Note: PostgreSQL Indexing Strategies"
date: 2024-02-28
tags: ["database", "postgresql", "note"]
document_type: "note"
status: Complete
summary: "B-tree, GIN, and HNSW index selection for relational plus vector workloads"
---

# PostgreSQL Indexing Strategies

Operational notes behind the Mind Palace schema's index choices.

## Index Types

- **B-tree**: default; equality and range on scalars
- **GIN**: inverted index for arrays and full-text — backs the tag-overlap `&&` operator
- **HNSW**: approximate nearest-neighbor for vectors — backs cosine similarity search
- **trigram (GIN)**: substring/fuzzy text matching via pg_trgm

## Practices

Index the actual query workload, not every column. Filtered vector search benefits from combining scalar B-tree filters with the ANN index. Verify with EXPLAIN ANALYZE before and after adding any index.
