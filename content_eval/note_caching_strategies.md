---
title: "Note: Caching Strategies for Read-Heavy Services"
date: 2024-03-15
tags: ["performance", "architecture", "note"]
document_type: "note"
status: Complete
summary: "Cache layers, invalidation, and TTL policy for read-heavy API services"
---

# Caching Strategies for Read-Heavy Services

Applied notes from the model-serving and dashboard projects.

## Layers

- **Client/CDN**: for static or slow-changing responses
- **Application LRU**: in-process, zero network cost, lost on restart
- **Shared store (Redis)**: survives deploys, adds a network hop

## Invalidation

Event-driven invalidation beats TTL guessing when writes are observable. For search systems, index updates are the natural invalidation trigger — cache keys should include an index version.

## What Not to Cache

Per-user personalized results with low hit rates waste memory. High-cardinality keys need hit-rate measurement before investing in caching at all.
