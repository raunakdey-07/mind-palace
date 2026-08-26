---
title: "Project: Personal Search Bar for Bookmarks"
date: 2024-06-30
tags: ["python", "search", "cli"]
document_type: "project"
status: Completed
git_repo: "https://github.com/raunakdey-07/bookmark-search"
summary: "Fuzzy and semantic search over browser bookmarks from the terminal"
---

# Bookmark Semantic Search

The smallest retrieval product: search 3,000 bookmarks by meaning, not folder.

## Design

- Bookmark export parsed to (title, URL, folder path) records
- **rapidfuzz** fuzzy matching for exact-ish queries; sentence embeddings for intent queries
- Terminal UI with instant-as-you-type filtering on the fuzzy branch
- Semantic branch queried only on demand (embedding latency)

## Finding

90% of real queries were fuzzy-lexical. The semantic branch earned its keep on the remaining 10% — a microcosm of the hybrid-retrieval argument.
