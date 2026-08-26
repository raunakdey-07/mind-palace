---
title: "Note: Reproducible Environments with Lock Files"
date: 2024-02-12
tags: ["devops", "python", "note"]
document_type: "note"
status: Complete
summary: "requirements.txt versus lock files, and pinning strategy for reproducible ML environments"
---

# Reproducible Environments

Why "it works on my machine" persists and how to kill it.

## Pinning Levels

- **Lower bound only** (`>=`): flexible, non-reproducible — fine for libraries, wrong for applications
- **Exact pins** (`==`) in requirements.txt: reproducible installs, no transitive guarantees
- **Full lock files** (uv/pip-tools/conda-lock): entire dependency tree pinned; the standard for deployed services

## Beyond Packages

Reproducibility also needs: base image digests, model versions recorded alongside artifacts, data hashes for training sets, and seeds logged per run. A locked requirements file alone does not make ML reproducible.
