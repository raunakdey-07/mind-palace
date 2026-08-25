---
title: "Note: Experiment Tracking and Reproducibility"
date: 2023-11-20
tags: ["devops", "mlops", "note"]
document_type: "note"
status: Complete
summary: "Tracking runs, seeds, data versions, and environment pinning for reproducible ML"
---

# Experiment Tracking and Reproducibility

Motivated by losing track of which Kaggle submission came from which script version.

## Practices

- Log parameters, metrics, and artifacts per run (MLflow or a plain CSV ledger)
- Fix seeds for python/numpy/torch; still expect GPU nondeterminism
- Version the *data*, not just code — hash raw inputs
- Pin dependencies exactly in requirements files; lock transitive deps for releases

## Mind Palace Application

The evaluation benchmark serves as a regression ledger: any retrieval change re-runs the same queries, making quality drift visible.
