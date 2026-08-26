---
title: "Project: Anomaly Detection for Financial Transactions"
date: 2024-03-15
tags: ["python", "finance", "anomaly-detection"]
document_type: "project"
status: Completed
git_repo: "https://github.com/raunakdey-07/transaction-anomalies"
summary: "Unsupervised detection of unusual personal transactions across categories and merchants"
---

# Personal Transaction Anomaly Detection

Applied anomaly detection to my own transaction history — the consumer version of the server-metrics project.

## Approach

- Per-category spending baselines with seasonal adjustment
- **Isolation Forest** over amount, merchant frequency, and time-since-last features
- Explainable flags: each anomaly annotated with its most-divergent feature
- Feedback loop: confirmed-fine flags suppress similar future alerts

## Findings

Merchant-frequency was the strongest signal — new merchants with atypical amounts. False-positive rate mattered more than recall for daily-use tolerance.
