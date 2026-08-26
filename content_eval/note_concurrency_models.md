---
title: "Note: Concurrency Models Compared"
date: 2024-03-02
tags: ["python", "asyncio", "architecture", "note"]
document_type: "note"
status: Complete
summary: "Threads vs asyncio vs multiprocessing for I/O-bound and CPU-bound ML workloads"
---

# Concurrency Models

Which concurrency model fits which workload — learned across the crawler, dashboard, and serving projects.

## Mapping

- **asyncio**: many concurrent I/O-bound operations (crawlers, API servers, database pools)
- **Threads**: blocking libraries with no async support; GIL released during I/O
- **Multiprocessing/process pools**: CPU-bound work (embedding batches, reranking) that would otherwise block the event loop

## The Classic Bug

Calling a sync, CPU-heavy function inside an async endpoint blocks the entire event loop — every request stalls, not just the caller. Offload to a process pool or accept and document the serialization.

## Database Angle

Async database drivers bind connections to event loops; mixing loop lifetimes (tests, scripts) requires disposing pools between loops.
