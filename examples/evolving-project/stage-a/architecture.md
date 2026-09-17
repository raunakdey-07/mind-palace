---
title: Dispatch architecture — pilot
claims:
  - key: architecture.streaming
    value: Redis Streams
    claim: Dispatch uses Redis Streams for event streaming.
    evidence: Dispatch uses Redis Streams for event streaming.
---
# Dispatch architecture

Dispatch uses Redis Streams for event streaming.

The pilot accepts order events from the checkout API. A worker group updates
fulfilment projections; consumers acknowledge only after committing their work.
The platform team owns the Redis deployment and pending-entry recovery runbook.
This keeps the pilot small, but replay retention shares capacity with the cache.
