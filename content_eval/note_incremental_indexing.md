---
title: "Note: Incremental Indexing and Freshness"
date: 2024-06-24
tags: ["retrieval", "architecture", "note"]
document_type: "note"
status: Complete
summary: "Keeping search indexes fresh: incremental ingestion, deletion handling, and consistency windows"
---

# Incremental Indexing and Freshness

How search systems stay current without full rebuilds.

## Mechanisms

- **Change detection** via content hashes; only reprocess modified documents
- **Deletion propagation**: removed sources must remove index entries — the step most often forgotten
- **Atomic swaps**: rebuild-then-replace avoids serving a half-updated index
- **Consistency window**: document how stale results may be, and make it observable

## Mind Palace Application

Manifest-tracked incremental ingestion covers updates; the manifest is the change log. Deletion of source files is currently manual — a known gap that matters only when sources are actually removed, which the evaluation corpus has not yet exercised.
