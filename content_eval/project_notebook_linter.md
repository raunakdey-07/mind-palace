---
title: "Project: Static Analysis of Notebook Quality"
date: 2024-06-14
tags: ["python", "devops", "data-engineering"]
document_type: "project"
status: Completed
git_repo: "https://github.com/raunakdey-07/notebook-lint"
summary: "Linting Jupyter notebooks for execution order, hidden state, and output staleness"
---

# Notebook Quality Linter

Jupyter notebooks fail in ways normal linters cannot see — hidden state and stale outputs.

## Checks

- Execution-count ordering violations (cells run out of order)
- Output staleness: source newer than recorded outputs
- Deterministic-restart verification: run top-to-bottom in a clean kernel, diff outputs
- Undocumented magic numbers and missing markdown between code blocks

## Motivation

Kaggle notebooks taught that notebook hygiene is a real skill. This tool encodes the checklist so it applies to every future notebook automatically.
