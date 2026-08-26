---
title: "Note: Secrets and Configuration Management"
date: 2024-01-12
tags: ["devops", "security", "note"]
document_type: "note"
status: Complete
summary: "Environment-based configuration, secret hygiene, and local development defaults"
---

# Secrets and Configuration Management

Security notes applied across all services.

## Practices

- All configuration through environment variables with safe local defaults
- Never commit credentials; development defaults must be obviously non-production (e.g. `secret` for a throwaway container database)
- Distinguish configuration by environment explicitly rather than by presence of secrets
- Secret values never appear in logs — error messages included

## Mind Palace Application

The DATABASE_URL default targets a local disposable container, not production. CI injects its own service-container URL. No production credential exists in the repository at all.
