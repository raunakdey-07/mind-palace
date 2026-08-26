---
title: "Paper Notes: Scaling Laws for Neural Language Models"
date: 2024-02-15
tags: ["paper", "llm", "deep-learning"]
document_type: "paper"
status: Read
summary: "Kaplan et al. 2020 — power-law relationships governing model, data, and compute tradeoffs"
---

# Paper: Scaling Laws (Kaplan et al., 2020)

Why the GPT-3 result was predictable before it happened.

## Core Idea

Cross-entropy loss follows **power laws** in model size, dataset size, and training compute — over many orders of magnitude.

## Key Findings

- Larger models are more sample-efficient: reach a given loss with less data
- Under a fixed compute budget, train bigger models shorter rather than smaller longer
- Architecture details matter far less than scale within the transformer family

## Relevance

Frames every should-we-upgrade decision: switching embedding models is an architecture tweak whose effect will be dwarfed by corpus growth. Data scale is the lever that matters most.
