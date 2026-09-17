---
title: On-call authentication runbook
claims:
  - key: security.auth
    value: API key
    claim: Dispatch authenticates operator requests with an API key.
    evidence: Dispatch authenticates operator requests with an API key.
---
# Operator authentication

Dispatch authenticates operator requests with an API key.

The on-call runbook describes a shared credential in the operator gateway.
This assertion deliberately disagrees with the platform contract under the same
structured key. Both documents remain active until their owners resolve the
inconsistency; a newer observation alone must not silently choose a winner.
