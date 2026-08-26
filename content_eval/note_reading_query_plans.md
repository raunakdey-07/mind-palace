---
title: "Note: Reading Database Query Plans"
date: 2024-04-12
tags: ["database", "postgresql", "performance", "note"]
document_type: "note"
status: Complete
summary: "Interpreting EXPLAIN ANALYZE output: scan types, joins, and the estimates-vs-actuals gap"
---

# Reading Query Plans

The skill behind the query-analyzer project and every retrieval-query optimization.

## Key Reads

- **Scan types**: Seq Scan on large tables usually means a missing index; Bitmap vs Index Scan tradeoffs depend on selectivity
- **Join order and type**: nested loop for small outer relations, hash join for large; a bad row estimate flips the choice wrongly
- **Estimates vs actuals**: large divergence means stale statistics — run ANALYZE before blaming the planner

## Method

Read top-down for structure, bottom-up for cost. The plan's total cost is an estimate; the actual rows and loops are the truth. Optimize the node with the largest actual time, not the one that looks theoretically wrong.
