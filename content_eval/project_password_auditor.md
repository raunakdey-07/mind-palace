---
title: "Project: Password Strength Auditor"
date: 2024-01-05
tags: ["python", "security", "cli"]
document_type: "project"
status: Completed
git_repo: "https://github.com/raunakdey-07/password-audit"
summary: "Offline password policy auditing against breach corpora without exposing secrets"
---

# Password Strength Auditor

Security hygiene tooling — audit strength patterns without ever storing actual passwords.

## Design

- Works on salted hashes where possible; plaintext never persisted
- Pattern analysis: length distribution, reuse across accounts, dictionary membership
- Breach-corpus k-anonymity range queries (HIBP style) — full password never leaves the machine
- Report aggregates only; per-account detail requires explicit flag

## Principle

Security tools must not become the vulnerability they audit. The design constraint that the tool's own storage and logs remain safe shaped every decision.
