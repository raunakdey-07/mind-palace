---
title: "Project: Configuration Validation Library"
date: 2024-05-18
tags: ["python", "pydantic", "devops"]
document_type: "project"
status: Completed
git_repo: "https://github.com/raunakdey-07/config-validation"
summary: "Pydantic-based startup configuration validation with actionable error messages"
---

# Configuration Validation Library

Fail-fast configuration for services — extracted from repeated patterns across projects.

## Design

- Pydantic models per service describing every environment variable
- Validation at startup, not first use — misconfiguration crashes immediately with a named-field error
- Defaults documented in the model; secrets never defaulted
- Type coercion at the boundary (URLs, durations, comma-separated lists)

## Why Startup

A service that starts successfully but fails on first request turns config errors into runtime incidents. Crashing at boot converts them into deployment-time failures, which are cheap.
