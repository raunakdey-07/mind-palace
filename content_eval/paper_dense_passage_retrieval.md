---
title: "Paper Notes: Dense Passage Retrieval for Open-Domain QA"
date: 2023-09-25
tags: ["paper", "retrieval", "embeddings", "rag"]
document_type: "paper"
status: Read
summary: "Karpukhin et al. 2020 — dual-encoder dense retrieval beating BM25 for open-domain QA"
---

# Paper: Dense Passage Retrieval (Karpukhin et al., 2020)

The paper behind the "dense retrieval beats lexical" consensus that motivates Mind Palace's vector search.

## Core Idea

Two **BERT encoders** map questions and passages to a shared vector space; similarity is inner product. Fine-tuned on question-passage pairs, dense retrieval substantially outperforms BM25 top-100 passage recall.

## Key Findings

- In-batch negatives make training tractable at scale
- Hard negatives (similar-but-wrong passages) matter more than random ones
- BM25 and dense retrieval are complementary — hybrid systems beat either

## Relevance

Justifies hybrid+RRF: the paper's own analysis shows lexical and dense signals fail on different query types.
