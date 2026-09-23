---
title: Dispatch Kafka target configuration
claims:
  - key: architecture.kafka.partitions
    value: 6
    claim: Dispatch configures the Kafka orders topic with 6 partitions.
    evidence: Dispatch configures the Kafka orders topic with 6 partitions.
---
# Kafka target configuration

Dispatch configures the Kafka orders topic with 6 partitions.

This is the provisioned migration target, not the active event transport in the
pilot. Partition keys are tenant identifiers. The migration rehearsal measures
consumer lag and PostgreSQL write pressure before traffic moves to this topic.
Retention and partition sizing are separate controls; neither changes auth.
