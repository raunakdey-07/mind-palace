---
title: "Note: Latency Budgeting for ML Systems"
date: 2024-05-20
tags: ["mlops", "performance", "note"]
document_type: "note"
status: Complete
summary: "Decomposing end-to-end latency, percentile targets, and where optimization pays"
---

# Latency Budgeting for ML Systems

Operational companion to the model-serving project.

## Decompose First

Measure each stage — tokenization, feature building, inference, post-processing — before optimizing anything. Percentiles lie less than averages: p95 exposes the tail that averages hide.

## Common Costs

- Model loading on cold start dominates first-request latency; warm the model at startup
- Network serialization can exceed inference time for small models
- Reranking stages multiply cost by candidate count: reranking 50 candidates costs 5× reranking 10

## Budget Allocation

Assign each stage a budget proportional to user tolerance, not to ease of optimization. A 100 ms stage consuming 90% of the budget deserves attention before a 10 ms stage consuming 9%.
