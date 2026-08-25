---
title: "Note: SQL Window Functions for Analytics"
date: 2023-10-05
tags: ["sql", "database", "note"]
document_type: "note"
status: Complete
summary: "ROW_NUMBER, RANK, and moving aggregates — the window functions used in RRF ranking"
---

# SQL Window Functions for Analytics

Reference for the window-function patterns the retrieval layer depends on.

## Functions

- **ROW_NUMBER()**: unique sequential ordering — used to build per-signal rank lists in RRF
- **RANK()/DENSE_RANK()**: tie-aware rankings
- Moving aggregates: rolling averages/sums over partition windows

## Patterns

Ranking within partitions (`PARTITION BY` + `ORDER BY`) turns any score column into a rank column without application-side sorting. CTEs compose these cleanly: one CTE per signal, join on entity id, fuse by rank.

## Gotcha

Column order in a SELECT is positional for drivers mapping tuples by index — reordering columns silently breaks index-based row access.
