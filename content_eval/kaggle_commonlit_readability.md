---
title: "Kaggle: CommonLit Readability Prize"
date: 2023-03-08
tags: ["kaggle", "nlp", "regression"]
document_type: "kaggle"
competition: "CommonLit Readability"
status: Completed
summary: "Text complexity regression with RoBERTa ensembles and stopword-aware analysis"
---

# CommonLit Readability Prize

Regression of passage reading complexity — earlier and simpler than the Feedback Prize work.

## Approach

- **RoBERTa-large** with mean pooling
- Extractive features (avg sentence length, rare-word ratio) as auxiliary inputs
- 5-fold CV, seed averaged

## Results

RMSE 0.462. Simple lexical features explained most of what the transformer added — a humbling early lesson about baselines.
