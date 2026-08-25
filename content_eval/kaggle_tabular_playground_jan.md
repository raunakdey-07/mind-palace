---
title: "Kaggle: Tabular Playground Series January"
date: 2024-01-05
tags: ["kaggle", "tabular", "playground"]
document_type: "kaggle"
competition: "Tabular Playground Jan"
status: Completed
summary: "Monthly playground competition; gradient boosting on synthetic tabular data"
---

# Tabular Playground Series January

Synthetic-data tabular competition — practice ground for feature engineering patterns.

## Approach

- LightGBM with Bayesian hyperparameter sweep (optuna)
- Feature selection via permutation importance
- Out-of-fold stacking with a ridge meta-model

## Takeaway

Synthetic features reward memorizing generator artifacts; gains rarely transfer to real data.
