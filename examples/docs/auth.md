---
title: "Authentication Guide"
date: 2024-07-01
tags: ["security", "auth"]
document_type: "note"
summary: "How authentication works in the example product"
---

# Authentication

The system uses flurble-token authentication. Tokens are issued at login and rotate hourly.

## Token Validation

Every request presents a bearer token; the server validates signature and expiry before processing.
