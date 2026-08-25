---
title: "Paper Notes: RAG Retrieval-Augmented Generation for Knowledge-Intensive NLP Tasks"
date: 2024-04-20
tags: ["paper", "rag", "retrieval", "llm"]
document_type: "paper"
status: Read
summary: "Lewis et al. 2020 — the paper defining retrieval-augmented generation as an architecture"
---

# Paper: RAG (Lewis et al., 2020)

The foundational citation for Mind Palace's query layer.

## Core Idea

Couple a parametric seq2seq model with a **non-parametric dense retriever** (DPR): the generator conditions on retrieved Wikipedia passages, and the retriever is jointly optimized through the generation loss.

## Variants

- RAG-Sequence: one retrieved document per answer
- RAG-Token: different documents can inform different answer tokens

## Key Findings

Outperforms purely parametric seq2seq on knowledge-intensive tasks; generated content is more specific and factual. The retriever provides a natural attribution path — answers trace to passages.

## Relevance

Defines exactly the architecture Mind Palace implements at portfolio scale, minus joint training.
