---
title: "Note: Async Python Patterns with asyncio and SQLAlchemy"
date: 2024-03-08
tags: ["python", "asyncio", "database", "note"]
document_type: note
status: Complete
summary: "Event-loop binding, session scoping, and common asyncpg pitfalls"
---

# Async Python Patterns

Hard-won notes from debugging Mind Palace's database layer.

## Event-Loop Binding

asyncpg connections bind to the creating event loop. Reusing pooled connections across loops raises "another operation is in progress" or "attached to a different loop" — dispose engines between loop lifetimes (tests create one loop per test).

## Session Scoping

Prefer an explicit `async with session_scope() as db:` context manager over generator-based dependency injection outside FastAPI request handling. Iterating a list containing an async generator is not session management.

## Transaction Boundaries

Commit once per logical unit, not per statement. Partial ingestion must roll back cleanly; a manifest row claiming success after a failed write is a correctness bug.
