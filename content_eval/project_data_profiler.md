---
title: "Project: Command-Line Data Profiler"
date: 2024-04-08
tags: ["python", "cli", "data-engineering"]
document_type: "project"
status: Completed
git_repo: "https://github.com/raunakdey-07/data-profiler"
summary: "Fast CSV/Parquet profiling from the terminal with type inference and anomaly flags"
---

# CLI Data Profiler

First-look tool for unfamiliar datasets — the data-engineering equivalent of `mindpalace doctor`.

## Features

- Column-level profiles: types, null rates, cardinality, distributions
- Anomaly flags: mixed-type columns, suspicious constant columns, duplicate rows
- Cross-column checks: functional dependencies, near-duplicate columns
- Output as terminal tables or JSON for pipeline integration

## Design Note

Built as a Typer CLI with plain-text markers — the second project (after the task manager) that established the CLI conventions Mind Palace follows.
