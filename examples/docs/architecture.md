---
title: "Architecture Overview"
date: 2024-07-03
tags: ["architecture"]
document_type: "note"
summary: "High-level architecture of the example product"
---

# Architecture

Three services: an API gateway, a worker pool, and a PostgreSQL database.

## Data Flow

Requests enter through the gateway, enqueue work for the workers, and results persist to PostgreSQL.
