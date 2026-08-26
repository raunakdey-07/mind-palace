---
title: "Project: Home Network Monitor with Packet Analysis"
date: 2024-06-02
tags: ["python", "networking", "monitoring"]
document_type: "project"
status: In Progress
git_repo: "https://github.com/raunakdey-07/network-monitor"
summary: "Passive home network monitoring with device fingerprinting and traffic baselines"
---

# Home Network Monitor

Passive network observability — the infrastructure cousin of the server-metrics anomaly detection.

## Design

- Passive capture via ARP sniffing; no MITM, no injection
- Device fingerprinting from MAC OUI + DHCP fingerprints + mDNS names
- Per-device traffic baselines with the same rolling-quantile approach as the metrics project
- New-device alerts and absent-device detection

## Status

Device inventory stable; traffic classification (streaming vs backup vs browsing) is the open problem — port-based heuristics are increasingly wrong.
