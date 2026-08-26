---
title: "Note: Synthetic Data for Evaluation Sets"
date: 2024-06-18
tags: ["evaluation", "data-engineering", "note"]
document_type: "note"
status: Complete
summary: "Using LLM-generated queries to expand benchmarks — and the biases that introduces"
---

# Synthetic Evaluation Queries

LLM-generated benchmark queries: scalable, but with known failure modes.

## The Approach

Feed each corpus document to an LLM; ask for questions a user might ask that the document answers. This scales label creation far beyond manual writing.

## Biases Introduced

- **Lexical echo**: generated questions reuse document vocabulary, inflating lexical-retrieval scores relative to real users
- **Coverage skew**: LLMs write questions about salient content, under-testing section-buried answers
- **Difficulty compression**: generated questions cluster at medium difficulty

## Mitigations

Mix synthetic and hand-written queries; validate synthetic labels by checking answer presence in the source; keep the synthetic fraction recorded in the benchmark metadata so results are interpretable.
