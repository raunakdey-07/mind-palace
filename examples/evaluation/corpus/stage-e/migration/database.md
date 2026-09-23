---
title: Dispatch database migration
claims:
  - key: migration.database.strategy
    value: expand-contract
    claim: Dispatch migrates PostgreSQL schemas with expand-contract changes.
    evidence: Dispatch migrates PostgreSQL schemas with expand-contract changes.
---
# Database rollout

Dispatch migrates PostgreSQL schemas with expand-contract changes.

Add nullable fields first, deploy compatible writers, backfill in bounded batches,
and validate before removing old fields. Replay workers can run older code while
Kafka traffic is cut over. Rollback restores application routing, not a destructive
reverse migration that would erase order history or operator audit records.
