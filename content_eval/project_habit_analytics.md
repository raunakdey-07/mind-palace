---
title: "Project: Habit Tracker Analytics"
date: 2024-01-08
tags: ["python", "streamlit", "statistics"]
document_type: "project"
status: Completed
git_repo: "https://github.com/raunakdey-07/habit-analytics"
summary: "Habit completion analytics with streak statistics and trend detection"
---

# Habit Tracker Analytics

Small-data statistics applied to personal habit logs.

## Analysis

- Streak distributions vs naive day-counting (calendar-week-aware streaks)
- Trend detection with rolling logistic regression on completion probability
- Weekday-effect estimation with confidence intervals — most "patterns" were noise at n < 200 days
- Streamlit visualization of completion heatmaps

## Lesson

The confidence-interval discipline from the statistics note earned its keep here: several apparent habits dissolved into noise once intervals were computed. Small-data honesty changes conclusions.
