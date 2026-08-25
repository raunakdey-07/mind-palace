---
title: "Kaggle: Santander Customer Transaction Prediction"
date: 2023-01-28
tags: ["kaggle", "tabular", "classification"]
document_type: "kaggle"
competition: "Santander Transaction"
status: Completed
summary: "Binary classification on anonymized features with magic-feature engineering"
---

# Santander Customer Transaction Prediction

Anonymized-feature binary classification — a pure signal-hunting competition.

## Approach

- LightGBM with heavy feature engineering: counts, unique values, aggregated statistics per feature value
- "Magic" features from statistical analysis of synthetic structure
- Adversarial validation confirmed train/test consistency
- 10-seed bagging for variance reduction

## Results

AUC 0.928 private. Lesson: with anonymous features, distribution statistics beat domain intuition by definition.
