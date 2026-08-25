---
title: "Project: CLI Task Manager with SQLite"
date: 2023-05-20
tags: ["python", "cli", "sqlite"]
document_type: "project"
status: Completed
git_repo: "https://github.com/raunakdey-07/task-cli"
summary: "Terminal task manager in Typer with SQLite persistence — a small tool, kept for reference"
---

# CLI Task Manager

Small Typer-based task tracker — the project where the CLI patterns later used by Mind Palace were developed.

## Features

- Add/complete/list tasks with priorities and due dates
- **SQLite** storage via plain SQL, no ORM
- Plain-text output markers like [OK] and [ERROR] instead of decorative symbols
- Single-binary packaging with Poetry

## Note

Kept as a reference for minimal Typer structure; Mind Palace's CLI is its direct descendant.
