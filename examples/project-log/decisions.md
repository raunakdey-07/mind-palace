---
title: Project decision log
date: 2026-10-08
description: >-
  Decisions this project actually made, in the words they were made. Read by
  examples/project-log/project_log.py, which is also the format
  `mindpalace remember --file` accepts, so a project can keep its decisions here
  next to its code.
claims:
  - group: retrieval
    key: retrieval.no-cross-encoder
    value: rejected, not deferred
    claim: We do not use a CrossEncoder reranker, because the frozen benchmark held at 1.000 without one and adding it would make every answer depend on a second model.
    evidence: We do not use a CrossEncoder reranker, because the frozen benchmark held at 1.000 without one
  - group: retrieval
    key: retrieval.no-vector-database
    value: rejected
    claim: We rejected Qdrant, Weaviate and Milvus as a dedicated vector store, because the authoritative projection has to be loaded anyway to decide what is true; pgvector in the existing database remains acceptable.
    evidence: We rejected Qdrant, Weaviate and Milvus as a dedicated vector store
  - group: retrieval
    key: retrieval.no-rrf
    value: rejected
    claim: Reciprocal rank fusion was measured and rejected, because it blended a lexical and a semantic result into something that was neither and scored no better.
    evidence: Reciprocal rank fusion was measured and rejected, because it blended
  - group: retrieval
    key: retrieval.acceptance-gate
    value: kept, and tightened in v0.9.0
    claim: The acceptance gate tests overlap against full claim terms rather than terms reduced by the set shared with every candidate, so a subject stays answerable once a second claim shares it.
    evidence: The acceptance gate tests overlap against full claim terms rather than
  - group: contracts
    key: contracts.receipt-version-1
    value: frozen
    claim: The receipt schema stays at version 1 until a real deployment asks for v2, and every receipt written under v0.8.0 must remain verifiable.
    evidence: The receipt schema stays at version 1 until a real deployment asks for v2
  - group: contracts
    key: contracts.memory-pack-version-1
    value: frozen
    claim: Memory Pack stays at version 1, because it is both the export format and the rebuild contract, so changing it breaks reproducibility.
    evidence: Memory Pack stays at version 1, because it is both the export format
  - group: contracts
    key: contracts.verified-is-not-true
    value: standing rule
    claim: A VERIFIED receipt means the artifact still represents the recorded state; it never means the original claim was factually correct, and authenticity additionally requires a trust anchor the user pinned.
    evidence: A VERIFIED receipt means the artifact still represents the recorded state;
  - group: runtime
    key: runtime.local-warm-runtime
    value: adopted in v0.9.0
    claim: A local runtime keeps the embedding model and the database pool warm, so a one-shot CLI recall stopped paying a 5.3 second model import on every command.
    evidence: A local runtime keeps the embedding model and the database pool warm
  - group: runtime
    key: runtime.no-lexical-fast-path
    value: rejected on measurement
    claim: A lexical fast path was measured and rejected, because it abstained on a question the semantic ranking answered, which would have made NO_RELEVANT_MEMORY depend on whether the model happened to be cached on that machine.
    evidence: A lexical fast path was measured and rejected, because it abstained
  - group: runtime
    key: runtime.writes-do-not-need-a-model
    value: adopted in v0.9.0
    claim: remember passes require_embeddings=False, so recording a first memory never imports the embedding stack at all.
    evidence: remember passes require_embeddings=False, so recording a first memory
  - group: scope
    key: scope.no-signatures
    value: deferred
    claim: Signed receipts are deferred until a deployment asks for them, because a digest the user pinned covers the threat model of a receipt handed to one person.
    evidence: Signed receipts are deferred until a deployment asks for them, because
  - group: scope
    key: scope.no-automatic-extraction
    value: deliberate
    claim: Every claim is authored, because a model-generated claim still has to be read before it can be trusted.
    evidence: Every claim is authored, because a model-generated claim still has to be read
  - group: scope
    key: scope.authentication-not-built
    value: documented contract only
    claim: Authentication is a written contract and not implemented code, because local development needs no credential and requiring one would make the first five minutes a configuration exercise.
    evidence: Authentication is a written contract and not implemented code, because
---

# Project decision log

Real decisions from this repository's history. Not invented for a demo: each one is
a choice that was actually made, sometimes after measuring, and that a contributor
would otherwise have to reconstruct from `CHANGELOG.md`, `docs/STATUS.md` and
`docs/research/`.

The point of putting them in memory is that they become answerable *with the reason
and the evidence attached*, which a Markdown file alone does not give you.
