---
title: Dispatch operator authentication — identity federation
claims:
  - key: security.auth
    value: OIDC
    claim: Dispatch authenticates operator requests with OIDC access tokens.
    evidence: Dispatch authenticates operator requests with OIDC access tokens.
---
# Operator gateway contract

Dispatch authenticates operator requests with OIDC access tokens.

The gateway now delegates identity to the corporate provider and checks its
issuer, audience, and scoped roles. Locally issued pilot JWT bearer tokens are
no longer accepted. OIDC tokens may themselves use JWT encoding; the authored
mechanism distinguishes the provider contract, not the token serialization.
Migration and replay operations still require explicit operator authorization.
