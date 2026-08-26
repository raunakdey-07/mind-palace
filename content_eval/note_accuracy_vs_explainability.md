---
title: "Note: Choosing Between Accuracy and Explainability"
date: 2024-07-05
tags: ["architecture", "philosophy", "note"]
document_type: "note"
status: Complete
summary: "When black-box gains are worth losing transparent reasoning — a portfolio-systems perspective"
---

# Accuracy vs Explainability

Personal systems tilt this tradeoff differently than production systems.

## The Production View

Explainability buys debugging ability, user trust, and regulatory compliance. Gains from opaque methods must be large to justify the loss.

## The Personal-System View

The operator and the user are the same person. Debuggability compounds — every future improvement depends on understanding current behavior. Opaque components impose a tax on *your own* future work.

## Applied

This is why Mind Palace keeps interpretable retrieval (visible scores, inspectable SQL) even though learned rankers might score higher, and why the reranker's cross-encoder scores are exposed in diagnostics rather than hidden inside the service.
