# Mind Palace
### AI-Native Research Operating System

**Version:** 0.1.0

**Status:** Draft

**Author:** Raunak Dey

---

# Table of Contents

1. Project Statement
2. Why This Project Exists
3. Problem Definition
4. Project Objectives
5. Project Scope
6. Design Principles
7. Constraints
8. High-Level System Overview
9. Repository Philosophy
10. Documentation Roadmap

---

# 1. Project Statement

Mind Palace is an AI-native Research Operating System designed to consolidate, organize, retrieve, and continuously evolve a researcher's accumulated knowledge.

Unlike traditional documentation systems that merely store documents, Mind Palace models knowledge as a connected graph of ideas, projects, experiments, codebases, research notes, publications, and personal experience.

The primary goal of the project is to remove the friction involved in documenting technical work while simultaneously increasing the long-term value of that documentation through semantic search, retrieval, reasoning, and automation.

Mind Palace is not a chatbot.

It is not a note-taking application.

It is not another personal portfolio.

Instead, it is the underlying operating system responsible for managing a researcher's technical knowledge throughout their career.

The public portfolio is only one interface into this system.

---

# 2. Why This Project Exists

Knowledge generated while working on technical projects is rarely preserved in a structured and reusable manner.

For most developers and researchers, information becomes scattered across multiple disconnected platforms:

- GitHub repositories
- Kaggle notebooks
- Markdown notes
- PDFs
- Research papers
- Blog posts
- Presentations
- Portfolio projects
- Course material
- Competition write-ups
- Experiment logs

Each source represents only one part of the overall learning process.

Current tooling treats these sources independently.

As a result,

- previous solutions become difficult to rediscover,
- experiments are repeated,
- lessons are forgotten,
- projects become isolated,
- documentation quickly becomes outdated.

Mind Palace exists to solve this problem.

Every artifact produced throughout the development lifecycle should become searchable, interconnected, and reusable.

---

# 3. Problem Definition

Documentation today is fundamentally document-centric.

Research, however, is knowledge-centric.

Documents describe knowledge.

They are not the knowledge itself.

For example,

A Kaggle notebook may contain

- feature engineering
- validation strategy
- modeling decisions
- lessons learned
- failed experiments

Months later, those ideas become buried inside notebooks that are difficult to search beyond keywords.

Mind Palace attempts to preserve ideas instead of documents.

Documents become one representation of knowledge.

The knowledge itself becomes queryable.

---

# 4. Project Objectives

The project has six primary objectives.

## Objective 1

Create a single source of truth for all technical work.

Every project, note, competition, experiment and publication should exist inside one ecosystem.

---

## Objective 2

Reduce documentation effort.

Writing documentation should never become a bottleneck.

The system should automatically generate metadata, summaries, embeddings and relationships wherever possible.

---

## Objective 3

Enable semantic retrieval.

The system should answer questions based on concepts rather than filenames or keywords.

Example

Instead of searching

"feature engineering"

the user should be able to ask

"What competitions involved feature leakage?"

---

## Objective 4

Improve long-term learning.

Past work should become easier to revisit than to recreate.

Knowledge accumulated over several years should remain accessible.

---

## Objective 5

Serve as the backend powering the public portfolio.

The portfolio should never contain manually duplicated content.

Instead,

Portfolio

Resume

Projects

Blogs

Research

Documentation

AI Assistant

should all consume the same underlying knowledge base.

---

## Objective 6

Act as a production-grade AI engineering project.

The project should intentionally demonstrate modern AI engineering practices including

- Retrieval-Augmented Generation
- Agentic Workflows
- Local LLM Deployment
- Embedding Pipelines
- Vector Search
- Hybrid Retrieval
- Tool Calling
- Prompt Engineering
- Observability
- Evaluation
- Distributed Background Processing
- CI/CD

The project itself is part of the portfolio.

Its architecture should therefore reflect production engineering standards.

---

# 5. Project Scope

Mind Palace intentionally focuses on technical knowledge.

The following content types are considered first-class citizens.

- Markdown
- MDX
- Jupyter Notebooks
- Source Code
- GitHub Repositories
- Kaggle Competitions
- Research Papers
- PDFs
- Technical Blogs
- Project Documentation
- Portfolio Pages
- Resume
- Experiment Logs

Support for additional formats should be added through modular ingestion pipelines rather than changing the core architecture.

---

# Out of Scope

Mind Palace is NOT

