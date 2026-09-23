---
title: Dispatch Kafka configuration — expanded capacity
claims:
  - key: architecture.kafka.partitions
    value: 12
    claim: Dispatch configures the Kafka orders topic with 12 partitions.
    evidence: Dispatch configures the Kafka orders topic with 12 partitions.
---
# Kafka capacity configuration

Dispatch configures the Kafka orders topic with 12 partitions.

The active orders topic has expanded after consumer lag exceeded the replay
headroom target. Tenant identifiers remain partition keys, but producers must
coordinate the mapping change before consumers resume ordering-sensitive work.
The database migration still uses bounded backfills; more partitions do not
justify unbounded concurrent PostgreSQL writes or changes to operator auth.
