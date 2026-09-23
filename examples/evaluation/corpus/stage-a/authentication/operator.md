---
title: Dispatch operator authentication — pilot
claims:
  - key: security.auth
    value: JWT
    claim: Dispatch authenticates operator requests with JWT bearer tokens.
    evidence: Dispatch authenticates operator requests with JWT bearer tokens.
---
# Operator gateway contract

Dispatch authenticates operator requests with JWT bearer tokens.

The pilot gateway validates locally issued tokens against a pinned issuer and
Dispatch audience. Operators receive scoped roles; service-to-service transport
uses a separate identity boundary. A database migration must not bypass gateway
authentication merely because consumer lag is rising.
