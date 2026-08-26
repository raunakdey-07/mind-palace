---
title: "Note: Structured Logging for Debugging Pipelines"
date: 2024-03-25
tags: ["devops", "observability", "note"]
document_type: "note"
status: Complete
summary: "Structured log design that makes pipeline failures diagnosable after the fact"
---

# Structured Logging

Why print statements fail at pipeline scale.

## Design

- JSON logs with stable field names: stage, document_id, duration_ms, outcome
- One event per logical step; include the identifiers needed to reconstruct the run
- Log outcomes, not prose — "ingested 13 chunks" beats "processing file..."
- Correlation IDs thread a single document through ingestion → chunking → indexing

## Mind Palace Application

Ingestion failures during corpus expansion were diagnosed from logs alone because each failed file logged its path and exception type. The alternative — re-running with breakpoints — does not scale to 200 documents.
