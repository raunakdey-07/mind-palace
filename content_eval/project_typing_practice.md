---
title: "Project: Typing Practice with Error Pattern Analysis"
date: 2024-06-10
tags: ["python", "cli", "statistics"]
document_type: "project"
status: Completed
git_repo: "https://github.com/raunakdey-07/typing-practice"
summary: "Typing trainer that identifies per-key and per-pair error patterns over time"
---

# Typing Practice with Error Analytics

A typing trainer that analyzes *which* keys you miss, not just how fast you type.

## Analysis

- Per-key error rates and common transposition pairs (e.g., 'th' → 'ht')
- Speed/accuracy tradeoff curves per finger
- Drill generation targeting the worst n-grams
- Longitudinal tracking with trend confidence intervals

## Finding

Targeted drills on the top-5 error pairs improved overall accuracy faster than generic practice — measurement-guided practice beats volume, the same principle as failure-driven benchmark improvement.
