---
title: Dispatch order storage
claims:
  - key: data.primary
    value: PostgreSQL
    claim: Dispatch stores authoritative order records in PostgreSQL.
    evidence:
      - Dispatch stores authoritative order records in PostgreSQL.
      - Reconciliation reads the PostgreSQL order ledger as the source of truth.
---
# Order storage boundary

Dispatch stores authoritative order records in PostgreSQL.

Redis and Kafka carry events, not the authoritative order ledger. A projection
can be rebuilt from the ledger and retained events. The migration worker uses a
read-only role for reconciliation and reports missing event identifiers without
writing speculative repairs into the source database.

## Reconciliation authority

Dispatch stores authoritative order records in PostgreSQL.

Reconciliation reads the PostgreSQL order ledger as the source of truth.
Replay projections are checked against these committed records before recovery
can resume; a streaming offset alone cannot establish an authoritative order.
