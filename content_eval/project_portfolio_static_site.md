---
title: "Project: Static Site Generator for Portfolio"
date: 2023-11-22
tags: ["python", "web", "markdown"]
document_type: "project"
status: Completed
git_repo: "https://github.com/raunakdey-07/portfolio-site"
summary: "Markdown-driven static portfolio site — the original motivation for structured project notes"
---

# Portfolio Static Site

The original portfolio generator — writing the Markdown documents that later became Mind Palace's corpus.

## Design

- Markdown + YAML frontmatter per project, rendered through Jinja templates
- Tags and dates drive index pages and filtering
- Syntax highlighting for embedded code blocks
- Deployed as static files; no server, no database

## Motivation Chain

Static pages felt dead → which motivated semantic search over them → which became Mind Palace. The frontmatter schema used here is the one Mind Palace ingests.
