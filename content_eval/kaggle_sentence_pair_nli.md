---
title: "Kaggle: Natural Language Inference with Sentence Pairs"
date: 2023-04-10
tags: ["kaggle", "nlp", "classification"]
document_type: "kaggle"
competition: "Sentence Pair NLI"
status: Completed
summary: "Entailment classification baseline comparing siamese encoders with cross-encoders"
---

# Sentence Pair NLI Baseline

Comparison study preceding the multilingual NLI competition.

## Compared

- **Bi-encoder**: separate sentence embeddings, cosine-scored — fast, precomputable
- **Cross-encoder**: both sentences through one transformer — accurate, O(n) per pair

## Findings

The cross-encoder won accuracy by 3 points but cannot precompute; retrieval systems resolve this exactly the way Mind Palace does — bi-encoder candidates, cross-encoder reranking.
