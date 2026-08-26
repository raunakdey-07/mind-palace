---
title: "Project: Sentiment Analysis API with DistilBERT"
date: 2023-10-20
tags: ["python", "nlp", "fastapi", "deep-learning"]
document_type: "project"
status: Completed
git_repo: "https://github.com/raunakdey-07/sentiment-api"
summary: "Containerized sentiment inference service with DistilBERT and latency benchmarking"
---

# Sentiment Analysis API

Compact end-to-end deployment: train, export, serve, benchmark.

## Design

- **DistilBERT** fine-tuned on SST-2; ONNX export for serving
- FastAPI service with dynamic batching (mirrors the model-serving project)
- Load-tested with locust: p95 tracked across concurrency levels
- Container image pinned by digest; model loaded once at startup

## Results

p95 of 45 ms at 50 concurrent clients on CPU. The load-test harness became the template for every later latency investigation, including Mind Palace's reranker analysis.
