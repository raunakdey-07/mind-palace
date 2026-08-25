---
title: "Project: FastAPI Microservice Template"
date: 2023-11-15
tags: ["python", "fastapi", "devops"]
document_type: "project"
status: Completed
git_repo: "https://github.com/raunakdey-07/fastapi-template"
summary: "Opinionated FastAPI project template with async SQLAlchemy, Alembic, and CI"
---

# FastAPI Microservice Template

The scaffolding Mind Palace's backend was built from.

## Includes

- **FastAPI** with typed dependency injection
- Async **SQLAlchemy** session management with a context-manager scope helper
- **Alembic** migration setup reading DATABASE_URL from the environment
- Pydantic settings, structured error handlers, Prometheus metrics
- GitHub Actions pipeline: lint, type-check, test against a service database

## Purpose

Standardizes service layout so new ML API projects start from a known-good baseline rather than re-deriving plumbing.
