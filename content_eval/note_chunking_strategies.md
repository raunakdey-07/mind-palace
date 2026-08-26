---
title: "Note: Chunking Strategies for Retrieval"
date: 2024-04-18
tags: ["rag", "chunking", "note"]
document_type: "note"
status: Complete
summary: "Fixed windows, structural splitting, and contextual enrichment for retrieval chunks"
---

# Chunking Strategies for Retrieval

Deeper dive on the chunking half of the RAG patterns note.

## Strategies Compared

- **Fixed window with overlap**: simple, structure-blind; safe default for prose
- **Structural (headings/sections)**: preserves semantic boundaries — what Mind Palace uses
- **Sentence-window**: small chunks retrieved, expanded to neighbors at read time
- **Semantic chunking**: split at embedding-similarity drop-offs; expensive to build

## Context Enrichment

Prepend document title and heading path to each chunk at index time so a retrieved fragment carries its own context. This costs storage but consistently improves answer grounding.

## Sizing

Chunk size interacts with the embedding model's training distribution; 200–500 tokens suits most sentence-transformers. Overlap of 10–20% recovers boundary-cut answers.
