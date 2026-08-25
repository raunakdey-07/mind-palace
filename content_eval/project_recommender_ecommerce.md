---
title: "Project: Recommendation Engine for E-commerce"
date: 2024-01-25
tags: ["python", "recommendation", "collaborative-filtering"]
document_type: "project"
status: Completed
git_repo: "https://github.com/raunakdey-07/recsys"
summary: "Hybrid recommender combining collaborative filtering with content embeddings"
---

# Recommendation Engine

Hybrid product recommender for an e-commerce catalog.

## Approach

- **Collaborative filtering** via implicit matrix factorization (ALS) on interaction data
- Content branch: product-text **embeddings** for cold-start items
- Hybrid score = weighted blend tuned on holdout clicks
- Candidate generation / ranking two-stage design for latency

## Results

Recall@20 of 0.34 offline; the content branch rescued ~60% of cold-start recommendations that pure CF missed entirely.
