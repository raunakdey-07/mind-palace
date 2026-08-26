---
title: "Kaggle: LANL Earthquake Prediction"
date: 2023-02-28
tags: ["kaggle", "time-series", "signal-processing"]
document_type: "kaggle"
competition: "LANL Earthquake"
status: Completed
summary: "Seismic signal regression to time-to-failure with statistical signal features"
---

# LANL Earthquake Prediction

Raw seismic acoustic data → time until laboratory failure. Pure signal processing.

## Approach

- Segmented the continuous signal into 150k-sample chunks
- Statistical features per segment: kurtosis, skew, spectral centroids, rolling quantiles
- **LightGBM** over features beat LSTM on raw signal — tabular features won
- Time-ordered validation; shuffling guaranteed leakage

## Results

MAE 2.1 seconds. A reminder that for structured physical signals, feature engineering remains competitive with end-to-end deep learning.
