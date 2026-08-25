---
title: "Paper Notes: Reciprocal Rank Fusion Outperforms Condorcet"
date: 2024-01-08
tags: ["paper", "retrieval", "ranking"]
document_type: "paper"
status: Read
summary: "Cormack et al. 2009 — RRF's simple score-free fusion beats sophisticated fusion methods"
---

# Paper: Reciprocal Rank Fusion (Cormack et al., 2009)

The justification for Mind Palace's default fusion strategy.

## Core Idea

Combine ranked lists by summing `1/(k + rank)` per document with constant k=60 — no scores needed, only ranks.

## Key Findings

- RRF beat the best individual system and every learned fusion method tested
- Score-free fusion is robust to incomparable score scales across systems
- Performance is insensitive to k in a broad range around 60

## Relevance

Directly explains why hybrid+RRF is the strongest Mind Palace strategy: vector cosine similarities and trigram similarities live on different scales that no fixed weighted average can reconcile.
