---
title: "Project: Flashcard Scheduler with Spaced Repetition"
date: 2024-01-20
tags: ["python", "algorithms", "streamlit"]
document_type: "project"
status: Completed
git_repo: "https://github.com/raunakdey-07/srs"
summary: "Spaced repetition scheduling with SM-2 variant and retention analytics"
---

# Spaced Repetition Scheduler

An SRS implementation with analytics — the applied version of the habit-analytics statistical discipline.

## Algorithm

- **SM-2** variant: ease factor and interval per card, modified by response latency
- Retention-targeted scheduling: intervals adjusted to hit a measured recall probability
- Per-card difficulty estimation from historical grades, with Bayesian shrinkage for new cards

## Analytics

Measured retention against predicted recall — the scheduler's own calibration curve. Miscalibration in the first month was systematic and correctable; the analytics view is what made that visible.
