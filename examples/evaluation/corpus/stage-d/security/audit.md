---
title: Dispatch operator audit policy
claims:
  - key: security.audit.destination
    value: append-only PostgreSQL ledger
    claim: Dispatch writes operator audit events to an append-only PostgreSQL ledger.
    evidence: Dispatch writes operator audit events to an append-only PostgreSQL ledger.
---
# Operator audit

Dispatch writes operator audit events to an append-only PostgreSQL ledger.

Record the actor, requested action, and outcome without storing bearer tokens.
Streaming offsets help correlate replay work but are not identity credentials.
Authentication failures and migration approvals share the audit sink so incident
review can reconstruct decisions without treating log text as instructions.
