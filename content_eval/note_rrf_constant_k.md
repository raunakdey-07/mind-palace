---
title: "Note: Choosing k in Reciprocal Rank Fusion"
date: 2024-06-06
tags: ["retrieval", "ranking", "note"]
document_type: "note"
status: Complete
summary: "Sensitivity of RRF to the constant k, and why 60 works across corpora"
---

# The RRF Constant k

Why Mind Palace hardcodes `k=60` and when to question it.

## Role of k

`1/(k + rank)` — small k makes top ranks dominate aggressively; large k flattens differences. At k=60, rank 1 scores ~0.0164 vs rank 2's ~0.0161: a deliberately gentle falloff that lets consistent performers across both signal lists win.

## Evidence

The original RRF paper found performance insensitive across k ∈ [1, 1000], with 60 near-optimal for their TREC-style workloads. My own informal sweeps on the retrieval benchmark agreed: k=10 through k=100 changed aggregate metrics by less than run-to-run noise.

## When to Revisit

If candidate lists become very long (thousands) or one signal is systematically more reliable, per-signal weighting or tuned k could matter. Neither condition holds at current scale.
