---
title: "Project: Terminal File Manager with Preview"
date: 2023-12-28
tags: ["python", "cli"]
document_type: "project"
status: Completed
git_repo: "https://github.com/raunakdey-07/tui-file-manager"
summary: "Keyboard-driven terminal file manager with instant text and image previews"
---

# Terminal File Manager

A TUI file manager — the project that taught terminal rendering discipline later applied to CLI output everywhere.

## Features

- Vim-key navigation with multi-select operations
- Instant preview pane: syntax-highlighted text, image protocol support where available
- Fuzzy find over the visible subtree
- Bulk rename with pattern editing

## Design Note

Preview latency was the entire UX: anything over ~50 ms made browsing feel broken. The caching and lazy-render strategies developed here reappear in every interactive tool since.
