---
title: Dispatch replay ownership
owner: Platform
claims:
  - key: operations.replay.owner
    value: Platform
    claim: The Platform team owns Dispatch replay recovery.
    evidence: The Platform team owns Dispatch replay recovery.
---
# Replay ownership

The Platform team owns Dispatch replay recovery.

The on-call engineer checks consumer lag, database saturation, and operator
authentication before replaying a partition. Escalate reconciliation mismatches
to the data team. Keep the event identifier in the incident notes so a repeated
request can be distinguished from a duplicate business effect.
