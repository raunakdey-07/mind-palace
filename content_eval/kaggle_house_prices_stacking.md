---
title: "Kaggle: House Prices Regression with Stacking"
date: 2023-02-14
tags: ["kaggle", "tabular", "regression", "ensemble"]
document_type: "kaggle"
competition: "House Prices"
status: Completed
summary: "Stacked regressions with target encoding for the Ames housing dataset"
---

# House Prices Regression with Stacking

Ames, Iowa housing price prediction — a regression competition.

## Approach

- **Target encoding** for high-cardinality neighborhoods
- **Stacking**: ridge + lasso + lightgbm base models, meta-learner on out-of-fold predictions
- Log-target transformation for skewed sale prices
- Skew correction via Box-Cox on numeric features

## Results

RMSLE 0.114 on the private leaderboard; stacking beat the best single model by 0.008 RMSLE.
