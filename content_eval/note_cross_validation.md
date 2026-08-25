---
title: "Note: Understanding Cross-Validation Strategies"
date: 2023-05-11
tags: ["ml-fundamentals", "validation", "note"]
document_type: "note"
status: Complete
summary: "When to use k-fold, stratified, group, and time-series cross-validation"
---

# Understanding Cross-Validation Strategies

Reference note on choosing validation schemes — the source of most silent data leakage bugs.

## Schemes

- **k-fold**: default for i.i.d. data
- **Stratified k-fold**: preserves class ratios; mandatory for imbalanced classification like the disaster tweets task
- **Group k-fold**: keeps all rows of one entity in the same fold; required when multiple rows share a source
- **Time-series split**: expanding window; never shuffle temporal data

## Common Mistakes

Fitting scalers or target encoders on the full dataset before splitting leaks test information into training. Every preprocessing step must live inside the fold.
