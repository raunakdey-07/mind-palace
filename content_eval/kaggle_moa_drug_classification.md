---
title: "Kaggle: Mechanisms of Action Drug Classification"
date: 2023-07-08
tags: ["kaggle", "tabular", "multi-label"]
document_type: "kaggle"
competition: "MoA"
status: Completed
summary: "Multi-label drug mechanism prediction with label smoothing and ensembling"
---

# Mechanisms of Action Classification

Predict biological mechanism targets from cell-response and gene-expression features.

## Approach

- Tabular deep learning: MLP with skip connections over 876 features
- **Label smoothing** + BCE loss across 206 binary targets
- Quadratic-weighted blending of NN and LightGBM probability outputs
- Seed-averaged 5-fold ensembling

## Results

Log-loss 0.0145 private. Control-type rows (ctl_vehicle) needed special handling — they carry no mechanism signal.
