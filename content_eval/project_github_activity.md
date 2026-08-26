---
title: "Project: GitHub Activity Analyzer"
date: 2024-03-02
tags: ["python", "api", "streamlit"]
document_type: "project"
status: Completed
git_repo: "https://github.com/raunakdey-07/github-activity"
summary: "Contribution pattern analysis across repositories with language and topic trends"
---

# GitHub Activity Analyzer

Personal analytics over the GitHub API — portfolio introspection tooling.

## Features

- Commit/PR/issue time series per repository with rolling trends
- Language distribution drift over time
- Topic clustering of repository descriptions via embeddings
- Stale-repository detection for cleanup candidates

## Note

The description-clustering feature was the seed of the arXiv search project — grouping by semantic similarity proved more useful than the raw statistics, which redirected attention toward retrieval.
