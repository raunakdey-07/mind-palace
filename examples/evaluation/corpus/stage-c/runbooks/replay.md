---
title: Dispatch replay recovery runbook
claims:
  - key: operations.replay.owner
    value: Platform
    claim: Dispatch replay recovery is owned by the Platform team.
    evidence: Dispatch replay recovery is owned by the Platform team.
---
# Replay recovery

Dispatch replay recovery is owned by the Platform team.

Pause the affected consumer, compare its checkpoint with committed PostgreSQL
records, and resume from a verified event boundary. Recheck lag before increasing
concurrency. The streaming architecture document selects the transport; this
runbook does not infer whether Redis Streams or Kafka is currently active.
