---
title: "Project: Terminal Dashboard for System Vitals"
date: 2023-11-30
tags: ["python", "cli", "monitoring"]
document_type: "project"
status: Completed
git_repo: "https://github.com/raunakdey-07/vitals-tui"
summary: "Live TUI dashboard for CPU, memory, disk, and network with configurable alerts"
---

# System Vitals TUI

A live terminal dashboard — the TUI rendering project between the file manager and the realtime dashboard.

## Features

- Textual-based layout with resizable panels
- Per-metric sparklines over rolling windows
- Threshold coloring and optional desktop notifications
- Plugin interface for custom metric sources

## Design Note

The alert-threshold design (warn/crit bands with hysteresis to prevent flapping) was reused directly in the anomaly-detection and network-monitor projects. Small tools teach patterns that big ones need.
