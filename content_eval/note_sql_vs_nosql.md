---
title: "Note: Choosing Between SQL and NoSQL Honestly"
date: 2024-02-25
tags: ["database", "architecture", "note"]
document_type: "note"
status: Complete
summary: "A decision framework for database selection based on access patterns, not fashion"
---

# Choosing Between SQL and NoSQL Honestly

The general framework behind the pgvector and Neo4j decisions.

## The Questions That Matter

1. What are the actual access patterns — not imagined ones?
2. What consistency guarantees does the workload need?
3. What is the operational cost of one more stateful system?
4. Can the existing database do it at acceptable performance?

## Common Failure

Choosing a database for a hypothetical workload. The graph-database question is the canonical example: teams adopt Neo4j for "relationship queries" that turn out to be two joins. Adopt special-purpose storage only after a measured workload proves the general-purpose one insufficient.

## Corollary

Every additional datastore adds a synchronization problem. Two sources of truth means eventual inconsistency somewhere — decide in advance which reads may be stale.
