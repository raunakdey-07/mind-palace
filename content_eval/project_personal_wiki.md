---
title: "Project: Personal Wiki with Bidirectional Links"
date: 2024-07-02
tags: ["python", "web", "markdown"]
document_type: "project"
status: In Progress
git_repo: "https://github.com/raunakdey-07/personal-wiki"
summary: "Markdown wiki with backlinks, graph view, and full-text search"
---

# Personal Wiki

Self-hosted Markdown wiki — the structured-capture counterpart to Mind Palace's retrieval-first approach.

## Features

- `[[WikiLinks]]` parsing with automatic backlink index
- Graph visualization of link structure
- Full-text search via PostgreSQL tsvector
- Git-backed versioning; plain files on disk

## Relationship to Mind Palace

Deliberately complementary: the wiki handles deliberate linking and curation; Mind Palace handles semantic discovery. Whether the two corpora should merge is an open design question — linked structure might eventually feed relationship-aware retrieval.
