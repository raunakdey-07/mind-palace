---
title: "Note: Writing Reproducible Benchmarks"
date: 2024-06-20
tags: ["evaluation", "performance", "note"]
document_type: "note"
status: Complete
summary: "Benchmark methodology: warm-up, repetition, environment control, and honest reporting"
---

# Writing Reproducible Benchmarks

Methodology notes behind the latency benchmarking phases.

## Protocol

- **Warm up** every model and connection pool before timing; first-query latency is cold-start noise
- Repeat measurements; report median and p95, not just mean — tails tell the real story
- Pin the environment: same machine class, same data location, no concurrent load
- Separate cold-start from steady-state explicitly rather than averaging across them

## Honoring Uncertainty

Report the configuration alongside numbers — corpus size, k values, hardware class. A benchmark without its configuration is an anecdote. When differences between configurations are smaller than run-to-run variance, say so instead of declaring a winner.
