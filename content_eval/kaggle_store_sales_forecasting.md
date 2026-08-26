---
title: "Kaggle: Store Sales Time Series Forecasting"
date: 2023-08-30
tags: ["kaggle", "time-series", "forecasting"]
document_type: "kaggle"
competition: "Store Sales"
status: Completed
summary: "Grocery sales forecasting with lag features, holidays, and LightGBM"
---

# Store Sales Forecasting

Corporación Favorita grocery sales — hierarchical time series across stores and product families.

## Approach

- Lag features (1, 7, 14, 28 days) and rolling means per store-family
- Holiday/event calendar joins, oil price as econometric covariate
- **LightGBM** single model across all series with entity embeddings for store/family
- RMSLE evaluation matching the competition metric

## Results

RMSLE 0.42. Promotion flags and pay-day cycles carried more signal than long lags.
