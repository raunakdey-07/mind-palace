---
title: "Note: Local-First Software and Data Ownership"
date: 2024-07-04
tags: ["architecture", "philosophy", "note"]
document_type: "note"
status: Complete
summary: "Designing systems that run locally, own their data, and avoid external dependencies"
---

# Local-First Software

The philosophy constraint that shaped every Mind Palace infrastructure decision.

## Properties

- Runs on your hardware; no paid API keys in the reference path
- Data lives in formats you control (Markdown files, an open database)
- Degrades gracefully offline; the network is optional
- Exit cost near zero: your corpus is portable plain text

## Consequences

Model choices narrow to what runs locally (Ollama, sentence-transformers). Scale assumptions stay modest. But the payoff is durability — a local-first knowledge base outlives any vendor's pricing changes.

## The Tradeoff

Local models lag frontier capability. For retrieval-grounded answering over a personal corpus, the gap is smaller than for open-ended generation — which is precisely why RAG is the right architecture for this constraint.
