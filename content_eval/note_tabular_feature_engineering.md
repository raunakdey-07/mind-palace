---
title: "Note: Feature Engineering for Tabular Competitions"
date: 2023-07-02
tags: ["ml-fundamentals", "tabular", "feature-engineering", "note"]
document_type: "note"
status: Complete
summary: "Practical feature construction patterns for gradient boosting on tabular data"
---

# Feature Engineering for Tabular Competitions

Distilled patterns from Titanic and House Prices iterations.

## Patterns

- **Target encoding** with smoothing and out-of-fold computation to avoid leakage
- Frequency/count encodings for categorical cardinality signals
- Aggregations per group (mean/max/std of numerics within a category)
- Interaction features only after univariate importance screening
- Datetime decomposition: cyclical encoding of hour/day/month

## Boosting Notes

LightGBM handles missing values natively; XGBoost needs explicit imputation. Early stopping on the validation fold is the single most reliable overfitting guard.
