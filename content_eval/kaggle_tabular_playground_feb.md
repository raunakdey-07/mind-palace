---
title: "Kaggle: Tabular Playground February Continuous Regression"
date: 2024-02-10
tags: ["kaggle", "tabular", "playground", "regression"]
document_type: "kaggle"
competition: "Tabular Playground Feb"
status: Completed
summary: "Continuous-target playground with neural network and GBDT blending"
---

# Tabular Playground February

Continuous-target synthetic regression — the NN-versus-GBDT comparison playground.

## Approach

- **MLP** with embedding layers for categorical features
- LightGBM/CatBoost blend; NN contributed most where interactions were smooth
- Optuna sweeps per model family, blended by ridge on out-of-fold predictions
- Target quantile transformation for the skewed distribution

## Results

RMSE 0.139. The blend beat every individual model by 0.003–0.005 — consistent with the House Prices stacking result.
