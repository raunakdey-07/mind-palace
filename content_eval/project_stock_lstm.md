---
title: "Project: Stock Price Predictor with LSTM Networks"
date: 2023-08-22
tags: ["python", "finance", "deep-learning", "time-series"]
document_type: "project"
status: Completed
git_repo: "https://github.com/raunakdey-07/stock-lstm"
summary: "LSTM sequence models for stock price movement prediction — an educational deep-learning exercise"
---

# Stock Price Predictor with LSTM

An educational project predicting next-day price direction — explicitly *not* a trading system.

## Model

- Stacked **LSTM** layers (64, 32 units) over 60-day sliding windows
- Dropout between recurrent layers
- Min-max scaling fit on train windows only to avoid leakage

## Honest Findings

Directional accuracy barely exceeded 52% out-of-sample. The exercise demonstrates sequence modeling mechanics; markets are close to efficient at daily horizons and this model captures no real edge.
