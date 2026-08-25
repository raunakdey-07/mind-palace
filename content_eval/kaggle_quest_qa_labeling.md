---
title: "Kaggle: Google QUEST Q&A Labeling"
date: 2023-11-08
tags: ["kaggle", "nlp", "multi-label"]
document_type: "kaggle"
competition: "Google QUEST Q&A Labeling"
status: Completed
summary: "Multi-label question-answer quality scoring with BERT and sentence embeddings"
---

# Google QUEST Q&A Labeling

Multi-label regression across 30 question/answer quality dimensions.

## Approach

- **BERT** for token-level features plus **Universal Sentence Encoder** for question-body pairs
- Multi-sample dropout on pooled outputs
- Per-target loss weighting after sweep
- Post-processing: clipping predictions to observed label ranges

## Results

 Spearman 0.392 private. The biggest lesson: 30 weakly-correlated targets need per-target handling, not one shared head.
