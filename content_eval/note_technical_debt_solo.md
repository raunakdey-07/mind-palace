---
title: "Note: Managing Technical Debt in Solo Projects"
date: 2024-07-07
tags: ["process", "philosophy", "note"]
document_type: "note"
status: Complete
summary: "Debt prioritization without a team: paying down what blocks the next milestone"
---

# Technical Debt Without a Team

Solo projects need debt triage rules, because nobody else will flag them.

## Triage Rule

Pay down debt only when it blocks a planned milestone or when the cost of touching the code exceeds the cost of fixing it. Everything else gets documented, not fixed — an undocumented mess compounds; a documented one is just deferred work.

## Debt That Compounds

- Broken test infrastructure (every future change is riskier)
- Invalid evaluation benchmarks (every decision made from them is suspect)
- Undocumented invariants (every refactor risks violating them)

## Mind Palace Application

The benchmark-label validation and test-corpus cleanup fixes were mandatory before corpus expansion could begin — both were compounding debt. The legacy weighted-blend retrieval mode is documented non-compounding debt: unused, but harmless until someone touches it.
