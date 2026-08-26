---
title: "Note: Confidence Intervals for Model Comparison"
date: 2024-05-28
tags: ["statistics", "evaluation", "note"]
document_type: "note"
status: Complete
summary: "Bootstrap and paired-test methods for deciding whether model A beats model B"
---

# Confidence Intervals for Model Comparison

The statistical machinery behind honest strategy comparisons.

## Methods

- **Bootstrap resampling** over evaluation queries gives distribution-free intervals for any metric
- **Paired differences**: compare strategies on the same queries; variance of the difference is smaller than either variance because query difficulty cancels
- **Permutation tests**: exchangeable under the null; exact for small samples

## Interpretation Rules

If the paired-difference interval contains zero, the strategies are indistinguishable at that sample size — prefer the simpler/faster one. Never report overlapping-vs-non-overlapping error bars as a test; compute the difference directly.

## Application

Retrieval strategy comparisons on small benchmarks are exactly this scenario: point estimates differ by less than the noise, so latency and simplicity become the tiebreakers.
