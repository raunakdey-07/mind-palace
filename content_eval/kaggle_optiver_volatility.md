---
title: "Kaggle: Optiver Realized Volatility Prediction"
date: 2023-10-28
tags: ["kaggle", "finance", "time-series", "tabular"]
document_type: "kaggle"
competition: "Optiver Volatility"
status: Completed
summary: "Short-term volatility forecasting from order book data with GBDT ensembles"
---

# Optiver Realized Volatility Prediction

Finance-domain time series: predicting 10-minute realized volatility from order-book snapshots.

## Approach

- Hand-crafted order-book features: bid-ask spread, WAP, log-return moments across time buckets
- LightGBM + NN two-model blend
- Per-stock-id target normalization
- Purged time-series cross-validation to prevent leakage across overlapping windows

## Results

RMSPE 0.229. Feature engineering on the first seconds of each bucket dominated model choice.
