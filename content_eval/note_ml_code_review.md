---
title: "Note: Code Review for Machine Learning Changes"
date: 2024-06-16
tags: ["process", "mlops", "note"]
document_type: "note"
status: Complete
summary: "What to review in ML pull requests beyond code correctness"
---

# Code Review for ML Changes

ML changes need review dimensions that pure-software review misses.

## Review Checklist

- **Evaluation validity**: was the benchmark run before and after? Same dataset version?
- **Metric honesty**: are improvements within noise? Were all strategies re-measured, or only the changed one?
- **Data handling**: any leakage into training or tuning paths?
- **Reproducibility**: can a reviewer regenerate the claimed numbers from documented commands?
- **Rollback**: can the change be reverted without data migration?

## Anti-Pattern

Reviewing only the diff. A three-line change to ranking weights is trivially reviewable as code but requires the full evaluation report to review as an ML decision.
