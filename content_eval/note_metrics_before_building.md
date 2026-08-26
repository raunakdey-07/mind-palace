---
title: "Note: Choosing Evaluation Metrics Before Building"
date: 2024-06-28
tags: ["evaluation", "ml-fundamentals", "note"]
document_type: "note"
status: Complete
summary: "Defining success metrics before system construction changes what gets built"
---

# Choose Metrics Before Building

The ordering discipline that separates engineering from tinkering.

## Why First

The metric defines the optimization target. Systems built before metrics get evaluated on whatever the finished system happens to do well — a subtle self-grading bias that mirrors benchmark contamination.

## Practice

- Write the evaluation query set and labels before writing retrieval code
- Define the primary metric (and its noise floor) in advance
- Pre-register which comparisons will be made; post-hoc metric shopping invalidates significance
- Accept that some designs will fail the metric — that is the point

## Mind Palace Application

The benchmark existed before the reranker was tuned against it, and the statistics note's noise-floor analysis was written before comparing strategies. Both orderings preserved the benchmark's integrity as an instrument.
