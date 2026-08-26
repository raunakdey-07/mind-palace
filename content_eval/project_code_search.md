---
title: "Project: Code Search with Hybrid Retrieval"
date: 2024-05-08
tags: ["python", "search", "embeddings"]
document_type: "project"
status: In Progress
git_repo: "https://github.com/raunakdey-07/code-search"
summary: "Searching a personal codebase by intent using hybrid lexical and semantic retrieval"
---

# Code Search

Hybrid retrieval over source code — Mind Palace's architecture applied to a different corpus.

## Design

- Function-level chunking with signature + docstring prepended as context
- BM25 for exact identifier matching, embeddings for intent queries
- Fusion by RRF; language-aware filtering by file extension
- Index refresh hooked to git commits

## Early Findings

Identifier queries (exact symbols) are dominated by the lexical branch; intent queries ("where do we validate tokens") need embeddings. Neither alone suffices — the same conclusion as document retrieval.
