---
title: "Note: Long-Term Maintenance of Personal ML Systems"
date: 2024-07-08
tags: ["mlops", "philosophy", "note"]
document_type: "note"
status: Complete
summary: "Keeping ML systems alive for years: dependency decay, model drift, and the maintenance mindset"
---

# Maintaining Personal ML Systems

Most personal ML projects die within a year. The ones that survive share habits.

## Decay Sources

- **Dependency rot**: unpinned environments break on OS or Python upgrades
- **Model drift**: local LLM/model versions change behavior silently
- **Data growth**: designs fine at 50 documents break at 5,000
- **Motivation decay**: without external users, only genuine utility sustains maintenance

## Survival Habits

One-command setup (documented, tested). Benchmarks that detect silent regressions. Data in portable formats. And building for your own daily use — utility is the only sustainable maintenance motivation.

## Mind Palace Application

Every architectural decision so far — pgvector over a dedicated vector DB, Ollama over paid APIs, Markdown corpus over proprietary storage — optimizes for five-year survivability over peak capability.
