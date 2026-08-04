# Technical Decisions

Version: 0.1.0

Status: Draft

Related Documents

- 00_PROJECT_FOUNDATION.md

---

# Purpose

This document records the major engineering decisions made during the design of Mind Palace.

Technology choices should never be based on popularity alone.

Every selected technology must satisfy the project's design principles.

Future contributors should consult this document before introducing new frameworks or replacing existing components.

Whenever a major architectural decision changes, a new Architecture Decision Record (ADR) should be added instead of modifying historical decisions.

---

# Decision Process

Every technical decision is evaluated using the following criteria.

1. Long-term maintainability

2. Local-first compatibility

3. Open-source ecosystem

4. Production readiness

5. Community support

6. Developer experience

7. Resume relevance

8. Future scalability

No technology should be introduced solely because it is currently popular.

---

# ADR-001

## Monorepo Architecture

Status

Accepted

---

Decision

Mind Palace will be developed as a monorepo.

---

Repository Structure

apps/

packages/

services/

content/

docs/

infrastructure/

scripts/

---

Why?

The project consists of multiple independently deployable systems.

Portfolio

API

Workers

AI

CLI

Shared UI

Shared Types

Shared Utilities

Keeping these inside separate repositories would introduce unnecessary complexity.

---

Alternatives

Multiple repositories

Rejected

Reason

Duplicated tooling

Harder dependency management

Cross-repository versioning

Poor developer experience

---

Benefits

Single CI pipeline

Shared TypeScript types

Shared configuration

Simpler dependency updates

Atomic commits

Single documentation source

---

Tradeoffs

Repository becomes larger

Longer install times

Requires workspace tooling

---

Decision

Accepted

---

# ADR-002

## Package Manager

Decision

pnpm

---

Alternatives

npm

Yarn

Bun

---

Reason

Workspace support

Disk efficiency

Fast installation

Deterministic lockfile

Excellent monorepo support

---

Rejected

npm

Poor workspace ergonomics

Higher storage usage

---

Yarn

No significant advantages over pnpm

---

Bun

Still evolving

Python ecosystem remains primary backend

---

Decision

Accepted

---

# ADR-003

## Frontend Framework

Decision

Next.js

---

Alternatives

Astro

SvelteKit

Remix

Nuxt

---

Why Next.js

Production maturity

Excellent React ecosystem

MDX support

Server Components

Streaming

Large ecosystem

Easy Vercel deployment

Excellent SEO

---

Why not Astro?

Astro excels for content websites.

Mind Palace is an application.

It contains

Search

Authentication

Streaming AI

Dynamic APIs

Interactive visualizations

Background updates

These align better with Next.js.

---

Tradeoffs

Slightly heavier

More complexity

Higher learning curve

---

Decision

Accepted

---

# ADR-004

## Backend Framework

Decision

FastAPI

---

Alternatives

Flask

Django

Express

NestJS

Go

Rust

---

Reason

Native Python ecosystem

Excellent async support

Automatic OpenAPI

Strong typing

Pydantic validation

AI libraries already Python-first

---

Why not Flask?

Too minimal

Requires many additional decisions

---

Why not Django?

Excellent framework

But unnecessary for this architecture

Mind Palace is API-first

not template-first

---

Decision

Accepted

---

# ADR-005

## Database

Decision

PostgreSQL

Extensions

pgvector

uuid-ossp

pg_trgm

---

Alternatives

SQLite

MongoDB

Supabase

Qdrant only

Milvus only

---

Reason

Production ready

Excellent indexing

Relational integrity

Native vector support

Strong migration tooling

Rich ecosystem

ACID compliance

---

Why not SQLite?

Development only

Poor concurrency

No native scaling

---

Why not MongoDB?

Knowledge is relational.

Projects

Documents

Tags

Embeddings

Agents

Sources

Users

Relationships

fit naturally inside relational models.

---

Why pgvector?

Keeps vectors beside metadata.

Reduces operational complexity.

Avoids synchronization issues.

---

Decision

Accepted

---

# ADR-006

## Cache

Decision

Redis

---

Purpose

Caching

Queues

Rate limiting

Session storage

Temporary memory

Streaming

---

Alternatives

Memcached

RabbitMQ

None

---

Reason

Single dependency

Mature ecosystem

Works well with FastAPI

Supports pub/sub

Supports streams

---

Decision

Accepted

---

# ADR-007

## AI Orchestration

Decision

LangGraph

---

Alternatives

LangChain Agents

CrewAI

AutoGen

Haystack

Semantic Kernel

---

Reason

Explicit execution graph

Deterministic

Checkpointing

State persistence

Human in the loop

Debuggable

Production oriented

---

Why not CrewAI?

Excellent experimentation tool.

Mind Palace requires deterministic workflows.

---

Why not LangChain Agents?

Too implicit.

Harder to debug.

---

Decision

Accepted

---

# ADR-008

## LLM Runtime

Decision

Ollama

---

Alternatives

vLLM

LM Studio

OpenAI

Anthropic

OpenRouter

---

Reason

Runs locally

Simple installation

Growing model library

Open source

API compatible

Works offline

Excellent developer experience

---

Cloud APIs

Remain optional providers.

Never required.

---

Decision

Accepted

---

# ADR-009

## Embedding Models

Current Recommendation

BAAI BGE-M3

---

Alternatives

Nomic Embed

E5 Large

Jina Embeddings

Sentence Transformers

OpenAI

---

Reason

Strong multilingual performance

Excellent retrieval quality

Open source

Good benchmark performance

---

Future

Embedding models remain configurable.

The application should never assume one model.

---

Decision

Accepted

---

# ADR-010

## Search Strategy

Decision

Hybrid Retrieval

---

Pipeline

Full Text Search

+

Vector Search

+

Metadata Filtering

+

Reranking

---

Reason

Vector search alone is insufficient.

Keyword search alone is insufficient.

Hybrid search consistently produces better retrieval quality.

---

Decision

Accepted

---

# ADR-011

## Content Format

Decision

Markdown + MDX

---

Reason

Human readable

Git friendly

Diff friendly

Portable

Easy version control

Static generation

Supports React components

---

Content should never be stored directly inside the database.

Database stores

Metadata

Relationships

Embeddings

Search indexes

Content remains filesystem-based.

---

Decision

Accepted

---

# ADR-012

## Background Processing

Decision

Celery + Redis

---

Responsibilities

Embedding generation

Document parsing

Summaries

Metadata extraction

Index rebuilding

Knowledge graph updates

---

Future

Temporal may replace Celery if workflow complexity increases.

Architecture should isolate task scheduling.

---

Decision

Accepted

---

# ADR-013

## Containerization

Decision

Docker

---

Reason

Development parity

Deployment portability

Simple onboarding

GitHub Actions compatibility

---

Decision

Accepted

---

# ADR-014

## Deployment Strategy

Frontend

Vercel

Backend

Railway or Render

Database

Neon PostgreSQL

Storage

Cloudflare R2

Images

Cloudflare

---

Reason

Maximum free-tier utilization

GitHub Student compatibility

Minimal operational overhead

---

Future

Self-hostable Docker Compose deployment

Kubernetes should never become a requirement.

---

Decision

Accepted

---

# ADR-015

## Documentation Philosophy

Documentation is treated as production code.

Every feature

Every subsystem

Every API

Every architectural decision

must have corresponding documentation.

Documentation changes are reviewed alongside implementation.
