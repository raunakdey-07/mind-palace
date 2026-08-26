---
title: "Project: Home Assistant Energy Dashboard"
date: 2024-06-25
tags: ["python", "monitoring", "streamlit"]
document_type: "project"
status: In Progress
git_repo: "https://github.com/raunakdey-07/energy-dashboard"
summary: "Home energy consumption analytics with appliance-level disaggregation experiments"
---

# Home Energy Dashboard

Energy monitoring with a disaggregation research angle.

## Design

- Ingests smart-plug and main-meter readings into PostgreSQL
- Whole-home vs sum-of-plugs gap analysis (the untracked load)
- Per-appliance consumption trends and cost attribution
- NILM disaggregation experiments to split the untracked remainder

## Status

Monitoring complete; disaggregation accuracy is the research frontier — currently ~70% of untracked load attributable, which is useful for direction but not for billing-grade attribution.
