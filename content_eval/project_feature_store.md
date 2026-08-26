---
title: "Project: Feature Store with Redis and PostgreSQL"
date: 2024-05-12
tags: ["python", "mlops", "redis", "postgresql"]
document_type: "project"
status: In Progress
git_repo: "https://github.com/raunakdey-07/feature-store"
summary: "Dual-backend feature store: hot features in Redis, historical features in PostgreSQL"
---

# Feature Store

Training/serving skew elimination for the model-serving stack.

## Design

- **Hot path**: precomputed features in Redis with TTL-based freshness
- **Historical path**: point-in-time-correct feature tables in **PostgreSQL**
- Single feature-definition layer generating both backends
- Backfill jobs replay history into the online store

## Motivation

The classic failure mode: offline metrics computed on data the online service cannot see. Point-in-time correctness in the historical store is what makes offline evaluation trustworthy.
