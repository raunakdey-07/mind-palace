---
title: "Note: Debugging Retrieval Quality Regressions"
date: 2024-07-02
tags: ["retrieval", "debugging", "note"]
document_type: "note"
status: Complete
summary: "A systematic procedure for finding why retrieval quality dropped"
---

# Debugging Retrieval Regressions

Procedure written after living through the RRF score-mapping bug.

## Steps

1. **Verify the benchmark**: are labels still valid against the corpus? Title drift mimics regression.
2. **Bisect strategies**: does vector-only also regress, or only fused modes? Localizes the stage.
3. **Inspect per-query**: aggregate drops of 0.02 are usually one or two queries; look at those, not averages.
4. **Check score semantics**: is the exposed score actually what the SQL computes? Column-order bugs produce plausible-looking nonsense.
5. **Confirm determinism**: run twice; nondeterminism masquerades as noise and hides real changes.

## Rule

Never tune parameters to fix a regression before identifying its class. Tuning on top of a bug encodes the bug into the configuration.
