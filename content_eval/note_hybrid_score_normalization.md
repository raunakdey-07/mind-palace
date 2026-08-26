---
title: "Note: Hybrid Search Score Normalization Pitfalls"
date: 2024-06-02
tags: ["retrieval", "search", "note"]
document_type: "note"
status: Complete
summary: "Why mixing cosine and trigram scores with fixed weights is fragile, and rank fusion fixes it"
---

# Hybrid Search Score Normalization

The engineering story behind Mind Palace's RRF preference.

## The Problem

Cosine similarity lives in roughly [0.5, 1.0] for normalized embeddings; trigram similarity spans [0, 1] but clusters near zero for unrelated text. A fixed weighted average implicitly assumes comparable distributions — they are not.

## Failed Approaches

- **Min-max normalization** per query: unstable when one signal has outliers
- **Z-score normalization**: assumes normality neither signal satisfies
- **Learned weights**: require labeled data and drift as the corpus changes

## Rank Fusion

RRF uses only ordinal information: `1/(k + rank)`. Ranks are scale-free by construction, so no calibration is needed and the fusion is stable under corpus growth. This is why score-free fusion survives corpus expansion while weighted blends need retuning.
