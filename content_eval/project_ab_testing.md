---
title: "Project: A/B Testing Framework for ML Features"
date: 2024-04-22
tags: ["python", "experimentation", "statistics"]
document_type: "project"
status: Completed
git_repo: "https://github.com/raunakdey-07/ab-testing"
summary: "Sequential A/B testing service with guardrail metrics and early stopping"
---

# A/B Testing Framework

Statistical experimentation service — the applied version of the statistics foundations note.

## Design

- Sequential probability ratio test instead of fixed-horizon t-tests
- Guardrail metrics (latency p95, error rate) that auto-pause experiments
- Variance-reduction via CUPED using pre-experiment covariates
- Assignment consistency through deterministic user hashing

## Lesson

Peeking at a naive p-value inflates false positives dramatically; sequential tests exist precisely because teams will not stop looking.
