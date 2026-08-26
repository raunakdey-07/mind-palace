---
title: "Note: Observability Metrics That Matter"
date: 2024-04-08
tags: ["mlops", "monitoring", "note"]
document_type: "note"
status: Complete
summary: "Choosing service metrics: the four golden signals adapted for ML systems"
---

# Observability Metrics That Matter

Monitoring guidance distilled from the dashboard and model-serving projects.

## The Four, Adapted

- **Latency**: per-stage histograms, not end-to-end averages
- **Traffic**: request rate by endpoint and by feature flags
- **Errors**: typed — dependency failures count differently from invalid requests
- **Saturation**: queue depths, pool utilization, cache hit rates

## ML-Specific Additions

Confidence/score distributions drift before accuracy does; track them. Empty-result rates distinguish index problems from query problems. Evaluation-benchmark runs are themselves a monitoring signal — schedule them.

## Anti-Pattern

Alerting on averages. Users experience tails; p95/p99 with a small window is the minimum useful resolution.
