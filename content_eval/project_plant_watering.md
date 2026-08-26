---
title: "Project: Plant Watering Scheduler with Sensor Feedback"
date: 2024-06-22
tags: ["python", "monitoring", "hardware"]
document_type: "project"
status: In Progress
git_repo: "https://github.com/raunakdey-07/plant-watering"
summary: "Soil moisture sensing with per-plant watering schedules and history tracking"
---

# Plant Watering Scheduler

Small hardware-adjacent project: moisture sensors driving watering recommendations.

## Design

- Capacitive soil moisture sensors on an ESP32, MQTT to a collector
- Per-plant calibration curves (sensor reading ↔ actual dryness)
- Watering schedule suggestions from moisture draw-down rate, not fixed intervals
- History dashboard with per-plant trends

## Status

Collection and dashboards working; the calibration curves drift as sensors age — recalibration cadence is the open operational question.
