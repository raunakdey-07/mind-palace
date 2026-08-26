---
title: "Kaggle: Google Brain Ventilator Pressure Prediction"
date: 2023-09-22
tags: ["kaggle", "time-series", "regression", "deep-learning"]
document_type: "kaggle"
competition: "Ventilator Pressure"
status: Completed
summary: "Mechanical ventilator airway pressure regression with GRU sequences and physics features"
---

# Ventilator Pressure Prediction

Simulate pulmonary mechanics: predict pressure sequence from inspired controls — a Kaggle competition with genuine clinical grounding.

## Approach

- **GRU** over breath sequences with per-step controls (R, C, u_in)
- Physics-informed feature: cumulative volume integral
- Breath-level grouping in CV; no mixing of breaths from one patient
- Post-hoc monotonicity correction for the expiratory phase

## Results

MAE 0.21. The cumulative-volume feature was the largest single gain — domain structure beat architecture tuning.
