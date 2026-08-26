---
title: "Project: Graph-Based Fraud Detection"
date: 2024-06-28
tags: ["python", "graph", "fraud", "neo4j"]
document_type: "project"
status: In Progress
git_repo: "https://github.com/raunakdey-07/fraud-graph"
summary: "Transaction fraud detection using graph features and community detection"
---

# Graph-Based Fraud Detection

The one project where graph structure is genuinely the signal: fraud rings are relationships, not rows.

## Approach

- Transaction graph in **Neo4j**: accounts as nodes, transactions as edges
- Graph features via Cypher: degree, triangle counts, PageRank, community membership (Louvain)
- GNN (GraphSAGE) node classification compared against gradient boosting on graph features
- Temporal edge weighting so stale connections decay

## Status

Graph-feature GBM beats row-only baseline by 6 points AUC; the GNN is not yet beating the GBM. This is the concrete evidence case when evaluating whether Mind Palace ever needs a graph layer.
