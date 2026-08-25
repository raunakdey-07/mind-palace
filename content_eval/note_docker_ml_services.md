---
title: "Note: Docker and Compose for ML Services"
date: 2024-01-18
tags: ["devops", "docker", "note"]
document_type: "note"
status: Complete
summary: "Container patterns for ML services: model loading, health checks, GPU passthrough"
---

# Docker and Compose for ML Services

Operational notes from containerizing the transcription service and Mind Palace stack.

## Patterns

- Load models once at startup, not per request; expose a readiness endpoint that flips after warm-up
- Health checks must distinguish "container up" from "model loaded"
- Pin base images by digest for reproducible builds
- Multi-stage builds keep runtime images free of build toolchains

## Data Services

Database images with required extensions (like pgvector-enabled Postgres) avoid extension-provisioning drift between local and CI environments.
