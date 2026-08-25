---
title: "Paper Notes: Sentence-BERT Sentence Embeddings using Siamese BERT-Networks"
date: 2023-08-18
tags: ["paper", "embeddings", "sentence-transformers"]
document_type: "paper"
status: Read
summary: "Reimers & Gurevych 2019 — siamese fine-tuning making BERT usable for semantic search"
---

# Paper: Sentence-BERT (Reimers & Gurevych, 2019)

The paper behind the sentence-transformers library that produces Mind Palace's embeddings.

## Core Idea

Standard BERT requires feeding both sentences together — O(n²) for search. **Siamese networks** fine-tune BERT to produce fixed-size sentence vectors comparable via cosine similarity, reducing search from hours to seconds.

## Training Objectives

- Classification head on concatenated sentence pairs
- Regression on STS semantic-textual-similarity scores
- Triplet loss for contrastive retrieval

## Relevance

Explains why `all-MiniLM-L6-v2` outputs need normalization for cosine equivalence, and why embedding models are separate from generation LLMs.
