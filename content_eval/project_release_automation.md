---
title: "Project: Semantic Versioning and Release Automation"
date: 2024-03-05
tags: ["devops", "git", "release"]
document_type: "project"
status: Completed
git_repo: "https://github.com/raunakdey-07/release-automation"
summary: "Changelog generation and tagged releases from conventional commits"
---

# Release Automation

Tooling project behind the git workflow conventions used everywhere else.

## Features

- Conventional-commit parsing drives semantic version bumps (feat → minor, fix → patch)
- Changelog generated per release from commit history
- Annotated tags carry release notes; GitHub releases created from tags
- CI gates releases on the full test suite — never tag red code

## Rationale

Manual changelogs rot; commit-message discipline scales. The rule that releases only happen from green main branches exists because cutting a release to "fix" a failing pipeline always makes it worse.
