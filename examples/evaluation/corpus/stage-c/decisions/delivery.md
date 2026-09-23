---
title: Dispatch delivery contract
claims:
  - key: architecture.delivery
    value: at-least-once
    claim: Dispatch delivers order events at least once.
    evidence: Dispatch delivers order events at least once.
---
# Delivery decision

Dispatch delivers order events at least once.

An outbox closes the database-to-streaming dual-write gap. Consumers must tolerate
a replay after a worker loses its lease. The ledger uses the event identifier
for deduplication, not the JWT subject or a Kafka offset. Migration validation
compares business effects rather than raw delivery counts.
