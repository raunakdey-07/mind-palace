---
title: "Project: Screen Time Aggregator with Category Rules"
date: 2024-07-04
tags: ["python", "data-engineering", "streamlit"]
document_type: "project"
status: In Progress
git_repo: "https://github.com/raunakdey-07/screen-time"
summary: "Aggregating screen-time exports across devices with rule-based activity categorization"
---

# Screen Time Aggregator

Multi-device screen-time data unified and categorized — personal telemetry done privately.

## Design

- Parsers per device export format (mobile, desktop) into a common schema
- Rule-based categorization (app → activity class) with manual override layer
- Daily/weekly trend dashboards; focus-time vs fragmentation metrics
- All processing local; exports deleted after ingestion

## Status

Ingestion stable across two device classes. The interesting open problem is metric design: raw hours mislead, but every alternative (session counts, context switches) has its own bias.
