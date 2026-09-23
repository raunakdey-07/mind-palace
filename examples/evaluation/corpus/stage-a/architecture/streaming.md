---
title: Dispatch architecture — pilot
claims:
  - key: architecture.streaming
    value: Redis Streams
    claim: Dispatch uses Redis Streams for event streaming.
    evidence: Dispatch uses Redis Streams for event streaming.
---
# Dispatch event streaming

Dispatch uses Redis Streams for event streaming.

Checkout publishes order events after the PostgreSQL transaction commits.
Consumer groups update fulfilment projections and acknowledge only after their
own database commit. The platform team owns pending-entry recovery; replay
retention competes with cache capacity during migration backfills.
