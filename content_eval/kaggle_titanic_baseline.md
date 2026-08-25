---
title: "Kaggle Notebooks: Titanic Survival Baseline"
date: 2022-11-03
tags: ["kaggle", "tabular", "beginner"]
document_type: "kaggle"
competition: "Titanic"
status: Completed
summary: "Classic tabular baseline with feature engineering and gradient boosting"
---

# Titanic Survival Baseline

First Kaggle competition: predicting Titanic survival from tabular passenger data.

## Feature Engineering

- Title extraction from names (Mr, Mrs, Master)
- Family size binning
- Cabin deck letter imputation
- Fare per-person normalization

## Model

Gradient boosting (XGBoost) with early stopping; 0.78 public score.
