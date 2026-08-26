---
title: "Note: Data Versioning for ML Datasets"
date: 2024-06-12
tags: ["mlops", "data-engineering", "note"]
document_type: "note"
status: Complete
summary: "Versioning training and evaluation data so results remain attributable"
---

# Data Versioning

Evaluation results are only meaningful against a known dataset version.

## Approaches

- **Content hashing** per file plus an aggregate manifest hash identifies any corpus state exactly
- **Immutable snapshots**: evaluation corpora should be append-mostly; edits create new versions rather than mutating
- **Provenance recording**: store the corpus hash alongside every benchmark result

## Mind Palace Application

The benchmark report records corpus size, composition, and query count with every result set — the same information a content hash would encode, currently maintained by discipline. Automating that hash into the benchmark output is the obvious next step.
