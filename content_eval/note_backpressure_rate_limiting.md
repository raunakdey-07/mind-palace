---
title: "Note: Backpressure and Rate Limiting"
date: 2024-06-18
tags: ["architecture", "performance", "note"]
document_type: "note"
status: Complete
summary: "Protecting services from overload: rate limits, queues, and load shedding"
---

# Backpressure and Rate Limiting

What happens after the dashboard project's slow-consumer problem generalizes.

## Mechanisms

- **Rate limiting** at the edge: token bucket per client, not per instance
- **Bounded queues**: when full, shed load rather than grow without limit
- **Timeouts everywhere**: a missing timeout turns slowdowns into outages
- **Circuit breakers**: stop calling a failing dependency; fail fast instead

## ML Services Specifically

Inference latency has heavy tails — p99 can be 50× the median. Capacity planning must use tail latency, and reranking-style variable-cost endpoints need per-request cost estimation before admission.
