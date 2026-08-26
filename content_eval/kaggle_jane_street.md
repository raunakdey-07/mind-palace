---
title: "Kaggle: Jane Street Market Prediction"
date: 2023-02-20
tags: ["kaggle", "finance", "tabular", "deep-learning"]
document_type: "kaggle"
competition: "Jane Street"
status: Completed
summary: "Intraday trade profitability prediction with autoencoder denoising and MLP ensembles"
---

# Jane Street Market Prediction

Anonymized intraday financial features; predict trade return sign and magnitude.

## Approach

- **Denoising autoencoder** pretraining on the 130 anonymous features
- MLP with skip connections per horizon day
- Purged group time-series split; utility metric optimized via threshold tuning
- Online learning adaptation for the live phase

## Results

The utility score was dominated by position sizing, not classification accuracy — an early exposure to how real trading objectives differ from AUC.
