---
title: Dispatch architecture — managed operations
claims:
  - key: architecture.streaming
    value: Kafka managed
    claim: Dispatch uses managed Kafka for event streaming.
    evidence: Dispatch uses managed Kafka for event streaming.
---
# Dispatch architecture

Dispatch uses managed Kafka for event streaming.

Broker patching and replacement move to a managed service after an on-call review.
The application team still owns partition keys, schema compatibility, consumer
lag alerts, and replay procedures. Migration preserves the order-event schema and
seven-day retention. Managed infrastructure does not make consumers exactly-once;
event IDs and idempotent writes remain application responsibilities.
