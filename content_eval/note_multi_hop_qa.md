---
title: "Note: Multi-Hop Question Answering"
date: 2024-06-22
tags: ["retrieval", "rag", "note"]
document_type: "note"
status: Complete
summary: "Multi-hop QA requires retrieval chains — the workload class where graphs are actually argued for"
---

# Multi-Hop Question Answering

The question class that motivates graph databases, examined honestly.

## What It Is

"Who directed the film starring the actor born in the city where X happened?" — answering requires chaining facts across documents. Single-shot retrieval fails because no single document contains the answer.

## Approaches Without a Graph Database

- **Iterative retrieval**: answer partially, reformulate, retrieve again
- **Recursive CTEs**: when relationships are explicit relational tables (project→technology), PostgreSQL traverses them natively
- **Document-link exploitation**: citations and references as pre-computed edges

## The Honest Test

Multi-hop capability is justified only when such questions occur in real usage with enough frequency to matter. Building traversal infrastructure for hypothetical questions inverts the evidence-first principle.
