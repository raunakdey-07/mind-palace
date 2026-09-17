---
title: Platform authentication contract
claims:
  - key: security.auth
    value: OIDC
    claim: Dispatch authenticates operator requests with OIDC access tokens.
    evidence: Dispatch authenticates operator requests with OIDC access tokens.
---
# Operator authentication

Dispatch authenticates operator requests with OIDC access tokens.

The platform contract describes the operator gateway. Tokens carry an audience
for Dispatch and short-lived operator roles. The security team owns this document.
The incident runbook has not yet been reconciled with this contract; do not infer
which source is authoritative from ingestion order.
