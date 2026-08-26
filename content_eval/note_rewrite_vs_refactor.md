---
title: "Note: When to Rewrite Versus Refactor"
date: 2024-07-15
tags: ["process", "architecture", "note"]
document_type: "note"
status: Complete
summary: "A decision framework for the rewrite question, with retrieval-layer examples"
---

# Rewrite or Refactor

The question every aging subsystem eventually raises.

## Refactor When

- Behavior is correct and understood; only structure hurts
- Tests exist and would survive the restructuring
- The interface to other components is stable

## Rewrite When

- The behavior itself is wrong in ways patching has compounded
- No tests exist, so refactoring cannot be verified incrementally
- The domain understanding has changed so much that the original design assumptions are void

## Mind Palace Application

The ingestion service was rewritten (not refactored) because its core assumption — a three-value parser contract — was wrong at the foundation. The retrieval service was refactored because its behavior was correct and tested; only the row-mapping and duplication needed surgery.
