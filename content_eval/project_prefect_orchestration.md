---
title: "Project: Data Pipeline Orchestration with Prefect"
date: 2024-06-15
tags: ["python", "mlops", "orchestration"]
document_type: "project"
status: In Progress
git_repo: "https://github.com/raunakdey-07/prefect-pipelines"
summary: "Scheduled data ingestion and model retraining pipelines with Prefect flows"
---

# Pipeline Orchestration with Prefect

Scheduling layer for the feature store and model retraining jobs.

## Design

- Flows for scrape → clean → featurize → train → evaluate → register
- Task retries with exponential backoff; alerting on final failure
- Parameterized runs per data source
- Evaluation gate: retrained models deploy only if they beat the current champion on the golden set

## Status

Ingestion and training flows stable; the champion-challenger promotion gate is the remaining work.
