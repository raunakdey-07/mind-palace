---
title: "Kaggle: Ubiquant Market Prediction"
date: 2023-05-18
tags: ["kaggle", "finance", "time-series", "tabular"]
document_type: "kaggle"
competition: "Ubiquant"
status: Completed
summary: "Anonymized asset return prediction with GBDT and NN ensembles under time constraints"
---

# Ubiquant Market Prediction

Anonymized investment-return regression — like Santander but with a strict time-based API latency budget.

## Approach

- Feature groups from lagged return windows
- **LightGBM** + MLP ensemble; the NN contributed most in later time buckets
- Time-series CV with embargo gaps to prevent window overlap leakage
- Inference-time feature computation vectorized to meet the latency limit

## Results

Pearson 0.112 private. The latency constraint eliminated half the model zoo — an operational lesson that carried into Mind Palace's reranking decision.
