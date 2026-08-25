---
title: "Note: Hyperparameter Tuning Strategies"
date: 2023-08-05
tags: ["ml-fundamentals", "hyperparameters", "optuna", "note"]
document_type: "note"
status: Complete
summary: "Grid, random, Bayesian, and successive-halving approaches to hyperparameter search"
---

# Hyperparameter Tuning Strategies

Practical tuning guidance accumulated across Kaggle and project work.

## Methods

- **Grid search**: only when parameters are few and independent
- **Random search**: better than grid in high dimensions
- **Bayesian (optuna/TPE)**: default choice; used in the tabular playground and volatility work
- **Successive halving (Hyperband)**: cheap early pruning of bad trials

## Rules of Thumb

Tune learning rate first, then regularization, then architecture-specific knobs. Always tune against the same CV scheme used for final evaluation.
