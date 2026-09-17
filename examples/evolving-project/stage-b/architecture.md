---
title: Dispatch architecture — independent consumers
claims:
  - key: architecture.streaming
    value: Kafka
    claim: Dispatch uses Kafka for event streaming.
    evidence: Dispatch uses Kafka for event streaming.
---
# Dispatch architecture

Dispatch uses Kafka for event streaming.

Billing and fulfilment now consume order events independently. The platform team
runs the brokers, partitions by order ID, and retains events for seven days so a
projection can be rebuilt without depending on the cache. Producers attach an
event ID; consumers deduplicate before applying side effects. The migration
replaced the pilot transport, not the event schema or delivery guarantees.
