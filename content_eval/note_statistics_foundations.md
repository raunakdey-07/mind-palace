---
title: "Note: Statistics Foundations for Machine Learning"
date: 2023-06-28
tags: ["ml-fundamentals", "statistics", "note"]
document_type: "note"
status: Complete
summary: "Distributions, hypothesis testing, and confidence intervals applied to model evaluation"
---

# Statistics Foundations

The math behind trustworthy evaluation — directly relevant to benchmark interpretation.

## Core Concepts

- **Sampling error**: a metric on N queries has variance; small N means wide confidence intervals
- **Confidence intervals**: bootstrap resampling gives distribution-free intervals on metrics like recall
- **Multiple comparisons**: testing many strategies on the same set inflates false positives; prefer fewer, pre-registered comparisons
- **Regression to the mean**: extreme scores on small samples shrink on repetition

## Application

A 26-query benchmark cannot distinguish strategies whose true difference is smaller than its sampling noise. Report intervals, not point estimates, when N is small.
