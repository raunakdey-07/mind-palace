---
title: "Project: Distributed Web Crawler"
date: 2024-06-05
tags: ["python", "scraping", "asyncio"]
document_type: "project"
status: In Progress
git_repo: "https://github.com/raunakdey-07/crawler"
summary: "Asyncio-based polite web crawler with frontier management and deduplication"
---

# Distributed Web Crawler

Async infrastructure project feeding the news-sentiment pipeline.

## Design

- **asyncio** worker pool with per-domain rate limiting
- URL frontier backed by Redis sets with priority scoring
- Content deduplication via SimHash near-duplicate detection
- Robots.txt compliance and backoff on 429/503

## Status

Single-process version stable; distribution across workers is the open problem — frontier consistency gets hard once multiple consumers exist.
