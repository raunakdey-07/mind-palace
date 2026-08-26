---
title: "Project: Personal Finance Dashboard Aggregation"
date: 2023-12-18
tags: ["python", "finance", "streamlit"]
document_type: "project"
status: Completed
git_repo: "https://github.com/raunakdey-07/finance-dashboard"
summary: "Unified net-worth dashboard aggregating bank, brokerage, and crypto accounts"
---

# Personal Finance Dashboard

Aggregation layer sitting above Finalysis and the crypto tracker.

## Design

- Account connectors per institution with normalized transaction schema
- Net-worth time series joining all accounts by date
- Category tagging of transactions with rule-based + manual override layers
- Streamlit multi-page app: overview, trends, per-account drilldown

## Note

Read-only aggregation; execution and optimization remain in the dedicated tools. Keeping concerns separated made each component simpler.
