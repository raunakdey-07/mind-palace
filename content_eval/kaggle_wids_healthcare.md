---
title: "Kaggle: WiDS Datathon Healthcare Equity"
date: 2024-01-15
tags: ["kaggle", "tabular", "healthcare"]
document_type: "kaggle"
competition: "WiDS Datathon"
status: Completed
summary: "Healthcare duration-of-stay prediction with fairness-aware evaluation"
---

# WiDS Datathon: Healthcare Equity

Predicting ICU stay duration with an explicit fairness constraint across demographic groups.

## Approach

- CatBoost over mixed categorical/numeric clinical features
- Group-wise error auditing: max gap in MAE across protected groups
- Post-hoc calibration per group to equalize error rates
- SHAP values for clinical plausibility review

## Results

Competition metric improved 3% while the demographic error gap shrank — evidence that fairness constraints and leaderboard position were not in tension here.
