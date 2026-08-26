---
title: "Project: Travel Itinerary Optimizer"
date: 2024-05-16
tags: ["python", "optimization", "streamlit"]
document_type: "project"
status: Completed
git_repo: "https://github.com/raunakdey-07/itinerary"
summary: "Multi-city trip routing with constraint optimization over points of interest"
---

# Travel Itinerary Optimizer

Constraint optimization for trip planning — the traveling-salesman problem with opening hours.

## Design

- Points of interest scored by preference weights
- Route optimization via **OR-Tools** with time windows (opening hours) and visit durations
- Day-clustering by geographic proximity before route solving
- Streamlit map visualization with editable itineraries

## Note

Decomposing into cluster-then-route made the optimization tractable and the results explainable — global optimal tours were neither necessary nor comprehensible.
