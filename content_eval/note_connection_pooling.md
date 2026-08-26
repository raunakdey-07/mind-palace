---
title: "Note: Database Connection Pooling"
date: 2024-02-18
tags: ["database", "postgresql", "performance", "note"]
document_type: "note"
status: Complete
summary: "Pool sizing, lifetime management, and event-loop interaction for async database access"
---

# Connection Pooling

Operational notes on SQLAlchemy async engine pools.

## Sizing

Pool size relative to concurrent requests, not CPU count. Overflow + pool exhaustion produces queueing that looks like random latency spikes; measure checkout time.

## Lifetime

Recycle connections before server-side idle timeouts kill them silently. Stale connections manifest as sporadic first-request failures after quiet periods.

## Event-Loop Interaction

Async pools bind connections to the creating loop — the source of the "attached to a different loop" failures in test environments. Dispose engines between loop lifetimes; never share an engine across independently created loops.
