---
title: "Project: Weather Data Collector and Forecast Comparator"
date: 2024-05-02
tags: ["python", "time-series", "data-engineering"]
document_type: "project"
status: Completed
git_repo: "https://github.com/raunakdey-07/weather-compare"
summary: "Collecting forecasts from multiple providers and scoring them against observations"
---

# Forecast Comparator

Which weather service is actually most accurate for my location? A data-driven answer.

## Design

- Scheduled collection of forecasts (7 providers) and hourly observations
- Alignment of forecast lead times to observation windows
- Per-provider, per-condition error tracking (temperature, precipitation, wind)
- Streamlit dashboard with rolling accuracy leaderboards

## Findings

Provider rankings flipped by condition and season — no universal winner. Precipitation-timing errors dominated practical dissatisfaction even where temperature accuracy was excellent.
