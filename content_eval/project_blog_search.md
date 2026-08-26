---
title: "Project: Static Blog with Search"
date: 2024-02-05
tags: ["python", "web", "search"]
document_type: "project"
status: Completed
git_repo: "https://github.com/raunakdey-07/blog-search"
summary: "Client-side search over a static blog using precomputed embeddings and fuzzy matching"
---

# Static Blog with Client-Side Search

Search for a static site with no server — the deployment-constrained retrieval problem.

## Design

- Post embeddings precomputed at build time, shipped as a compact binary blob
- Client-side cosine search in WebAssembly-friendly JavaScript
- Fuzzy title/keyword fallback for low-power devices
- Index size budget: quantized embeddings kept the blob under 2 MB

## Constraint Lesson

Shipping the index to the client forces hard size discipline — quantization choices that were optional for pgvector became mandatory here. The same corpus, opposite constraints.
