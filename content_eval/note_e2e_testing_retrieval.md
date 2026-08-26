---
title: "Note: End-to-End Testing Strategy for Retrieval Systems"
date: 2024-07-18
tags: ["testing", "retrieval", "note"]
document_type: "note"
status: Complete
summary: "Testing retrieval systems at three levels — unit, integration, and full-pipeline"
---

# End-to-End Testing for Retrieval

Retrieval systems need three distinct test layers; collapsing them hides bugs.

## The Three Layers

- **Unit**: metrics math, chunking logic, score mapping — no database, fast, exhaustive edge cases
- **Integration**: real PostgreSQL, seeded fixtures — verifies SQL, extensions, filters, ordering
- **End-to-end**: markdown file in → parsed → ingested → retrieved → grounded answer with sources out — verifies the seams between stages

## What Each Catches

Unit catches arithmetic and contract bugs. Integration catches driver, schema, and extension issues (the vector-binding class). End-to-end catches *composition* failures — stages that pass individually but disagree at their boundaries.

## The Seams Matter Most

Every major Mind Palace bug lived at a seam: parser-to-ingestion contract, service-to-database session handling, SQL-to-driver parameter binding. Layered testing exists to localize which seam broke.
