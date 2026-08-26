---
title: "Note: API Design for ML Services"
date: 2024-04-25
tags: ["api", "mlops", "note"]
document_type: "note"
status: Complete
summary: "Contract design for prediction APIs: errors, versioning, and observability"
---

# API Design for ML Services

Interface lessons from the FastAPI template, model serving, and Mind Palace.

## Error Contracts

Distinguish explicitly: invalid request (4xx), backend dependency down (503), and valid-but-empty (200 with empty payload). Collapsing these forces clients to guess — the single most common ML API design failure.

## Versioning

Version the request/response schema, not just the model. A model swap that changes output distribution is a breaking change for consumers even when the JSON shape is identical.

## Observability

Log per-stage latency and confidence distributions, not just counts. An API that returns confident garbage silently is worse than one that errors loudly.
