---
title: "Project: Web Scraper for Market News Sentiment"
date: 2024-04-28
tags: ["python", "nlp", "finance", "scraping"]
document_type: "project"
status: In Progress
git_repo: "https://github.com/raunakdey-07/news-sentiment"
summary: "Scraping financial news and scoring sentiment as a signal complement to price data"
---

# Market News Sentiment Scraper

NLP pipeline over financial news — sits between the finance projects and the NLP work.

## Pipeline

- Scheduled scraping of headlines via RSS + requests with polite rate limits
- Deduplication by URL canonicalization and title similarity
- FinBERT sentiment scoring per headline
- Aggregation into daily per-ticker sentiment series for correlation with returns

## Status

Collection and scoring working; the sentiment-return correlation analysis is inconclusive so far.
