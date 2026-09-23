---
title: Dispatch architecture — Kafka cutover
claims:
  - key: architecture.streaming
    value: Kafka
    claim: Dispatch uses Kafka for event streaming.
    evidence: Dispatch uses Kafka for event streaming.
---
# Dispatch event streaming

Dispatch uses Kafka for event streaming.

Checkout publishes committed outbox events to the orders topic. Consumer groups
update fulfilment projections and commit offsets after the database transaction.
Redis remains a cache, not an event transport. The platform team owns replay;
separate broker storage prevents backfills from evicting cached order summaries.
