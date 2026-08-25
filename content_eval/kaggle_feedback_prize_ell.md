---
title: "Kaggle: Feedback Prize English Language Learning"
date: 2023-04-22
tags: ["kaggle", "nlp", "regression"]
document_type: "kaggle"
competition: "Feedback Prize 3"
status: Completed
summary: "Predicting English learner essay scores across six analytic measures with DeBERTa"
---

# Feedback Prize: English Language Learning

Regression over essays scoring cohesion, syntax, vocabulary, phraseology, grammar, and conventions.

## Approach

- **DeBERTa-v3-base** with a regression head per measure
- Long-sequence handling via sliding windows over essays
- Multi-task loss with uncertainty weighting
- 5-fold ensemble, seed-averaged

## Results

MSE 0.043 private. Syntax and conventions were easiest to predict; cohesion needed the longest context windows.
