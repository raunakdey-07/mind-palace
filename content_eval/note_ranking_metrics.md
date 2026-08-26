---
title: "Note: Ranking Metrics Deep Dive"
date: 2024-05-02
tags: ["ml-fundamentals", "evaluation", "ranking", "note"]
document_type: "note"
status: Complete
summary: "Recall, precision, MRR, MAP, and nDCG — definitions, pitfalls, and when each matters"
---

# Ranking Metrics Deep Dive

The theory behind Mind Palace's evaluation service.

## Metric Properties

- **Recall@k**: coverage of relevant items; insensitive to ordering among retrieved
- **Precision@k**: purity of the top-k; punishes filler
- **MRR**: first-relevant rank only; ignores later relevant documents
- **MAP**: average precision across all relevant; order-sensitive throughout
- **nDCG**: position-discounted, supports graded relevance; normalized to [0,1]

## Pitfalls

- Chunk-level retrieval with document-level labels inflates precision unless duplicates are collapsed
- Comparing metrics across different k values is meaningless
- Small query sets give wide confidence intervals: ±1 query on 50 queries moves recall by 0.02

## Choosing

Optimize what users experience: for "find the document," MRR and Recall@3 dominate; for "show me a list," precision@k matters more.
