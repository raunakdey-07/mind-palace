---
title: "Note: Feature Flags and Progressive Delivery"
date: 2024-05-22
tags: ["devops", "architecture", "note"]
document_type: "note"
status: Complete
summary: "Decoupling deployment from release with flags, canaries, and rollback paths"
---

# Feature Flags and Progressive Delivery

Deployment discipline for risky changes — like swapping a retrieval default.

## Mechanisms

- **Feature flags** decouple deploy from activate; new retrieval strategies ship dark, then flip per-cohort
- **Canary analysis**: route a small percentage, compare error/latency/score distributions before full rollout
- **Instant rollback**: flag-off must be faster than redeploying

## For Retrieval Systems

Quality regressions are invisible to infrastructure monitoring — they need the evaluation benchmark run against production traffic samples. A flag without a measurement plan is just a slower rollout.
