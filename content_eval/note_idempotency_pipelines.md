---
title: "Note: Idempotency in Data Pipelines"
date: 2024-06-22
tags: ["data-engineering", "architecture", "note"]
document_type: "note"
status: Complete
summary: "Designing pipeline steps so re-running them is always safe"
---

# Idempotency in Data Pipelines

The property that separates pipelines you can trust from ones you babysit.

## Principle

Every step, run twice with the same input, leaves the same state as run once. Content-hash upserts, MERGE-based writes, and deterministic IDs are the standard toolkit.

## Where It Bites

- Non-deterministic IDs turn re-ingestion into duplication
- Partial failure without transactional boundaries leaves half-written state
- Manifests or logs that claim success before the write completes lie after crashes

## Mind Palace Application

Document IDs derive from path+content hash; ingestion checks the manifest before work; chunk replacement is delete-then-insert per document; the manifest updates only after chunks commit. Re-running ingestion over an unchanged corpus is a no-op by design.
