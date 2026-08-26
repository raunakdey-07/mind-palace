---
title: "Project: Real-Time Dashboard with WebSockets"
date: 2024-02-08
tags: ["python", "fastapi", "websockets"]
document_type: "project"
status: Completed
git_repo: "https://github.com/raunakdey-07/realtime-dashboard"
summary: "Live metrics dashboard streaming over WebSockets with backpressure handling"
---

# Real-Time Metrics Dashboard

WebSocket streaming for the anomaly-detection alerts — the visualization half of that project.

## Design

- **FastAPI** WebSocket endpoint with per-client subscription filters
- Server-side broadcast fan-out with slow-consumer disconnect (backpressure)
- Client-side ring buffer to keep rendering independent of burst arrival
- Reconnect with sequence-gap detection and catch-up replay

## Lesson

Backpressure is a server responsibility. Dropping frames client-side after they traverse the network wastes the exact resource you're trying to protect.
