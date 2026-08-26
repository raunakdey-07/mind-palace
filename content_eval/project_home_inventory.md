---
title: "Project: Inventory Tracker for Home Storage"
date: 2024-06-28
tags: ["python", "cli", "sqlite"]
document_type: "project"
status: In Progress
git_repo: "https://github.com/raunakdey-07/home-inventory"
summary: "Where-is-it database for home storage with location hierarchy and barcode lookup"
---

# Home Inventory Tracker

"Which box is the router in?" — a location-hierarchy database with barcode shortcuts.

## Design

- Location tree (room → shelf → box) with printable QR labels
- Item records with photos, purchase data, and barcode/UPC lookup
- SQLite storage; Typer CLI for fast check-in/check-out at the box
- Search by name, tag, or partial location path

## Status

Core CRUD and label printing work; photo attachment management is the remaining rough edge. A deliberately boring CRUD project — sometimes the useful tool is the simple one.
