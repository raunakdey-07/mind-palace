---
title: "Kaggle: Mayo Clinic Mesothelioma Prediction"
date: 2024-04-05
tags: ["kaggle", "tabular", "healthcare"]
document_type: "kaggle"
competition: "Mayo Clinic"
status: Completed
summary: "Clinical risk prediction with small-data techniques and heavy regularization"
---

# Mayo Clinic Mesothelioma

Small clinical dataset (~250 rows) — the opposite regime from every tabular playground.

## Approach

- Logistic regression with L1/L2 sweep; complex models overfit immediately
- Feature stability selection across bootstrap resamples
- Clinical-plausibility review of retained coefficients
- Leave-one-out CV as the only trustworthy validation at this size

## Results

AUC 0.71. The lesson set: with tiny data, simple models and domain review beat everything; validation strategy matters more than model choice.
