---
title: "Note: Cold Start Problems in Retrieval Systems"
date: 2024-07-03
tags: ["retrieval", "architecture", "note"]
document_type: "note"
status: Complete
summary: "Empty index, first queries, and bootstrapping quality before usage data exists"
---

# Cold Start in Retrieval Systems

Every retrieval system has a chicken-and-egg phase; design for it deliberately.

## Three Cold Starts

- **Empty index**: no documents — the system must abstain honestly, not hallucinate results
- **No query history**: no data for query expansion, caching, or popularity boosts
- **No relevance feedback**: no signal about which results satisfy users

## Design Responses

Abstention on empty retrieval (Mind Palace's fixed no-content answer) is correct cold-start behavior. Popularity-based features should be structured to activate only when data exists. Evaluation benchmarks substitute for usage data during bootstrap — which is why building them early matters.

## The Transition

The riskiest moment is leaving cold start: early usage patterns get baked into tuning before they're representative. Re-validate against fresh queries once real traffic exists.
