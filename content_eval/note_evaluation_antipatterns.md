---
title: "Note: Evaluation Set Construction Anti-Patterns"
date: 2024-06-10
tags: ["evaluation", "rag", "note"]
document_type: "note"
status: Complete
summary: "How evaluation sets silently become invalid: contamination, circular labels, and narrow coverage"
---

# Evaluation Set Construction Anti-Patterns

Written after catching two of these in my own benchmark.

## Anti-Patterns

- **Label contamination**: deriving expected documents from what a strategy returns — the benchmark then measures agreement with itself
- **Title drift**: labels referencing document titles that no longer match the corpus, so every strategy "fails" for bookkeeping reasons
- **Narrow categories**: only queries the system was designed to answer; failure modes stay invisible
- **Tuning on the test set**: adjusting architecture after inspecting per-query failures without holding out fresh queries

## Defenses

Validate labels against corpus metadata mechanically. Keep negative queries. Add categories that target suspected weaknesses rather than strengths. When a label proves wrong, fix it and record why — never quietly.
