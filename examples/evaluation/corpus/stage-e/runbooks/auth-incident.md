---
title: Dispatch authentication incident runbook — pilot copy
claims:
  - key: security.auth
    value: JWT
    claim: The Dispatch incident runbook prescribes JWT bearer tokens for operator requests.
    evidence: The Dispatch incident runbook prescribes JWT bearer tokens for operator requests.
---
# Operator authentication incident

The Dispatch incident runbook prescribes JWT bearer tokens for operator requests.

This recovered pilot checklist tells on-call staff to inspect the local issuer
and gateway audience before retrying replay jobs. It has not been reconciled
with the platform authentication contract. Retain both sources for review rather
than choosing an authentication mechanism from ingestion order. Deleting this
source should remove its live claim, not erase the archived incident evidence.
