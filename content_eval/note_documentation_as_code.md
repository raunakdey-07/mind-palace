---
title: "Note: Documentation as Code with Doc Tests"
date: 2024-02-20
tags: ["devops", "documentation", "note"]
document_type: "note"
status: Complete
summary: "Keeping documentation honest by testing examples and validating claims"
---

# Documentation as Code

Documentation rots silently because nothing fails when it becomes wrong.

## Practices

- Code examples in docs should be executable tests (doctest or extracted snippets)
- Numeric claims in documentation ("99% accurate", "under 100ms") should reference the artifact that measured them
- README badges for CI and coverage make staleness visible
- Architecture decisions recorded next to the code they govern, not in a separate wiki that drifts

## Mind Palace Application

The evaluation report lives beside the benchmark it describes, and every number in it is reproducible from a documented command. When a number cannot be regenerated, it is marked superseded rather than quietly left in place.
