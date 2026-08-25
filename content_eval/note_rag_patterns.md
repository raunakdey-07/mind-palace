---
title: "Note: Retrieval Augmented Generation Patterns"
date: 2024-04-02
tags: ["ml-fundamentals", "rag", "retrieval", "note"]
document_type: "note"
status: Complete
summary: "Chunking, retrieval, grounding, and evaluation patterns for RAG systems"
---

# Retrieval Augmented Generation Patterns

Design notes distilled while building the docs chatbot and Mind Palace.

## Chunking

Structure-aware splitting (headings) beats fixed windows for documentation; preserve heading paths as metadata so retrieved chunks carry context.

## Retrieval

Hybrid retrieval combining embeddings with lexical signals is more robust than either alone; rank fusion (RRF) avoids score-scale calibration between signals.

## Grounding

Prompt-only grounding ("answer only from context") reduces but does not eliminate hallucination. True guarantees need post-hoc answer verification against sources.

## Evaluation

Build a labeled query set before tuning. Recall@k on relevant documents is the primary health metric; generation quality is downstream of retrieval quality.
