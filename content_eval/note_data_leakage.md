---
title: "Note: Data Leakage Catalog"
date: 2023-12-08
tags: ["ml-fundamentals", "validation", "note"]
document_type: "note"
status: Complete
summary: "A catalog of leakage modes: target, temporal, group, and preprocessing leakage"
---

# Data Leakage Catalog

Every leakage mode I have personally committed or debugged.

## Modes

- **Target leakage**: a feature is a proxy for the label (post-outcome fields)
- **Temporal leakage**: future information in past predictions — needs time-based splits with embargo gaps
- **Group leakage**: same entity in train and test — needs group splits
- **Preprocessing leakage**: scalers/encoders fit before splitting
- **Benchmark leakage**: tuning on the test set by repeated evaluation

## Detection

Suspiciously high performance is the primary symptom. Adversarial validation (can you distinguish train from test?) catches distribution shifts; ablation of suspicious features catches proxies.

## Mind Palace Application

The benchmark query set must never be tuned against retrieval results, or it becomes a leakage channel into the architecture.
