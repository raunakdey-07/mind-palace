---
title: "Note: Testcontainers and Ephemeral Databases for Integration Tests"
date: 2024-05-05
tags: ["testing", "database", "note"]
document_type: "note"
status: Complete
summary: "Running integration tests against real, disposable database instances"
---

# Ephemeral Databases for Integration Tests

The testing pattern Mind Palace's CI uses.

## Why Not Mocks

Mocked database layers test the mock. Real pgvector behavior — extension availability, index usage, array operators, cast syntax — only surfaces against a real server. Several Mind Palace bugs (vector text-format binding, missing extensions) were invisible to mocks by construction.

## Pattern

- Service container per CI run; schema applied via the project's own migrations
- Tests seed their own fixtures and clean them deterministically
- Tests skip cleanly when no database is configured, so local unit runs stay fast

## Hygiene

Fixtures must namespace their data (`test/%` paths) or they leak into shared environments and pollute benchmarks — a failure mode this repository has personally experienced.
