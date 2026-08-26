---
title: "Project: Database Query Performance Analyzer"
date: 2024-06-08
tags: ["python", "postgresql", "performance"]
document_type: "project"
status: In Progress
git_repo: "https://github.com/raunakdey-07/query-analyzer"
summary: "EXPLAIN ANALYZE parsing and index recommendation for slow PostgreSQL queries"
---

# Query Performance Analyzer

Tooling built after repeatedly hand-analyzing Mind Palace's retrieval queries.

## Design

- Parse **EXPLAIN (ANALYZE, BUFFERS)** output into a plan tree
- Flag sequential scans on large tables, high estimated-vs-actual row divergence
- Index suggestions from filter/join column analysis
- Before/after timing harness for validating any suggested index

## Status

Plan parsing and seq-scan detection working; the recommender is heuristic and needs validation against real workloads before its suggestions can be trusted blindly.
