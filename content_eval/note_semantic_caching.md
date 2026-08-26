---
title: "Note: Semantic Caching for LLM Services"
date: 2024-06-26
tags: ["llm", "performance", "note"]
document_type: "note"
status: Complete
summary: "Caching LLM responses by query similarity — savings and correctness risks"
---

# Semantic Caching for LLM Services

LLM responses are expensive; identical-ish questions recur. Cache them?

## Design

Embed incoming queries; if a cached embedding exceeds a similarity threshold, return the cached answer instead of generating.

## The Correctness Trap

Similar questions can need different answers. "What did I learn from BirdCLEF?" and "what did I learn from BirdCLEF 2024?" are semantically near-identical but factually distinct when the corpus holds both documents. Threshold tuning trades cost against silent wrong-answer risk.

## Safer Variant

Cache at the retrieval layer rather than the generation layer — retrieved document sets are more stable across paraphrases than answers, and stale retrievals are less harmful than stale conclusions.
