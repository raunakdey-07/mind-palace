---
title: "Kaggle: Mechanistic Moisture Prediction Soil"
date: 2024-05-28
tags: ["kaggle", "regression", "climate"]
document_type: "kaggle"
competition: "Soil Moisture"
status: Completed
summary: "Climate time-series regression blending physical models with gradient boosting residuals"
---

# Soil Moisture Prediction

Climate-grid soil moisture regression — a hybrid physics-plus-ML exercise.

## Approach

- Physical water-balance model as the base prediction
- **LightGBM** trained on residuals (what physics misses) rather than the target directly
- Spatial cross-validation by grid cell to test geographic generalization
- Feature importance reviewed for physical plausibility

## Results

The residual-learning decomposition beat direct prediction and remained interpretable: the ML component captured vegetation effects the physical model ignored. A template for combining domain models with data-driven correction.
