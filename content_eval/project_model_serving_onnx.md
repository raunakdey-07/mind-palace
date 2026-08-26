---
title: "Project: ML Model Serving with FastAPI and ONNX"
date: 2024-04-10
tags: ["python", "fastapi", "mlops", "deployment"]
document_type: "project"
status: Completed
git_repo: "https://github.com/raunakdey-07/model-serving"
summary: "Low-latency model inference service using ONNX Runtime with batching and caching"
---

# Model Serving with ONNX Runtime

Generic low-latency inference service — the deployment counterpart to the FastAPI template.

## Design

- Models exported to **ONNX**; served via onnxruntime with intra-op thread tuning
- Dynamic request **batching** window (5 ms) trading latency for throughput
- LRU prediction cache keyed on feature hashes
- Prometheus histograms for per-stage latency accounting

## Findings

Batching helped p50 throughput but hurt p95 under bursty load; the cache absorbed ~35% of production traffic. Latency accounting per stage is what made the tradeoffs visible at all.
