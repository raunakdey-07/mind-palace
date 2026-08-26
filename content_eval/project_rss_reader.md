---
title: "Project: RSS Reader with Content Extraction"
date: 2024-06-18
tags: ["python", "data-engineering", "nlp"]
document_type: "project"
status: Completed
git_repo: "https://github.com/raunakdey-07/rss-reader"
summary: "Minimal RSS reader with full-text extraction and read-time estimation"
---

# RSS Reader with Full-Text Extraction

A reading pipeline: feeds in, clean readable text out.

## Design

- Feed polling with per-feed intervals based on update frequency
- Readability-style main-content extraction from article pages
- Read-time estimation and tagging by source-defined categories
- Deduplication across feeds covering the same stories

## Connection

This is the ingestion front-end that could feed Mind Palace — external content flowing through the same parse → chunk → embed pipeline as the local corpus. Integration remains a design question rather than an implemented feature.