- a social network
- a CMS
- a replacement for Git
- a replacement for Obsidian
- another ChatGPT clone
- another Notion clone

The project exists specifically for technical knowledge management.

Any feature that does not contribute to this goal should be questioned.

---

# 6. Design Principles

Every architectural decision must satisfy the following principles.

## Markdown First

Markdown remains the canonical representation of human-authored knowledge.

Everything else should be derived automatically.

---

## Local First

The complete system must function without requiring paid APIs.

Cloud-hosted inference should remain optional.

The local deployment should always remain the reference implementation.

---

## AI Native

Artificial Intelligence should not be implemented as an optional feature.

The system should be designed assuming intelligent retrieval and reasoning are available from the beginning.

---

## Single Source of Truth

Knowledge should never exist in multiple manually synchronized locations.

Any duplicated information should be generated automatically.

---

## Modular

Every subsystem should be independently replaceable.

Replacing

- vector database
- embedding model
- reranker
- LLM
- frontend

should require minimal architectural changes.

---

## Observable

Every AI decision should be measurable.

Latency.

Retrieval quality.

Prompt usage.

Model performance.

Failure rate.

Everything should be observable.

---

## Explainable

Every AI response should be capable of answering

Why was this document retrieved?

Which documents contributed?

What confidence exists?

Which model produced the answer?

---

## Incremental

The system should provide value after every milestone.

No feature should require completion of the entire roadmap before becoming useful.

---

# 7. Constraints

The architecture intentionally embraces several practical constraints.

## Financial

The system must operate without mandatory paid APIs.

Open-source models should remain the primary inference providers.

---

## Hardware

The project should run on commodity consumer hardware.

GPU acceleration should improve performance but never become a requirement.

---

## Development

The project is expected to be maintained primarily by a single developer.

The architecture should therefore prioritize maintainability over unnecessary abstraction.

---

## Deployment

Every core service should support containerized deployment.

Development and production environments should remain as similar as possible.

---

## Data Ownership

All generated knowledge belongs to the user.

No mandatory cloud synchronization should exist.

Users should retain complete ownership over

- documents
- embeddings
- metadata
- vector indexes
- AI outputs

---

## Cost

The reference deployment should be possible using

- GitHub Student Pack
- GitHub Actions
- Cloudflare
- Vercel Hobby
- Railway credits (if available)
- Self-hosted Docker

No paid infrastructure should be assumed.

---

# 8. High-Level System Overview

Mind Palace consists of several independent but cooperating subsystems.

At a high level

External Knowledge Sources

↓

Content Engine

↓

Metadata Engine

↓

Knowledge Engine

↓

Embedding Engine

↓

Search Engine

↓

AI Layer

↓

Portfolio
API
Documentation
Agents

Each subsystem has one clearly defined responsibility.

Subsystem interactions should occur through well-defined interfaces rather than shared implementation logic.

Detailed architecture is intentionally deferred to the System Architecture document.

---

# 9. Repository Philosophy

The repository should be organized around domains rather than technologies.

For example

content/

instead of

markdown/

because future content may include

PDFs

videos

audio

repositories

without changing the conceptual organization.

Likewise,

ai/

should contain AI-specific capabilities regardless of the underlying frameworks.

Repository organization should describe intent rather than implementation.

---

# 10. Documentation Roadmap

This document intentionally avoids implementation details.

Its purpose is to establish the project's direction and engineering philosophy.

Subsequent documents will progressively refine individual subsystems.

The expected documentation sequence is

01_SYSTEM_ARCHITECTURE.md

Overall software architecture

---

02_DATABASE_ARCHITECTURE.md

PostgreSQL schema

pgvector

metadata

relationships

migrations

---

03_AI_PIPELINE.md

Embedding generation

retrieval

reranking

LLM orchestration

evaluation

---

04_AGENT_SYSTEM.md

Agent framework

tool execution

memory

planning

---

05_CONTENT_ENGINE.md

Markdown ingestion

GitHub synchronization

Kaggle synchronization

frontmatter

MDX

---

06_PORTFOLIO_INTEGRATION.md

Next.js

search

timeline

AI interface

---

07_DEPLOYMENT.md

Docker

GitHub Actions

CI/CD

hosting

monitoring

---

This documentation should evolve alongside the implementation.

Whenever implementation diverges from documentation, either the implementation or the documentation must be updated immediately.

The documentation is considered part of the codebase rather than supplementary material.
