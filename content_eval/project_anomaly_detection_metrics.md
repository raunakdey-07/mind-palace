---
title: "Project: Time Series Anomaly Detection for Server Metrics"
date: 2024-05-30
tags: ["python", "time-series", "anomaly-detection", "monitoring"]
document_type: "project"
status: In Progress
git_repo: "https://github.com/raunakdey-07/metric-anomalies"
summary: "Unsupervised anomaly detection over CPU/memory/latency telemetry streams"
---

# Time Series Anomaly Detection

Unsupervised detection of incidents in server telemetry — the ops cousin of the stock LSTM project, same sequence domain, different goal.

## Approach

- Seasonal decomposition to separate daily cycles from residuals
- **Isolation Forest** and statistical z-score baselines on residuals
- Rolling quantile bands as an explainable fallback
- Alert deduplication window to avoid pager storms

## Status

Working prototype on synthetic + one real production dataset; precision acceptable, recall on slow-burn anomalies still weak.
