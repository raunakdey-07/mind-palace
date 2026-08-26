---
title: "Project: Commit Message Linter and Enforcer"
date: 2024-03-28
tags: ["python", "git", "devops"]
document_type: "project"
status: Completed
git_repo: "https://github.com/raunakdey-07/commit-lint"
summary: "Conventional-commit validation as a fast standalone CLI and pre-commit hook"
---

# Commit Message Linter

Enforcing the conventional-commit discipline that the release automation depends on.

## Checks

- Type(scope): subject format validation with configurable allowed types
- Subject length, capitalization, and trailing-period rules
- Merge-commit and revert-format exemptions
- Pre-commit hook mode plus a batch-audit CLI over history

## Rationale

Commit conventions only produce value (changelogs, version bumps) when they're consistent. Manual enforcement fails within weeks; mechanical enforcement lasts. This tool is why the repository's history stays parseable.
