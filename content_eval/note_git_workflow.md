---
title: "Note: Git Workflow for Research Code"
date: 2023-12-15
tags: ["devops", "git", "note"]
document_type: "note"
status: Complete
summary: "Branching, commit conventions, and review discipline adapted for ML repositories"
---

# Git Workflow for Research Code

Process notes adopted in Mind Palace after a merge incident lost documentation work.

## Practices

- Conventional commits: `feat(scope):`, `fix(scope):`, `docs:` — scopes map to subsystems
- Never force-push shared branches; recovery history matters more than tidy graphs
- Tag releases with annotated tags; keep release notes in the tag message
- Audit merges for silently dropped files — `git diff main@{1}..main --stat` catches regressions

## Research-Specific

Experiment scripts and notebooks get the same review discipline as production code; unreproducible results are worse than no results.
