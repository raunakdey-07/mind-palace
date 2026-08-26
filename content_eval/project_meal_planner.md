---
title: "Project: Recipe Meal Planner with Pantry Deduction"
date: 2024-07-06
tags: ["python", "streamlit", "optimization"]
document_type: "project"
status: In Progress
git_repo: "https://github.com/raunakdey-07/meal-planner"
summary: "Weekly meal planning against pantry inventory with shopping-list minimization"
---

# Meal Planner

Planning meals against what's already in the pantry — built on the recipe parser and inventory tracker.

## Design

- Weekly plan selection maximizing ingredient overlap with current stock
- Shopping list = union of recipe needs minus pantry deduction, quantity-aware
- Constraint inputs: cooking-time budget per day, ingredient expiry priorities
- Streamlit interface with drag-to-swap meal slots

## Status

The deduction logic works; plan optimization is greedy rather than optimal. Good enough in practice — the exhaustive search was slower and produced plans users liked less.
